"""Our own DSP building blocks (NumPy/SciPy primitives only).

* RBJ-cookbook biquads (low/high-pass, peaking, shelving, band-pass)
* envelope follower, RMS compressor, split-band de-esser
* ITU-R BS.1770-4 integrated loudness (K-weighting + gating) and 4x-oversampled true peak
* lookahead true-peak limiter

Signals are float64/float32 arrays shaped ``(n,)`` (mono) or ``(n, channels)``.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt
from scipy import ndimage, signal

Audio = npt.NDArray[np.float64]
SR = 48000


def db_to_gain(db: float) -> float:
    """Decibels to linear amplitude."""
    return float(10.0 ** (db / 20.0))


def gain_to_db(g: float) -> float:
    """Linear amplitude to decibels (floor at -200 dB)."""
    return 20.0 * math.log10(max(g, 1e-10))


def as_2d(x: npt.ArrayLike) -> Audio:
    """View audio as (n, channels) float64."""
    a = np.asarray(x, dtype=np.float64)
    return a[:, None] if a.ndim == 1 else a


def resample(x: npt.ArrayLike, sr_in: int, sr_out: int) -> Audio:
    """Polyphase resampling (anti-aliased)."""
    a = np.asarray(x, dtype=np.float64)
    if sr_in == sr_out:
        return a
    g = math.gcd(sr_in, sr_out)
    return np.asarray(signal.resample_poly(a, sr_out // g, sr_in // g, axis=0), dtype=np.float64)


# --- biquads (RBJ audio EQ cookbook) --------------------------------------------------------


def biquad(kind: str, f0: float, sr: int = SR, q: float = 0.707, gain_db: float = 0.0) -> tuple[Audio, Audio]:
    """Return (b, a) coefficients for an RBJ biquad.

    Args:
        kind: ``lowpass``, ``highpass``, ``bandpass``, ``peak``, ``lowshelf``, ``highshelf``.
    """
    w0 = 2 * math.pi * f0 / sr
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2 * q)
    amp = 10 ** (gain_db / 40)
    if kind == "lowpass":
        b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "highpass":
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "bandpass":
        b = [alpha, 0.0, -alpha]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "peak":
        b = [1 + alpha * amp, -2 * cw, 1 - alpha * amp]
        a = [1 + alpha / amp, -2 * cw, 1 - alpha / amp]
    elif kind in ("lowshelf", "highshelf"):
        sq = 2 * math.sqrt(amp) * alpha
        if kind == "lowshelf":
            b = [
                amp * ((amp + 1) - (amp - 1) * cw + sq),
                2 * amp * ((amp - 1) - (amp + 1) * cw),
                amp * ((amp + 1) - (amp - 1) * cw - sq),
            ]
            a = [
                (amp + 1) + (amp - 1) * cw + sq,
                -2 * ((amp - 1) + (amp + 1) * cw),
                (amp + 1) + (amp - 1) * cw - sq,
            ]
        else:
            b = [
                amp * ((amp + 1) + (amp - 1) * cw + sq),
                -2 * amp * ((amp - 1) + (amp + 1) * cw),
                amp * ((amp + 1) + (amp - 1) * cw - sq),
            ]
            a = [
                (amp + 1) - (amp - 1) * cw + sq,
                2 * ((amp - 1) - (amp + 1) * cw),
                (amp + 1) - (amp - 1) * cw - sq,
            ]
    else:
        raise ValueError(f"unknown biquad kind {kind}")
    bn = np.array(b) / a[0]
    an = np.array(a) / a[0]
    return bn, an


def apply_filter(x: npt.ArrayLike, ba: tuple[Audio, Audio]) -> Audio:
    """Apply (b, a) along time for mono or multichannel audio."""
    return np.asarray(signal.lfilter(ba[0], ba[1], np.asarray(x, dtype=np.float64), axis=0), dtype=np.float64)


def eq(x: npt.ArrayLike, kind: str, f0: float, sr: int = SR, q: float = 0.707, gain_db: float = 0.0) -> Audio:
    """Convenience: filter ``x`` with one biquad."""
    return apply_filter(x, biquad(kind, f0, sr, q, gain_db))


# --- dynamics -------------------------------------------------------------------------------


def one_pole_coeff(time_s: float, sr: int = SR) -> float:
    """Smoothing coefficient for a one-pole filter with time constant ``time_s``."""
    return math.exp(-1.0 / max(time_s * sr, 1.0))


def envelope(x: npt.ArrayLike, sr: int = SR, attack: float = 0.005, release: float = 0.08, rms: bool = True) -> Audio:
    """Smoothed level envelope (mono, linear amplitude).

    A symmetric smoother is applied to the squared (or rectified) signal, then a short max-hold
    approximates fast attack / slower release without a per-sample Python loop.
    """
    a = as_2d(x)
    mono = np.mean(a * a, axis=1) if rms else np.max(np.abs(a), axis=1)
    r = one_pole_coeff(release, sr)
    sm = signal.lfilter([1 - r], [1, -r], mono)
    hold = max(1, int(attack * sr * 4))
    sm = ndimage.maximum_filter1d(sm, size=hold)
    sm = np.maximum(sm, 0.0)
    return np.sqrt(sm) if rms else np.asarray(sm, dtype=np.float64)


def compress(
    x: npt.ArrayLike,
    sr: int = SR,
    threshold_db: float = -20.0,
    ratio: float = 3.0,
    attack: float = 0.006,
    release: float = 0.12,
    knee_db: float = 6.0,
    makeup_db: float = 0.0,
) -> Audio:
    """Soft-knee RMS compressor (feed-forward)."""
    a = as_2d(x)
    env = envelope(a, sr, attack, release)
    lvl = 20 * np.log10(np.maximum(env, 1e-9))
    over = lvl - threshold_db
    gr = np.where(
        over <= -knee_db / 2,
        0.0,
        np.where(
            over >= knee_db / 2,
            over * (1 - 1 / ratio),
            (1 - 1 / ratio) * (over + knee_db / 2) ** 2 / (2 * knee_db),
        ),
    )
    gain = 10 ** ((makeup_db - gr) / 20)
    return a * gain[:, None]


def deess(
    x: npt.ArrayLike,
    sr: int = SR,
    f_lo: float = 5000.0,
    f_hi: float = 9500.0,
    threshold_db: float = -30.0,
    max_cut_db: float = 8.0,
) -> Audio:
    """Split-band de-esser: attenuates the sibilance band only when it is hot."""
    a = as_2d(x)
    center = math.sqrt(f_lo * f_hi)
    band = apply_filter(a, biquad("bandpass", center, sr, q=center / (f_hi - f_lo)))
    env = envelope(band, sr, 0.002, 0.04)
    lvl = 20 * np.log10(np.maximum(env, 1e-9))
    cut_db = np.clip((lvl - threshold_db) * 0.6, 0.0, max_cut_db)
    g = 10 ** (-cut_db / 20)
    return a - band + band * g[:, None]


# --- loudness (ITU-R BS.1770-4) -------------------------------------------------------------


def _k_weight(a: Audio, sr: int) -> Audio:
    if sr == 48000:
        b1 = np.array([1.53512485958697, -2.69169618940638, 1.19839281085285])
        a1 = np.array([1.0, -1.69065929318241, 0.73248077421585])
        b2 = np.array([1.0, -2.0, 1.0])
        a2 = np.array([1.0, -1.99004745483398, 0.99007225036621])
    else:  # derive equivalent filters at other rates
        b1, a1 = biquad("highshelf", 1681.974450955533, sr, q=0.7071752369554196, gain_db=3.999843853973347)
        b2, a2 = biquad("highpass", 38.13547087602444, sr, q=0.5003270373238773)
    y = signal.lfilter(b1, a1, a, axis=0)
    return np.asarray(signal.lfilter(b2, a2, y, axis=0), dtype=np.float64)


def integrated_lufs(x: npt.ArrayLike, sr: int = SR) -> float:
    """Gated integrated loudness in LUFS (returns -inf for silence)."""
    a = as_2d(x)
    if a.shape[0] < int(0.4 * sr):
        return float("-inf")
    k = _k_weight(a, sr)
    block, hop = int(0.4 * sr), int(0.1 * sr)
    n_blocks = 1 + (k.shape[0] - block) // hop
    sq = k * k
    csum = np.concatenate([np.zeros((1, sq.shape[1])), np.cumsum(sq, axis=0)])
    starts = np.arange(n_blocks) * hop
    ms = (csum[starts + block] - csum[starts]) / block  # (blocks, ch)
    z = np.sum(ms, axis=1)  # L/R weights are 1.0
    with np.errstate(divide="ignore"):
        lk = -0.691 + 10 * np.log10(np.maximum(z, 1e-20))
    abs_gated = z[lk > -70.0]
    if abs_gated.size == 0:
        return float("-inf")
    rel_thresh = -0.691 + 10 * math.log10(float(np.mean(abs_gated))) - 10.0
    final = z[(lk > -70.0) & (lk > rel_thresh)]
    if final.size == 0:
        return float("-inf")
    return float(-0.691 + 10 * math.log10(float(np.mean(final))))


def true_peak_dbtp(x: npt.ArrayLike, sr: int = SR) -> float:
    """4x-oversampled true peak in dBTP."""
    a = as_2d(x)
    up = signal.resample_poly(a, 4, 1, axis=0)
    return gain_to_db(float(np.max(np.abs(up))) if up.size else 0.0)


def limit(
    x: npt.ArrayLike,
    sr: int = SR,
    ceiling_dbtp: float = -1.2,
    lookahead: float = 0.005,
    hold: float = 0.05,
) -> Audio:
    """Lookahead true-peak limiter: gain reaches its target before each over, then recovers."""
    a = as_2d(x)
    ceiling = db_to_gain(ceiling_dbtp)
    up = signal.resample_poly(a, 4, 1, axis=0)
    peak_up = np.max(np.abs(up), axis=1)
    n = a.shape[0]
    # max over the 4 oversampled points belonging to each original sample
    padded = np.pad(peak_up[: n * 4], (0, max(0, n * 4 - peak_up.size)))
    peak = np.max(padded.reshape(n, 4), axis=1)
    req = np.minimum(1.0, ceiling / np.maximum(peak, 1e-9))
    la = max(1, int(lookahead * sr))
    held = ndimage.minimum_filter1d(req, size=2 * la + int(hold * sr), origin=0)
    smooth = ndimage.uniform_filter1d(held, size=la)
    g = np.minimum(smooth, held)
    return np.asarray(a * g[:, None], dtype=np.float64)


def fade(x: npt.ArrayLike, sr: int, fade_in: float = 0.0, fade_out: float = 0.0) -> Audio:
    """Equal-power fades at the start/end."""
    a = as_2d(x).copy()
    n = a.shape[0]
    if fade_in > 0:
        k = min(n, int(fade_in * sr))
        a[:k] *= np.sin(np.linspace(0, math.pi / 2, k))[:, None]
    if fade_out > 0:
        k = min(n, int(fade_out * sr))
        a[n - k :] *= np.cos(np.linspace(0, math.pi / 2, k))[:, None]
    return a
