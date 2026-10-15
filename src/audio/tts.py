"""Per-sentence text-to-speech with Kokoro (open weights, Apache-2.0) via ONNX Runtime.

Sentences are synthesised one at a time so we know each sentence's exact duration; that drives
caption word timing and scene alignment. Each channel gets a distinct synthetic voice by blending
Kokoro voice styles (no real person's voice is imitated). Results are cached on disk keyed by
(model checksum, voice, speed, text).

A deterministic ``SyntheticBackend`` (speech-shaped tones, clearly not a voice) exists for CI and
unit tests where the 330 MB model is absent.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import re
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field, field_validator
from scipy.io import wavfile

from src.audio.captions import spoken_text
from src.common.log import get_logger, log_event
from src.common.paths import DATA_DIR, MODELS_DIR, REPO_ROOT, ensure_dir

log = get_logger("src.audio.tts")

KOKORO_MODEL = MODELS_DIR / "kokoro" / "kokoro-v1.0.onnx"
KOKORO_VOICES = MODELS_DIR / "kokoro" / "voices-v1.0.bin"
CACHE_DIR = DATA_DIR / "cache" / "tts"
DLOPEN_TMP = Path("/var/tmp/dlopen")  # noqa: S108  # nosec B108
TTS_SR = 24000
_VOICE_RE = re.compile(r"^[a-z]{2}_[a-z]{2,12}$")


class VoiceSpec(BaseModel):
    """A channel voice: weighted blend of Kokoro voices, speed and language."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    blend: tuple[tuple[str, float], ...] = Field(min_length=1, max_length=4)
    speed: float = Field(1.0, ge=0.7, le=1.4)
    lang: str = Field("en-us", pattern=r"^[a-z]{2}(-[a-z]{2})?$")

    @field_validator("blend")
    @classmethod
    def _blend(cls, v: tuple[tuple[str, float], ...]) -> tuple[tuple[str, float], ...]:
        for name, w in v:
            if not _VOICE_RE.fullmatch(name) or not 0 < w <= 1:
                raise ValueError("invalid voice blend entry")
        return v

    def key(self) -> str:
        """Stable identifier used in cache keys."""
        return ";".join(f"{n}:{w:.3f}" for n, w in self.blend) + f"|{self.speed:.3f}|{self.lang}"


class Backend(Protocol):
    """Speech synthesis backend."""

    name: str

    def synth(self, text: str, voice: VoiceSpec) -> npt.NDArray[np.float32]:
        """Return mono float32 samples at ``TTS_SR``."""
        ...


@contextlib.contextmanager
def _dlopen_tmp() -> Iterator[None]:
    """Point tempfile at the exec-allowed tmpfs while espeak-ng is copied and dlopen'ed."""
    old = tempfile.tempdir
    if DLOPEN_TMP.is_dir():
        tempfile.tempdir = str(DLOPEN_TMP)
    try:
        yield
    finally:
        tempfile.tempdir = old


class KokoroBackend:
    """Kokoro-82M v1.0 through kokoro-onnx."""

    name = "kokoro-v1.0"

    def __init__(self, model: Path = KOKORO_MODEL, voices: Path = KOKORO_VOICES) -> None:
        from kokoro_onnx import Kokoro

        if not model.is_file() or not voices.is_file():
            raise FileNotFoundError("Kokoro weights missing; run `bash scripts/task.sh fetch-assets`")
        self._k = Kokoro(str(model), str(voices))
        self._styles: dict[str, npt.NDArray[np.float32]] = {}
        with _dlopen_tmp():  # first phonemize loads libespeak-ng
            self._k.create("Ready.", voice="af_heart", speed=1.0, lang="en-us")

    def _style(self, voice: VoiceSpec) -> npt.NDArray[np.float32]:
        key = voice.key()
        st = self._styles.get(key)
        if st is None:
            total = sum(w for _, w in voice.blend)
            st = sum(
                (self._k.get_voice_style(n).astype(np.float32) * (w / total) for n, w in voice.blend),
                np.zeros((510, 1, 256), np.float32),
            )
            self._styles[key] = st
        return st

    def synth(self, text: str, voice: VoiceSpec) -> npt.NDArray[np.float32]:
        """Synthesize one sentence."""
        # phonemizer loads a fresh libespeak-ng copy per new language, so every call is scoped.
        with _dlopen_tmp():
            samples, sr = self._k.create(text, voice=self._style(voice), speed=voice.speed, lang=voice.lang, trim=True)
        if sr != TTS_SR:
            raise RuntimeError(f"unexpected Kokoro sample rate {sr}")
        return np.asarray(samples, dtype=np.float32)


class SyntheticBackend:
    """Deterministic speech-shaped placeholder for tests/CI (NOT a voice; never used for output)."""

    name = "synthetic"

    def synth(self, text: str, voice: VoiceSpec) -> npt.NDArray[np.float32]:
        """Produce syllable-paced voiced tones with gaps at commas."""
        from src.audio.captions import syllables

        rng = np.random.default_rng(int(hashlib.sha256(text.encode()).hexdigest()[:8], 16))
        chunks: list[npt.NDArray[np.float32]] = []
        for word in text.split():
            n = max(1, syllables(word))
            dur = 0.17 * n / voice.speed
            t = np.arange(int(dur * TTS_SR)) / TTS_SR
            f0 = 110 + 30 * rng.random()
            env = np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 0.5
            tone = (0.3 * np.sin(2 * np.pi * f0 * t) + 0.15 * np.sin(2 * np.pi * 2 * f0 * t)) * env
            chunks.append(tone.astype(np.float32))
            gap = 0.2 if word.endswith((",", ";", "—")) else 0.04
            chunks.append(np.zeros(int(gap * TTS_SR), np.float32))
        return np.concatenate(chunks) if chunks else np.zeros(1, np.float32)


@functools.cache
def _model_digest() -> str:
    sums = REPO_ROOT / "security" / "checksums.txt"
    if sums.is_file():
        for line in sums.read_text(encoding="utf-8").splitlines():
            if line.endswith("kokoro-v1.0.onnx"):
                return line.split()[0][:16]
    return "unknown"


@dataclass(frozen=True)
class SentenceAudio:
    """One synthesized sentence."""

    text: str
    samples: npt.NDArray[np.float32]  # mono, TTS_SR
    pause_after: float  # seconds of silence that follow it

    @property
    def duration(self) -> float:
        """Speech duration in seconds."""
        return len(self.samples) / TTS_SR


_ABBREV = (
    "Mr.",
    "Mrs.",
    "Ms.",
    "Dr.",
    "St.",
    "vs.",
    "e.g.",
    "i.e.",
    "U.S.",
    "U.K.",
    "approx.",
    "No.",
)


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences, respecting common abbreviations and decimals."""
    protected = text
    for i, ab in enumerate(_ABBREV):
        protected = protected.replace(ab, f"\x00{i}\x00")
    parts = re.split(r"(?<=[.!?…])[\"”’)]?\s+(?=[\"“‘(]?[A-Z0-9])", protected.strip())
    out = []
    for part in parts:
        restored = part
        for i, ab in enumerate(_ABBREV):
            restored = restored.replace(f"\x00{i}\x00", ab)
        if restored.strip():
            out.append(restored.strip())
    return out


def pause_after(sentence: str, rng: np.random.Generator, *, segment_end: bool = False) -> float:
    """Natural pause after a sentence: depends on punctuation, length and position, with jitter."""
    s = sentence.rstrip("\"'”’) ")
    words = len(sentence.split())
    if s.endswith(("…", "...")):
        base = 0.62
    elif s.endswith("?"):
        base = 0.44
    elif s.endswith("!"):
        base = 0.36
    elif s.endswith(":"):
        base = 0.30
    elif s.endswith("—"):
        base = 0.22
    else:
        base = 0.34 if words > 5 else 0.27  # punchy short sentences keep momentum
    if segment_end:
        base += 0.22
    return round(base * float(rng.uniform(0.88, 1.12)), 3)


class TTS:
    """Caching per-sentence synthesizer."""

    def __init__(self, backend: Backend | None = None, cache_dir: Path = CACHE_DIR) -> None:
        self.backend: Backend = backend or KokoroBackend()
        self.cache_dir = cache_dir

    def _cache_path(self, text: str, voice: VoiceSpec) -> Path:
        h = hashlib.sha256(f"{self.backend.name}|{_model_digest()}|{voice.key()}|{text}".encode()).hexdigest()[:32]
        return self.cache_dir / f"{h}.wav"

    def sentence(self, text: str, voice: VoiceSpec) -> npt.NDArray[np.float32]:
        """Synthesize (or load from cache) one sentence."""
        path = self._cache_path(text, voice)
        if path.is_file():
            sr, data = wavfile.read(path)
            if sr == TTS_SR:
                return np.asarray(data, dtype=np.float32)
        samples = self.backend.synth(text, voice)
        try:
            ensure_dir(self.cache_dir)
            wavfile.write(path, TTS_SR, samples.astype(np.float32))
        except OSError:
            pass  # read-only environments just skip caching
        return samples

    def narrate(self, segments: Sequence[Sequence[str]], voice: VoiceSpec, seed: int = 0) -> list[SentenceAudio]:
        """Synthesize every sentence of every segment, with natural pauses.

        Args:
            segments: list of segments, each a list of sentences.
            voice: Channel voice.
            seed: Seed for pause jitter (deterministic).
        """
        rng = np.random.default_rng(seed)
        out: list[SentenceAudio] = []
        for si, seg in enumerate(segments):
            for k, sent in enumerate(seg):
                samples = self.sentence(spoken_text(sent), voice)
                last = k == len(seg) - 1 and si < len(segments) - 1
                out.append(SentenceAudio(sent, samples, pause_after(sent, rng, segment_end=last)))
        log_event(
            log,
            "narration synthesized",
            backend=self.backend.name,
            sentences=len(out),
            seconds=round(sum(s.duration + s.pause_after for s in out), 2),
        )
        return out
