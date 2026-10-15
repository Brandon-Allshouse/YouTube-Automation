"""Text-to-speech with Kokoro (open weights, Apache-2.0) via ONNX Runtime.

Each script segment is synthesised in one pass, so intonation carries across its sentences
instead of resetting at every full stop (sentence-by-sentence synthesis is what made early
narration sound robotic). The timestamped export of the same Kokoro weights reports how long
every phoneme lasts; we group those into words, which gives exact word timings for captions and
lets us cut the segment into sentences and set the pauses between them ourselves. Without that
export, sentences are synthesised one at a time and captions fall back to energy alignment.

Each channel gets a distinct synthetic voice from Kokoro voice styles (no real person's voice is
imitated). Results are cached on disk keyed by (backend, model checksum, voice, text).

A deterministic ``SyntheticBackend`` (speech-shaped tones, clearly not a voice) exists for CI and
unit tests where the 330 MB model is absent.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import re
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field, field_validator
from scipy.io import wavfile

from src.audio.captions import WordTiming, parse_markup, spoken_text
from src.common.log import get_logger, log_event
from src.common.paths import DATA_DIR, MODELS_DIR, REPO_ROOT, ensure_dir

log = get_logger("src.audio.tts")

KOKORO_MODEL = MODELS_DIR / "kokoro" / "kokoro-v1.0.onnx"
# Same weights, exported with a per-token `durations` output (scripts/fetch_assets.py, ADR-031).
KOKORO_TIMED_MODEL = MODELS_DIR / "kokoro" / "kokoro-v1.0-timestamped.onnx"
KOKORO_VOICES = MODELS_DIR / "kokoro" / "voices-v1.0.bin"
CACHE_DIR = DATA_DIR / "cache" / "tts"
DLOPEN_TMP = Path("/var/tmp/dlopen")  # noqa: S108  # nosec B108
TTS_SR = 24000
_VOICE_RE = re.compile(r"^[a-z]{2}_[a-z]{2,12}$")
# Phoneme-string characters that are punctuation, not sound.
_PUNCT = frozenset(';:,.!?—…"()“”«»¡¿')
WordSpans = list[tuple[float, float]]


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


@runtime_checkable
class TimedBackend(Protocol):
    """A backend that can voice several sentences in one pass and report word timings."""

    name: str

    def synth(self, text: str, voice: VoiceSpec) -> npt.NDArray[np.float32]:
        """Return mono float32 samples at ``TTS_SR``."""
        ...

    def synth_words(self, words: Sequence[str], voice: VoiceSpec) -> tuple[npt.NDArray[np.float32], WordSpans] | None:
        """Voice ``words`` as one passage; return samples and a (start, end) span per word.

        Returns None when the words cannot be matched to the model's timings; callers then fall
        back to per-sentence synthesis.
        """
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


_STRESS = frozenset("ˈˌ")
Phone = tuple[str, float, float]  # (phoneme, start, end)


def _sounds(phonemes: str) -> str:
    """Phoneme string without stress marks, punctuation or spaces."""
    return "".join(c for c in phonemes if c not in _PUNCT and c not in _STRESS and not c.isspace())


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# (words, groups) a step of the alignment may join: espeak sometimes fuses short words in
# context ("from the" -> "fɹʌmðɪ") or splits one ("sixty-nine" -> two groups).
_STEPS = ((1, 1), (2, 1), (3, 1), (1, 2), (1, 3), (1, 0))


def align_phoneme_words(word_sounds: Sequence[str], groups: Sequence[Sequence[Phone]]) -> WordSpans | None:
    """Match words (their isolated pronunciation) to the timed phoneme groups of a passage.

    Dynamic programming over joins/splits, scored by edit distance between the pronunciations, so
    context changes in espeak's output don't break word timing. Fused groups are divided at
    phoneme boundaries in proportion to each word's length.

    Args:
        word_sounds: Per word, its phonemes from ``_sounds`` (empty for a word with no sound).
        groups: Per space-separated phoneme group of the passage, its timed phonemes (no stress or
            punctuation).

    Returns:
        A (start, end) span per word, or None if the passage can't be matched confidently.
    """
    n, m = len(word_sounds), len(groups)
    gs = ["".join(p for p, _, _ in g) for g in groups]
    inf = float("inf")
    cost = [[inf] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple[int, int] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            if cost[i][j] == inf:
                continue
            for a, b in _STEPS:
                if i + a > n or j + b > m:
                    continue
                w = "".join(word_sounds[i : i + a])
                if b == 0 and w:
                    continue  # only a soundless word may take no group
                g = "".join(gs[j : j + b])
                step = _edit_distance(w, g) / max(len(w), len(g), 1) + 0.25 * max(a + b - 2, 0)
                if cost[i][j] + step < cost[i + a][j + b]:
                    cost[i + a][j + b] = cost[i][j] + step
                    back[i + a][j + b] = (a, b)
    if cost[n][m] == inf or cost[n][m] / max(n, 1) > 0.5:
        return None
    steps: list[tuple[int, int, int, int]] = []
    i, j = n, m
    while i or j:
        ab = back[i][j]
        if ab is None:
            return None
        a, b = ab
        i, j = i - a, j - b
        steps.append((i, a, j, b))
    spans: WordSpans = []
    for i, a, j, b in reversed(steps):
        if b == 0:
            at = spans[-1][1] if spans else 0.0
            spans.append((at, at))
        elif a == 1:
            spans.append((groups[j][0][1], groups[j + b - 1][-1][2]))
        else:
            phones = groups[j]
            if len(phones) < a:
                return None
            lengths = [max(len(word_sounds[i + k]), 1) for k in range(a)]
            total, acc, cut = sum(lengths), 0, 0
            for k in range(a):
                acc += lengths[k]
                nxt = (
                    len(phones) if k == a - 1 else min(max(round(len(phones) * acc / total), cut + 1), len(phones) - 1)
                )
                spans.append((phones[cut][1], phones[nxt - 1][2]))
                cut = nxt
    return spans


class KokoroBackend:
    """Kokoro-82M v1.0 through kokoro-onnx (timestamped export preferred)."""

    def __init__(self, model: Path | None = None, voices: Path = KOKORO_VOICES) -> None:
        from kokoro_onnx import Kokoro

        if model is None:
            model = KOKORO_TIMED_MODEL if KOKORO_TIMED_MODEL.is_file() else KOKORO_MODEL
        if not model.is_file() or not voices.is_file():
            raise FileNotFoundError("Kokoro weights missing; run `bash scripts/task.sh fetch-assets`")
        self._k = Kokoro(str(model), str(voices))
        # kokoro-onnx looks for an output named "duration"; this export calls it "durations"
        outputs = {o.name for o in self._k.sess.get_outputs()}
        self.timed = bool(outputs & {"duration", "durations"})
        self._k.has_timings = self.timed
        self.model_file = model.name
        self.name = "kokoro-v1.0-timed" if self.timed else "kokoro-v1.0"
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

    def synth_words(self, words: Sequence[str], voice: VoiceSpec) -> tuple[npt.NDArray[np.float32], WordSpans] | None:
        """Voice a passage in one continuous pass and time every word from phoneme durations."""
        if not self.timed or not words:
            return None
        with _dlopen_tmp():
            # Pauses are ours to set (pause_after), so kokoro-onnx must not insert any.
            audio, sr, timings = self._k.create_timed(
                " ".join(words),
                voice=self._style(voice),
                speed=voice.speed,
                lang=voice.lang,
                sentence_pause=0.0,
                clause_pause=0.0,
                continuous=True,
            )
            word_sounds = [_sounds(self._k.tokenizer.phonemize(w, voice.lang)) for w in words]
        if sr != TTS_SR:
            raise RuntimeError(f"unexpected Kokoro sample rate {sr}")
        groups: list[list[Phone]] = []
        current: list[Phone] = []
        for t in [*timings, None]:
            if t is None or t.phoneme.isspace():
                if current:
                    groups.append(current)
                current = []
            elif t.phoneme not in _PUNCT and t.phoneme not in _STRESS:
                current.append((t.phoneme, t.start, t.end))
        spans = align_phoneme_words(word_sounds, groups)
        if spans is None:
            log_event(log, "word timing mismatch", words=len(words), groups=len(groups))
            return None
        return np.asarray(audio, dtype=np.float32), spans


class SyntheticBackend:
    """Deterministic speech-shaped placeholder for tests/CI (NOT a voice; never used for output)."""

    name = "synthetic"

    def _render(
        self, words: Sequence[str], voice: VoiceSpec, seed_text: str
    ) -> tuple[npt.NDArray[np.float32], WordSpans]:
        from src.audio.captions import syllables

        rng = np.random.default_rng(int(hashlib.sha256(seed_text.encode()).hexdigest()[:8], 16))
        chunks: list[npt.NDArray[np.float32]] = []
        spans: WordSpans = []
        pos = 0
        for word in words:
            n = max(1, syllables(word))
            dur = 0.17 * n / voice.speed
            t = np.arange(int(dur * TTS_SR)) / TTS_SR
            f0 = 110 + 30 * rng.random()
            env = np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 0.5
            tone = (0.3 * np.sin(2 * np.pi * f0 * t) + 0.15 * np.sin(2 * np.pi * 2 * f0 * t)) * env
            chunks.append(tone.astype(np.float32))
            spans.append((pos / TTS_SR, (pos + len(tone)) / TTS_SR))
            gap = int((0.2 if word.endswith((",", ";", "—", ".", "?", "!")) else 0.04) * TTS_SR)
            chunks.append(np.zeros(gap, np.float32))
            pos += len(tone) + gap
        return (np.concatenate(chunks) if chunks else np.zeros(1, np.float32)), spans

    def synth(self, text: str, voice: VoiceSpec) -> npt.NDArray[np.float32]:
        """Produce syllable-paced voiced tones with gaps at commas."""
        return self._render(text.split(), voice, text)[0]

    def synth_words(self, words: Sequence[str], voice: VoiceSpec) -> tuple[npt.NDArray[np.float32], WordSpans] | None:
        """Voice a passage and report exact word spans (the tones are placed by construction)."""
        return self._render(words, voice, " ".join(words)) if words else None


@functools.cache
def _model_digest(model_file: str = "kokoro-v1.0.onnx") -> str:
    sums = REPO_ROOT / "security" / "checksums.txt"
    if sums.is_file():
        for line in sums.read_text(encoding="utf-8").splitlines():
            if line.endswith(f"/{model_file}"):
                return line.split()[0][:16]
    return "unknown"


@dataclass(frozen=True)
class SentenceAudio:
    """One synthesized sentence."""

    text: str
    samples: npt.NDArray[np.float32]  # mono, TTS_SR
    pause_after: float  # seconds of silence that follow it
    words: tuple[WordTiming, ...] | None = None  # caption tokens timed by the model, relative to the clip

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


_FRAME = int(0.01 * TTS_SR)


def _release_end(samples: npt.NDArray[np.float32], end: float, limit: float) -> float:
    """Where the sound after ``end`` has decayed 40 dB under the passage peak, capped at ``limit``."""
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    floor = peak * 10 ** (-40 / 20)
    i = int(end * TTS_SR)
    stop = min(int(limit * TTS_SR), len(samples))
    while i + _FRAME <= stop:
        if float(np.sqrt(np.mean(samples[i : i + _FRAME] ** 2))) < floor:
            return i / TTS_SR
        i += _FRAME
    return stop / TTS_SR


def _fade(clip: npt.NDArray[np.float32], ms: float = 4.0) -> npt.NDArray[np.float32]:
    n = min(int(ms / 1000 * TTS_SR), len(clip) // 2)
    if n > 0:
        ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
        clip = clip.copy()
        clip[:n] *= ramp
        clip[-n:] *= ramp[::-1]
    return clip


def cut_sentences(
    sentences: Sequence[str],
    samples: npt.NDArray[np.float32],
    spans: WordSpans,
    pauses: Sequence[float],
) -> list[SentenceAudio]:
    """Split one voiced passage into sentence clips at the gaps between sentences.

    The model's own gap is dropped (each clip keeps a short lead-in and its release tail) so our
    pause model decides how long the listener waits. Caption tokens get the model's word times.

    Args:
        sentences: Script sentences (with markup) in passage order.
        samples: The passage audio at ``TTS_SR``.
        spans: (start, end) seconds of every spoken word in the passage.
        pauses: Pause after each sentence.

    Returns:
        One ``SentenceAudio`` per sentence, with ``words`` set.
    """
    groups: list[WordSpans] = []
    i = 0
    for s in sentences:
        n = len(spoken_text(s).split())
        groups.append(spans[i : i + n])
        i += n
    total = len(samples) / TTS_SR
    out: list[SentenceAudio] = []
    for k, (sent, ws) in enumerate(zip(sentences, groups, strict=True)):
        prev_end = groups[k - 1][-1][1] if k else 0.0
        next_start = groups[k + 1][0][0] if k + 1 < len(groups) else total
        a = max((prev_end + ws[0][0]) / 2, ws[0][0] - 0.06, 0.0)
        b = _release_end(samples, ws[-1][1], min(next_start - 0.02, ws[-1][1] + 0.3))
        clip = _fade(samples[int(a * TTS_SR) : int(b * TTS_SR)])
        timed: list[WordTiming] = []
        j = 0
        for tok in parse_markup(sent):
            first, last = ws[j], ws[j + len(tok.spoken) - 1]
            timed.append(WordTiming(tok.display, round(first[0] - a, 3), round(last[1] - a, 3), tok.emph))
            j += len(tok.spoken)
        out.append(SentenceAudio(sent, clip, pauses[k], tuple(timed)))
    return out


class TTS:
    """Caching synthesizer: whole segments when the backend reports timings, else per sentence."""

    def __init__(self, backend: Backend | None = None, cache_dir: Path = CACHE_DIR) -> None:
        self.backend: Backend = backend or KokoroBackend()
        self.cache_dir = cache_dir

    def _cache_path(self, text: str, voice: VoiceSpec, suffix: str = ".wav") -> Path:
        model_file = getattr(self.backend, "model_file", "kokoro-v1.0.onnx")
        key = f"{self.backend.name}|{_model_digest(model_file)}|{voice.key()}|{text}"
        return self.cache_dir / f"{hashlib.sha256(key.encode()).hexdigest()[:32]}{suffix}"

    def passage(self, sentences: Sequence[str], voice: VoiceSpec) -> tuple[npt.NDArray[np.float32], WordSpans] | None:
        """Synthesize (or load from cache) several spoken sentences in one pass, with word spans.

        Returns None if the backend has no timings or they could not be matched to the words.
        """
        if not isinstance(self.backend, TimedBackend):
            return None
        text = "\n".join(sentences)
        wav, meta = self._cache_path(text, voice), self._cache_path(text, voice, ".json")
        if wav.is_file() and meta.is_file():
            try:
                sr, data = wavfile.read(wav)
                spans = [(float(s), float(e)) for s, e in json.loads(meta.read_text(encoding="utf-8"))]
            except (OSError, ValueError, TypeError):
                sr, spans = 0, []  # unreadable cache entry: synthesize again
            if sr == TTS_SR and len(spans) == len(" ".join(sentences).split()):
                return np.asarray(data, dtype=np.float32), spans
        res = self.backend.synth_words(" ".join(sentences).split(), voice)
        if res is None:
            return None
        try:
            ensure_dir(self.cache_dir)
            wavfile.write(wav, TTS_SR, res[0].astype(np.float32))
            meta.write_text(json.dumps([[round(s, 5), round(e, 5)] for s, e in res[1]]), encoding="utf-8")
        except OSError:
            pass  # read-only environments just skip caching
        return res

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
        passages = 0
        for si, seg in enumerate(segments):
            pauses = [
                pause_after(sent, rng, segment_end=k == len(seg) - 1 and si < len(segments) - 1)
                for k, sent in enumerate(seg)
            ]
            res = self.passage([spoken_text(s) for s in seg], voice) if seg else None
            if res is not None:
                out.extend(cut_sentences(seg, res[0], res[1], pauses))
                passages += 1
                continue
            for sent, pause in zip(seg, pauses, strict=True):
                out.append(SentenceAudio(sent, self.sentence(spoken_text(sent), voice), pause))
        log_event(
            log,
            "narration synthesized",
            backend=self.backend.name,
            passages=passages,
            sentences=len(out),
            seconds=round(sum(s.duration + s.pause_after for s in out), 2),
        )
        return out
