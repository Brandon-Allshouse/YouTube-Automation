"""Mixer: voice chain, narration layout, sidechain ducking, loudness + true-peak mastering.

Voice chain (our DSP): 80 Hz high-pass → low-mid cleanup → presence lift → air shelf → de-esser →
soft-knee compressor. Music is set relative to the voice, then ducked under speech with an
envelope follower (fast attack, slow release) so it breathes back up in pauses. The master is
gain-normalised to the target integrated loudness (BS.1770, default -14 LUFS) and passed through a
lookahead true-peak limiter (ceiling below -1 dBTP), iterating once to land precisely.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import numpy.typing as npt
from scipy.io import wavfile

from src.audio import dsp
from src.audio.captions import WordTiming, align_sentence
from src.audio.tts import TTS_SR, SentenceAudio

SR = dsp.SR


def voice_chain(x: npt.ArrayLike, sr: int = SR) -> npt.NDArray[np.float64]:
    """Broadcast-style voice processing on mono audio."""
    y = dsp.eq(x, "highpass", 80.0, sr, 0.707)
    y = dsp.eq(y, "peak", 260.0, sr, 1.1, -2.0)  # boxiness
    y = dsp.eq(y, "peak", 3200.0, sr, 0.9, 2.5)  # presence / intelligibility
    y = dsp.eq(y, "highshelf", 10000.0, sr, 0.707, 1.5)  # air
    y = dsp.deess(y, sr, threshold_db=-32.0, max_cut_db=7.0)
    y = dsp.compress(y, sr, threshold_db=-24.0, ratio=3.0, attack=0.005, release=0.11, knee_db=8.0, makeup_db=4.0)
    return np.asarray(y[:, 0], dtype=np.float64)


@dataclass
class Narration:
    """Voice laid out on the timeline."""

    track: npt.NDArray[np.float64]  # mono at SR
    sentences: list[tuple[str, float, float]]  # (text, start, end) seconds
    words: list[WordTiming]
    duration: float


def layout_narration(sentences: Sequence[SentenceAudio], *, lead_in: float = 0.25, tail: float = 1.2) -> Narration:
    """Place sentences back to back with their pauses; compute global word timings."""
    total = lead_in + sum(s.duration + s.pause_after for s in sentences) + tail
    track = np.zeros(int(total * SR) + SR)
    t = lead_in
    spans: list[tuple[str, float, float]] = []
    words: list[WordTiming] = []
    for s in sentences:
        clip = dsp.resample(s.samples, TTS_SR, SR)
        i0 = round(t * SR)
        track[i0 : i0 + len(clip)] += clip
        for w in align_sentence(s.text, s.samples, TTS_SR):
            words.append(WordTiming(w.text, round(t + w.start, 3), round(t + w.end, 3), w.emph))
        spans.append((s.text, round(t, 3), round(t + s.duration, 3)))
        t += s.duration + s.pause_after
    end = t - (sentences[-1].pause_after if sentences else 0.0) + tail
    return Narration(track[: int(end * SR)], spans, words, round(end, 3))


def duck(music: npt.ArrayLike, voice: npt.ArrayLike, sr: int = SR, depth_db: float = 9.0) -> npt.NDArray[np.float64]:
    """Sidechain ducking: reduce music by up to ``depth_db`` while the voice is active."""
    m = dsp.as_2d(music)
    env = dsp.envelope(voice, sr, attack=0.03, release=0.45)
    if env.size < m.shape[0]:
        env = np.pad(env, (0, m.shape[0] - env.size))
    env = env[: m.shape[0]]
    ref = np.percentile(env[env > 1e-4], 90) if np.any(env > 1e-4) else 1.0
    amount = np.clip(env / max(ref, 1e-6), 0, 1) ** 0.7
    gain = 10 ** (-depth_db * amount / 20)
    return m * gain[:, None]


@dataclass
class MixReport:
    """Measured result of a mix."""

    lufs: float
    true_peak_dbtp: float
    voice_lufs: float
    music_lufs: float
    duration: float
    notes: list[str] = field(default_factory=list)


def master(
    voice: npt.NDArray[np.float64],
    music: npt.NDArray[np.float64] | None,
    *,
    sr: int = SR,
    target_lufs: float = -14.0,
    ceiling_dbtp: float = -1.3,
    music_rel_db: float = -12.0,
    duck_db: float = 8.0,
) -> tuple[npt.NDArray[np.float64], MixReport]:
    """Process voice, balance + duck music, normalise loudness and limit peaks. Returns stereo."""
    v = voice_chain(voice, sr)
    n = len(v)
    v_lufs = dsp.integrated_lufs(v, sr)
    stereo = np.stack([v, v], axis=1)
    m_lufs = float("-inf")
    if music is not None:
        m = dsp.as_2d(music)[:n]
        if m.shape[0] < n:
            m = np.pad(m, ((0, n - m.shape[0]), (0, 0)))
        raw_m = dsp.integrated_lufs(m, sr)
        if np.isfinite(raw_m) and np.isfinite(v_lufs):
            m = m * dsp.db_to_gain(v_lufs + music_rel_db - raw_m)
        m = duck(m, v, sr, duck_db)
        m_lufs = dsp.integrated_lufs(m, sr)
        stereo = stereo + m
    out = stereo
    for _ in range(3):
        cur = dsp.integrated_lufs(out, sr)
        if not np.isfinite(cur):
            break
        out = dsp.limit(out * dsp.db_to_gain(target_lufs - cur), sr, ceiling_dbtp)
        if abs(dsp.integrated_lufs(out, sr) - target_lufs) < 0.2:
            break
    report = MixReport(
        lufs=round(dsp.integrated_lufs(out, sr), 2),
        true_peak_dbtp=round(dsp.true_peak_dbtp(out, sr), 2),
        voice_lufs=round(v_lufs, 2),
        music_lufs=round(m_lufs, 2) if np.isfinite(m_lufs) else -99.0,
        duration=round(n / sr, 3),
    )
    return out, report


def write_wav(path: Path, audio: npt.ArrayLike, sr: int = SR) -> Path:
    """Write float32 WAV (mono or stereo)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(path, sr, np.asarray(audio, dtype=np.float32))
    return path


def read_wav(path: Path) -> tuple[int, npt.NDArray[np.float64]]:
    """Read a WAV as float64 in [-1, 1]."""
    sr, data = wavfile.read(path)
    a = np.asarray(data)
    if a.dtype.kind == "i":
        a = a / float(np.iinfo(a.dtype).max)
    return int(sr), np.asarray(a, dtype=np.float64)
