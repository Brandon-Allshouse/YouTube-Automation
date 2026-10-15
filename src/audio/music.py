"""Procedural music generator: fully owned beds, so there is no licensing risk.

Everything is synthesised in NumPy/SciPy:

* oscillators: PolyBLEP band-limited saw/pulse, sine, triangle, noise
* Karplus-Strong plucks implemented as an IIR comb (``lfilter``), so no per-sample Python loops
* ADSR envelopes, resonant biquad filters with slow cutoff motion
* drums from sweeps and filtered noise; sidechain "pump" from the kick
* Schroeder reverb (parallel combs + series allpasses) via ``lfilter``
* sound design: noise risers into scene changes and a low impact on the hook

Styles (``cinematic``, ``synthwave``, ``lofi``, ``ambient``) define tempo, harmony, instruments
and patterns; a channel's seed picks key, progression and voicing so every channel has its own
sound, and each video gets its own variation. Arrangements follow an energy curve (intro → body →
outro) and keep energy out of the 1-4 kHz speech band where possible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt
from scipy import signal

from src.audio.dsp import SR, Audio, biquad, fade

Style = Literal["cinematic", "synthwave", "lofi", "ambient"]

SCALES = {
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "major": (0, 2, 4, 5, 7, 9, 11),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
}


@dataclass(frozen=True)
class StyleDef:
    """Parameters of a musical style."""

    bpm: tuple[int, int]
    scale: str
    progressions: tuple[tuple[int, ...], ...]  # scale degrees (0-based)
    bars_per_chord: int
    sevenths: bool
    roots: tuple[int, ...]  # candidate MIDI roots for the key
    drums: bool
    arp: bool
    pluck: bool
    pump: float  # sidechain depth 0..1
    swing: float
    brightness: float  # pad filter cutoff multiplier
    reverb: float


STYLES: dict[str, StyleDef] = {
    "cinematic": StyleDef((70, 84), "minor",
                          ((0, 5, 2, 6), (0, 3, 5, 4), (5, 6, 0, 0), (0, 5, 3, 4)), 2, False,
                          (45, 47, 48, 50), False, False, True, 0.0, 0.0, 0.8, 0.55),
    "synthwave": StyleDef((96, 108), "minor", ((0, 5, 2, 6), (0, 6, 5, 6), (5, 3, 0, 4)), 1, False,
                          (45, 48, 50, 52), True, True, False, 0.55, 0.0, 1.2, 0.35),
    "lofi": StyleDef((72, 86), "dorian", ((1, 4, 0, 5), (0, 3, 1, 4), (3, 4, 2, 5)), 1, True,
                     (48, 50, 51, 53), True, False, True, 0.25, 0.16, 0.6, 0.3),
    "ambient": StyleDef((60, 70), "lydian", ((0, 3), (0, 4, 3, 0), (5, 3, 0, 4)), 2, True,
                        (43, 45, 47, 48), False, False, True, 0.0, 0.0, 0.7, 0.75),
}  # fmt: skip


def midi_hz(m: float) -> float:
    """MIDI note number to frequency."""
    return 440.0 * 2 ** ((m - 69) / 12)


# --- oscillators ----------------------------------------------------------------------------


def _phase(freq: float | npt.NDArray[np.float64], n: int, sr: int, phase0: float = 0.0) -> npt.NDArray[np.float64]:
    f = np.broadcast_to(np.asarray(freq, dtype=np.float64), (n,))
    return np.asarray((phase0 + np.cumsum(f) / sr) % 1.0, dtype=np.float64)


def _polyblep(p: npt.NDArray[np.float64], dt: npt.NDArray[np.float64] | float) -> npt.NDArray[np.float64]:
    dtv = np.broadcast_to(np.asarray(dt, dtype=np.float64), p.shape)
    out = np.zeros_like(p)
    m1 = p < dtv
    t = p[m1] / dtv[m1]
    out[m1] = 2 * t - t * t - 1
    m2 = p > 1 - dtv
    t = (p[m2] - 1) / dtv[m2]
    out[m2] = t * t + 2 * t + 1
    return out


def saw(freq: float, n: int, sr: int = SR, phase0: float = 0.0) -> npt.NDArray[np.float64]:
    """Band-limited sawtooth (PolyBLEP)."""
    p = _phase(freq, n, sr, phase0)
    return 2 * p - 1 - _polyblep(p, freq / sr)


def pulse(freq: float, n: int, sr: int = SR, width: float = 0.5, phase0: float = 0.0) -> npt.NDArray[np.float64]:
    """Band-limited pulse wave (difference of two PolyBLEP saws)."""
    p = _phase(freq, n, sr, phase0)
    dt = freq / sr
    a = 2 * p - 1 - _polyblep(p, dt)
    p2 = (p + width) % 1.0
    b = 2 * p2 - 1 - _polyblep(p2, dt)
    return 0.5 * (a - b)


def sine(freq: float | npt.NDArray[np.float64], n: int, sr: int = SR, phase0: float = 0.0) -> npt.NDArray[np.float64]:
    """Sine oscillator (accepts per-sample frequency for sweeps)."""
    return np.sin(2 * np.pi * _phase(freq, n, sr, phase0))


def adsr(n: int, sr: int, a: float, d: float, s: float, r: float, gate: float | None = None) -> npt.NDArray[np.float64]:
    """ADSR envelope of ``n`` samples; release starts at ``gate`` s (default: end minus r)."""
    t = np.arange(n) / sr
    g = gate if gate is not None else max(n / sr - r, a + d)
    env = np.where(t < a, t / max(a, 1e-6), np.where(t < a + d, 1 - (1 - s) * (t - a) / max(d, 1e-6), s))
    rel = t >= g
    level_at_gate = s if g >= a + d else (g / max(a, 1e-6) if g < a else 1 - (1 - s) * (g - a) / max(d, 1e-6))
    env = np.where(rel, level_at_gate * np.exp(-(t - g) / max(r / 4, 1e-4)), env)
    return np.asarray(env, dtype=np.float64)


def feedback_comb(x: npt.NDArray[np.float64], delay: int, g: float, avg2: bool = False) -> npt.NDArray[np.float64]:
    """y[n] = x[n] + g·y[n-D]  (or g·(y[n-D] + y[n-D-1])/2 when ``avg2``), computed block-wise.

    Every sample in a block of length D depends only on earlier blocks, so each block is one
    vectorised operation: O(n) work with n/D Python iterations instead of an O(n·D) IIR filter.
    """
    n = len(x)
    y = np.array(x, dtype=np.float64, copy=True)
    d = max(int(delay), 2)
    for start in range(d, n, d):
        stop = min(start + d, n)
        prev = y[start - d : stop - d]
        if avg2:
            prev2 = (
                y[start - d - 1 : stop - d - 1] if start - d - 1 >= 0 else np.concatenate(([0.0], y[: stop - d - 1]))
            )
            y[start:stop] += g * 0.5 * (prev + prev2)
        else:
            y[start:stop] += g * prev
    return y


def allpass(x: npt.NDArray[np.float64], delay: int, g: float) -> npt.NDArray[np.float64]:
    """Schroeder allpass y[n] = -g·x[n] + x[n-D] + g·y[n-D], block-wise."""
    n = len(x)
    d = max(int(delay), 1)
    xd = np.concatenate((np.zeros(d), x))[:n]
    y = -g * x + xd
    for start in range(d, n, d):
        stop = min(start + d, n)
        y[start:stop] += g * y[start - d : stop - d]
    return y


def pluck(
    freq: float,
    n: int,
    sr: int,
    rng: np.random.Generator,
    decay: float = 0.996,
    brightness: float = 0.5,
) -> npt.NDArray[np.float64]:
    """Karplus-Strong plucked string: noise burst into an averaging feedback comb."""
    period = max(2, int(sr / freq))
    burst = np.zeros(n)
    k = min(period, n)
    exc = signal.lfilter([brightness], [1, -(1 - brightness)], rng.uniform(-1, 1, k))  # softer attack
    burst[:k] = exc
    return feedback_comb(burst, period, decay, avg2=True)


# --- effects --------------------------------------------------------------------------------


def reverb(
    x: npt.NDArray[np.float64], sr: int, mix: float, size: float = 1.0, seed_offset: int = 0
) -> npt.NDArray[np.float64]:
    """Schroeder reverb (4 feedback combs in parallel → 2 allpasses), returns wet+dry mono."""
    if mix <= 0:
        return x
    out = np.zeros_like(x)
    for i, d in enumerate((1557, 1617, 1491, 1422)):
        delay = int((d + 23 * seed_offset + 7 * i) * size * sr / 44100)
        y = feedback_comb(x, delay, 0.84)
        out += signal.lfilter([0.6], [1, -0.4], y)  # damping
    for d in (225, 556):
        out = allpass(out, int((d + 11 * seed_offset) * sr / 44100), 0.5)
    out *= 0.12
    return np.asarray((1 - mix * 0.5) * x + mix * out, dtype=np.float64)


def lowpass(x: npt.NDArray[np.float64], f: float, sr: int, q: float = 0.707) -> npt.NDArray[np.float64]:
    """Biquad low-pass."""
    b, a = biquad("lowpass", min(f, sr * 0.45), sr, q)
    return np.asarray(signal.lfilter(b, a, x), dtype=np.float64)


def highpass(x: npt.NDArray[np.float64], f: float, sr: int, q: float = 0.707) -> npt.NDArray[np.float64]:
    """Biquad high-pass."""
    b, a = biquad("highpass", f, sr, q)
    return np.asarray(signal.lfilter(b, a, x), dtype=np.float64)


def _pan(mono: npt.NDArray[np.float64], pan: float) -> Audio:
    ang = (pan + 1) * math.pi / 4
    return np.stack([mono * math.cos(ang), mono * math.sin(ang)], axis=1)


# --- drums ----------------------------------------------------------------------------------


def kick(sr: int) -> npt.NDArray[np.float64]:
    """Sine kick with pitch drop and a soft click."""
    n = int(0.45 * sr)
    t = np.arange(n) / sr
    f = 45 + 110 * np.exp(-t * 28)
    body = sine(f, n, sr) * np.exp(-t * 7.5)
    click = np.random.default_rng(1).uniform(-1, 1, n) * np.exp(-t * 300) * 0.15
    return body + lowpass(click, 3000, sr)


def snare(sr: int, rng: np.random.Generator) -> npt.NDArray[np.float64]:
    """Noise + tone snare."""
    n = int(0.3 * sr)
    t = np.arange(n) / sr
    noise = signal.lfilter(*biquad("bandpass", 1900, sr, 0.8), rng.uniform(-1, 1, n)) * np.exp(-t * 18)
    tone = sine(185.0, n, sr) * np.exp(-t * 25) * 0.5
    return np.asarray(noise * 1.4 + tone, dtype=np.float64)


def hat(sr: int, rng: np.random.Generator, open_: bool = False) -> npt.NDArray[np.float64]:
    """High-passed noise hat."""
    n = int((0.25 if open_ else 0.06) * sr)
    t = np.arange(n) / sr
    return highpass(rng.uniform(-1, 1, n), 7500, sr) * np.exp(-t * (12 if open_ else 70))


def _place(buf: npt.NDArray[np.float64], clip: npt.NDArray[np.float64], at: int, gain: float) -> None:
    if at >= len(buf) or at < 0:
        return
    m = min(len(clip), len(buf) - at)
    buf[at : at + m] += clip[:m] * gain


# --- composition ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Arrangement:
    """Resolved musical choices for one bed (stored in the manifest for reproducibility)."""

    style: str
    bpm: float
    root: int
    progression: tuple[int, ...]
    seed: int


def plan(style: Style, seed: int) -> Arrangement:
    """Pick tempo, key and progression deterministically from ``seed``."""
    sd = STYLES[style]
    rng = np.random.default_rng(seed)
    return Arrangement(
        style=style,
        bpm=float(rng.integers(sd.bpm[0], sd.bpm[1] + 1)),
        root=int(rng.choice(sd.roots)),
        progression=tuple(sd.progressions[int(rng.integers(len(sd.progressions)))]),
        seed=seed,
    )


def _chord(root: int, scale: tuple[int, ...], degree: int, sevenths: bool) -> list[int]:
    def note(step: int) -> int:
        octave, idx = divmod(degree + step, len(scale))
        return root + 12 * octave + scale[idx]

    notes = [note(0), note(2), note(4)]
    if sevenths:
        notes.append(note(6))
    return notes


def energy_curve(t: npt.NDArray[np.float64], duration: float) -> npt.NDArray[np.float64]:
    """Intro swell, full body, gentle outro."""
    intro = np.clip(t / min(4.0, duration * 0.15), 0, 1)
    outro = np.clip((duration - t) / min(3.0, duration * 0.1), 0, 1)
    return np.asarray(0.55 + 0.45 * np.minimum(intro, outro), dtype=np.float64)


def generate(
    style: Style,
    duration: float,
    seed: int,
    *,
    sr: int = SR,
    cues: tuple[float, ...] = (),
    hook_hit: bool = True,
) -> tuple[Audio, Arrangement]:
    """Render a stereo music bed.

    Args:
        style: Musical style.
        duration: Seconds.
        seed: Channel/video seed (key, progression, voicing, patterns).
        cues: Times (s) of scene changes; each gets a short riser ending on the cut.
        hook_hit: Add a low impact at t=0 for the hook.

    Returns:
        (stereo float64 array shaped (n, 2), arrangement).
    """
    if not 0 < duration <= 1800:
        raise ValueError("music duration out of range")
    sd = STYLES[style]
    arr = plan(style, seed)
    rng = np.random.default_rng(seed + 1)
    n = int(duration * sr)
    t = np.arange(n) / sr
    beat = 60.0 / arr.bpm
    bar = 4 * beat
    chord_len = bar * sd.bars_per_chord
    scale = SCALES[sd.scale]
    pad = np.zeros(n)
    bass = np.zeros(n)
    lead = np.zeros(n)
    drums = np.zeros(n)
    kick_env = np.zeros(n)

    n_chords = math.ceil(duration / chord_len) + 1
    for ci in range(n_chords):
        start = ci * chord_len
        s0 = int(start * sr)
        if s0 >= n:
            break
        degree = arr.progression[ci % len(arr.progression)]
        notes = _chord(arr.root + 12, scale, degree, sd.sevenths)
        seg_n = min(int((chord_len + 1.2) * sr), n - s0)
        env = adsr(seg_n, sr, 0.6 if style != "synthwave" else 0.08, 0.5, 0.8, 1.2, gate=chord_len)
        chord_sig = np.zeros(seg_n)
        for k, m in enumerate(notes):
            f = midi_hz(m)
            for det in (-0.09, 0.0, 0.11):
                chord_sig += saw(f * 2 ** (det / 12), seg_n, sr, phase0=float(rng.random())) * (0.5 if det else 0.7)
            if k == 0 and style in ("cinematic", "ambient"):
                chord_sig += sine(f / 2, seg_n, sr) * 0.8
        cutoff = 700 * sd.brightness * (1.0 + 0.4 * math.sin(ci * 1.3))
        chord_sig = lowpass(lowpass(chord_sig, cutoff, sr, 0.9), cutoff * 1.6, sr)
        pad[s0 : s0 + seg_n] += chord_sig * env * 0.12
        # bass: root an octave below
        f_b = midi_hz(notes[0] - 12)
        if style == "synthwave":
            step = beat / 2
            for j in range(int(chord_len / step)):
                b0 = int((start + j * step) * sr)
                bn = min(int(step * 0.9 * sr), n - b0)
                if bn <= 0:
                    break
                tone = lowpass(saw(f_b, bn, sr), 380, sr, 1.2) + 0.6 * sine(f_b, bn, sr)
                bass[b0 : b0 + bn] += tone * adsr(bn, sr, 0.005, 0.08, 0.6, 0.05) * 0.35
        else:
            bn = min(int(chord_len * sr), n - s0)
            tone = sine(f_b, bn, sr) + 0.25 * lowpass(saw(f_b, bn, sr), 300, sr)
            bass[s0 : s0 + bn] += tone * adsr(bn, sr, 0.4 if style == "cinematic" else 0.02, 0.3, 0.7, 0.6) * 0.32
        # melodic layer
        if sd.arp:
            step = beat / 4
            pattern = [0, 1, 2, 1, 0, 2, 1, 2] if rng.random() < 0.5 else [0, 2, 1, 2, 0, 1, 2, 1]
            for j in range(int(chord_len / step)):
                a0 = int((start + j * step) * sr)
                an = min(int(step * 0.95 * sr), n - a0)
                if an <= 0:
                    break
                m = notes[pattern[j % len(pattern)]] + 12
                tone = lowpass(pulse(midi_hz(m), an, sr, 0.3), 2200, sr)
                lead[a0 : a0 + an] += tone * adsr(an, sr, 0.003, 0.09, 0.2, 0.02) * 0.10
        if sd.pluck:
            hits = int(chord_len / beat)
            for j in range(hits):
                if rng.random() < (0.45 if style == "cinematic" else 0.6):
                    p0 = int((start + j * beat + (sd.swing * beat / 2 if j % 2 else 0)) * sr)
                    m = notes[int(rng.integers(len(notes)))] + 12 * int(rng.integers(1, 3))
                    pn = min(int(2.5 * sr), n - p0)
                    if pn > 0:
                        lead[p0 : p0 + pn] += pluck(midi_hz(m), pn, sr, rng, 0.995, 0.45) * 0.16
        # drums
        if sd.drums and ci >= 1:  # drums enter after the first chord: arrangement lift
            for j in range(int(chord_len / beat)):
                bt = start + j * beat
                b0 = int(bt * sr)
                if style == "synthwave":
                    _place(drums, kick(sr), b0, 0.55)
                    _place(kick_env, np.exp(-np.arange(int(0.3 * sr)) / (0.09 * sr)), b0, 1.0)
                    if j % 2 == 1:
                        _place(drums, snare(sr, rng), b0, 0.28)
                    for h in (0.0, 0.5):
                        _place(drums, hat(sr, rng), int((bt + h * beat) * sr), 0.06)
                else:  # lofi: lazy kick/snare, swung hats
                    if j % 4 in (0, 2) and not (j % 4 == 2 and rng.random() < 0.4):
                        _place(drums, kick(sr), b0, 0.45)
                        _place(kick_env, np.exp(-np.arange(int(0.3 * sr)) / (0.1 * sr)), b0, 1.0)
                    if j % 4 in (1, 3):
                        _place(drums, lowpass(snare(sr, rng), 4000, sr), b0, 0.2)
                    for h in (0.0, 0.5 + sd.swing):
                        _place(drums, hat(sr, rng), int((bt + h * beat) * sr), 0.04)

    energy = energy_curve(t, duration)
    pump = 1.0 - sd.pump * np.clip(kick_env, 0, 1)
    pad = reverb(pad * energy * pump, sr, sd.reverb, 1.0, 0)
    lead = reverb(lead * energy, sr, sd.reverb * 0.8, 0.9, 1)
    bass *= energy
    drums *= np.clip(energy * 1.2, 0, 1)
    # speech-band courtesy: dip pad/lead around 2.5 kHz so the voice owns that range
    dip = biquad("peak", 2500, sr, 0.8, -4.0)
    pad = np.asarray(signal.lfilter(dip[0], dip[1], pad), dtype=np.float64)
    lead = np.asarray(signal.lfilter(dip[0], dip[1], lead), dtype=np.float64)

    mix = _pan(pad, -0.2) + _pan(np.roll(pad, int(0.011 * sr)), 0.2)  # Haas widening
    mix += _pan(bass, 0.0) + _pan(drums, 0.05) + _pan(lead, 0.3 if sd.arp else -0.3)

    # sound design
    fx = np.zeros(n)
    for cue in cues:
        if 0.6 < cue < duration:
            rl = 0.6
            r0 = int((cue - rl) * sr)
            rn = int(rl * sr)
            rt = np.arange(rn) / rn
            sweep = signal.lfilter(*biquad("bandpass", 1200, sr, 0.7), rng.uniform(-1, 1, rn))
            _place(fx, sweep * rt**2.2 * (1 - np.clip((rt - 0.97) / 0.03, 0, 1)), r0, 0.18)
    if hook_hit:
        hn = int(1.6 * sr)
        ht = np.arange(hn) / sr
        boom = sine(38 + 60 * np.exp(-ht * 9), hn, sr) * np.exp(-ht * 2.6)
        _place(fx, boom, 0, 0.55)
    mix += _pan(reverb(fx, sr, 0.3, 1.2, 2), 0.0)

    mix = fade(mix, sr, fade_in=0.02, fade_out=min(2.5, duration * 0.1))
    peak = float(np.max(np.abs(mix))) or 1.0
    return mix / peak * 0.7, arr
