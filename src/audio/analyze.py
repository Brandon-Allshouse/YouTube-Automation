"""Numeric audio analysis + a waveform/spectrogram image for review.

Metrics: integrated loudness (our BS.1770 meter, cross-checked against FFmpeg ``ebur128``), true
peak, clipping, silence gaps, and speech-to-music ratio (voice vs. music level). The image is drawn
with our own canvas (no plotting library).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from scipy import signal

from engine.core.canvas import Fonts, FontSpec, Picture, Surface
from src.audio import dsp
from src.common import proc
from src.common.ffmpeg import ffmpeg_exe


def silence_gaps(x: npt.ArrayLike, sr: int, floor_db: float = -45.0, min_s: float = 0.3) -> list[tuple[float, float]]:
    """Stretches quieter than ``floor_db`` dBFS (RMS, 20 ms windows) lasting ≥ ``min_s``."""
    a = np.mean(dsp.as_2d(x), axis=1)
    hop = int(0.02 * sr)
    n = len(a) // hop
    if n == 0:
        return []
    rms = np.sqrt(np.mean(a[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    quiet = 20 * np.log10(rms) < floor_db
    gaps, i = [], 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if (j - i) * 0.02 >= min_s:
                gaps.append((round(i * 0.02, 2), round(j * 0.02, 2)))
            i = j
        else:
            i += 1
    return gaps


def ffmpeg_loudness(path: Path) -> dict[str, float]:
    """Independent loudness measurement via FFmpeg's ebur128 filter (I, LRA, true peak)."""
    res = proc.run(
        [
            ffmpeg_exe(),
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-filter_complex",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        timeout=300,
        check=False,
    )
    text = res.stderr.decode(errors="replace")
    summary = text[text.rfind("Summary:") :]
    out: dict[str, float] = {}
    for key, pat in (
        ("I", r"I:\s+(-?[\d.]+) LUFS"),
        ("LRA", r"LRA:\s+(-?[\d.]+) LU"),
        ("TP", r"Peak:\s+(-?[\d.]+) dBFS"),
    ):
        m = re.search(pat, summary)
        if m:
            out[key] = float(m.group(1))
    return out


def analyze(
    master: npt.ArrayLike,
    sr: int,
    voice: npt.ArrayLike | None = None,
    music: npt.ArrayLike | None = None,
) -> dict[str, Any]:
    """Compute review metrics for a mix."""
    m = dsp.as_2d(master)
    report: dict[str, Any] = {
        "duration_s": round(m.shape[0] / sr, 2),
        "integrated_lufs": round(dsp.integrated_lufs(m, sr), 2),
        "true_peak_dbtp": round(dsp.true_peak_dbtp(m, sr), 2),
        "clipped_samples": int(np.sum(np.abs(m) >= 0.999)),
        "silence_gaps_gt_1_5s": silence_gaps(m, sr, -45.0, 1.5),
    }
    if voice is not None and music is not None:
        report["speech_to_music_db"] = round(dsp.integrated_lufs(voice, sr) - dsp.integrated_lufs(music, sr), 2)
    return report


def _colormap(v: npt.NDArray[np.float64]) -> npt.NDArray[np.uint8]:
    """Perceptual-ish dark→amber→white ramp."""
    stops = np.array(
        [[8, 10, 24], [52, 30, 90], [180, 60, 80], [240, 160, 60], [255, 245, 220]],
        dtype=np.float64,
    )
    x = np.clip(v, 0, 1) * (len(stops) - 1)
    i = np.minimum(x.astype(int), len(stops) - 2)
    f = (x - i)[..., None]
    rgb = stops[i] * (1 - f) + stops[i + 1] * f
    return np.asarray(rgb, dtype=np.uint8)


def review_image(
    master: npt.ArrayLike,
    sr: int,
    out_png: Path,
    *,
    title: str = "",
    marks: list[float] | None = None,
) -> Path:
    """Waveform (top) + log-frequency spectrogram (bottom) PNG with optional time markers."""
    w, h = 1600, 900
    surf = Surface(w, h, Fonts())
    cv = surf.canvas
    cv.clear((0.04, 0.05, 0.08, 1))
    a = np.mean(dsp.as_2d(master), axis=1)
    dur = len(a) / sr
    # waveform: min/max per column
    top, wh = 60.0, 300.0
    cols = w - 80
    per = max(1, len(a) // cols)
    trimmed = (
        a[: per * cols].reshape(cols, per)
        if len(a) >= per * cols
        else np.pad(a, (0, per * cols - len(a))).reshape(cols, per)
    )
    mins, maxs = trimmed.min(axis=1), trimmed.max(axis=1)
    mid = top + wh / 2
    for i in range(cols):
        x = 40 + i
        cv.line(
            x,
            mid - maxs[i] * wh / 2,
            x,
            mid - mins[i] * wh / 2,
            (0.35, 0.8, 0.9, 1),
            1.0,
            cap="butt",
        )
    for db in (-1.0,):
        y = wh / 2 * dsp.db_to_gain(db)
        cv.line(40, mid - y, 40 + cols, mid - y, (1, 0.3, 0.3, 0.6), 1.0)
        cv.line(40, mid + y, 40 + cols, mid + y, (1, 0.3, 0.3, 0.6), 1.0)
    # spectrogram
    f, _, sxx = signal.spectrogram(a, sr, nperseg=2048, noverlap=1536, scaling="spectrum")
    db = 10 * np.log10(sxx + 1e-12)
    db = np.clip((db - (db.max() - 80)) / 80, 0, 1)
    sh = 460
    fmin, fmax = 40.0, min(16000.0, sr / 2)
    rows = np.geomspace(fmax, fmin, sh)
    idx = np.clip(np.searchsorted(f, rows), 0, len(f) - 1)
    img = db[idx]  # (sh, frames)
    xs = np.linspace(0, img.shape[1] - 1, cols).astype(int)
    img = img[:, xs]
    rgba = np.zeros((sh, cols, 4), np.uint8)
    rgba[..., :3] = _colormap(img)
    rgba[..., 3] = 255
    cv.picture(Picture.from_array(rgba), 40, 400)
    font = FontSpec("JetBrains Mono", 18, 500)
    for hz in (100, 1000, 4000, 10000):
        y = 400 + sh * float(np.log(fmax / hz) / np.log(fmax / fmin))
        cv.text(f"{hz}Hz", 44, float(y) - 4, font, (1, 1, 1, 0.7))
    for mark in marks or []:
        mx = 40 + cols * mark / dur
        cv.line(mx, top, mx, 400 + sh, (1, 1, 1, 0.25), 1.0)
    cv.text(title, 40, 36, FontSpec("Inter", 24, 600), (1, 1, 1, 0.9))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out_png.write_bytes(surf.snapshot().to_png())
    return out_png


def dump(report: dict[str, Any], path: Path) -> Path:
    """Write a report as JSON."""
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path
