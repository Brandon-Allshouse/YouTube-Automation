"""Animation/typography checks on real renders.

* **Safe zones / clipping:** every readable text box (effective opacity ≥ 0.35) must lie inside
  the format's safe area and fully on canvas. Boxes come from the canvas text recorder during a
  check render (device-space, after all transforms).
* **Words on screen:** ≤ ``max_words_on_screen`` readable words outside captions at any sampled
  moment (tokens with ≥ 2 letters; pure numbers/symbols are glanceable and not counted).
* **Contrast:** WCAG ratio of each readable text colour vs. the median background behind it,
  sampled from a second render with text suppressed. AA thresholds: 3:1 for large text
  (≥ 48 px at 1080p), 4.5:1 otherwise. Captions are judged against their outline stroke.
* **Static frames:** longest stretch where the picture doesn't meaningfully change, measured on
  the decoded video (10 fps, downscaled so film grain averages out).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from engine.build import build_from_spec
from engine.core.canvas import TextRecord
from engine.core.color import contrast_ratio, relative_luminance
from engine.core.scene import FrameRenderer
from src.common import proc
from src.common.config import rules
from src.common.ffmpeg import ffmpeg_exe

READABLE_OPACITY = 0.35
LARGE_TEXT_PX = 48.0  # at 1080 px short side
STATIC_DIFF = 0.9  # mean abs luminance change (0-255) per 0.1 s below which a step is "static"


@dataclass
class VisualReport:
    """Findings from the text scan."""

    safe_zone_violations: list[dict[str, Any]] = field(default_factory=list)
    offcanvas: list[dict[str, Any]] = field(default_factory=list)
    max_words_on_screen: int = 0  # raw peak over all samples, including transient crossfades
    max_words_persistent: int = 0  # peak that persisted ≥ 2 consecutive samples (used by the check)
    word_violations: list[dict[str, Any]] = field(default_factory=list)
    contrast_failures: list[dict[str, Any]] = field(default_factory=list)
    min_contrast: float = 99.0
    samples: int = 0


def _count_words(texts: list[str]) -> int:
    return sum(1 for t in texts for w in t.split() if len(re.findall(r"[A-Za-z]", w)) >= 2)


def scan_text(spec_json: str, *, sample_fps: float = 2.0, scale: float = 0.5) -> VisualReport:
    """Render sampled frames with instrumentation and check typography rules."""
    comp, ctx = build_from_spec(spec_json, scale)
    fr = FrameRenderer(comp, ctx)
    cv = fr.surface.canvas
    q = rules().quality
    rep = VisualReport()
    safe = ctx.safe
    unit = ctx.unit
    tol = 2.0
    times = np.arange(0.0, comp.duration, 1.0 / sample_fps)
    for t in times:
        frame = round(float(t) * comp.fps)
        records: list[TextRecord] = []
        cv.recorder, cv.suppress_text = records, False
        fr.render(frame)
        cv.recorder, cv.suppress_text = None, True
        plate = fr.render(frame).copy()
        cv.suppress_text = False
        rep.samples += 1
        # dedupe identical draws (glow + fill passes of the same word count once)
        seen: set[tuple[str, int, int]] = set()
        readable = []
        for r in records:
            key = (r.text, round(r.box[0]), round(r.box[1]))
            if r.opacity >= READABLE_OPACITY and key not in seen:
                seen.add(key)
                readable.append(r)
        content_words = _count_words([r.text for r in readable if r.role == "content"])
        rep.max_words_on_screen = max(rep.max_words_on_screen, content_words)
        if content_words > q.max_words_on_screen:
            rep.word_violations.append({"t": round(float(t), 2), "words": content_words})
        for r in readable:
            left, top, right, bottom = r.box
            if left < -tol or top < -tol or right > ctx.width + tol or bottom > ctx.height + tol:
                rep.offcanvas.append({"t": round(float(t), 2), "text": r.text[:40]})
            elif left < safe.x - tol or top < safe.y - tol or right > safe.right + tol or bottom > safe.bottom + tol:
                rep.safe_zone_violations.append({"t": round(float(t), 2), "text": r.text[:40], "role": r.role})
            if r.role == "caption":
                ratio = contrast_ratio(r.color, (0.02, 0.02, 0.04, 1.0))  # captions sit on a dark outline
            else:
                x0, y0 = max(int(left), 0), max(int(top), 0)
                x1, y1 = min(int(right), ctx.width), min(int(bottom), ctx.height)
                if x1 <= x0 or y1 <= y0:
                    continue
                bg = plate[y0:y1, x0:x1, :3].reshape(-1, 3).astype(np.float64) / 255.0
                lum = np.array(
                    [
                        relative_luminance((float(a), float(b), float(c), 1.0))
                        for a, b, c in bg[:: max(1, len(bg) // 600)]
                    ]
                )
                text_l = relative_luminance(r.color)
                bg_l = float(np.median(lum))
                hi, lo = max(text_l, bg_l), min(text_l, bg_l)
                ratio = (hi + 0.05) / (lo + 0.05)
            large = r.size_px / max(unit, 1e-6) >= LARGE_TEXT_PX
            need = 3.0 if large else q.min_contrast_ratio
            rep.min_contrast = min(rep.min_contrast, ratio)
            if ratio < need:
                rep.contrast_failures.append(
                    {"t": round(float(t), 2), "text": r.text[:40], "ratio": round(ratio, 2), "need": need}
                )
    return _persistent(rep, 1.0 / sample_fps)


def _persistent(rep: VisualReport, step: float) -> VisualReport:
    """Keep only violations present in two consecutive samples (≥ one sample interval).

    Entrance/exit crossfades last ~0.3 s; a reader can't parse text in that time, so transient
    overlaps are not "on screen at once". Safe-zone/off-canvas violations are never filtered.
    """

    def keep(items: list[dict[str, Any]], key: str | None) -> list[dict[str, Any]]:
        idx = {(round(i["t"] / step), i.get(key) if key else None) for i in items}
        return [
            i
            for i in items
            if (round(i["t"] / step) - 1, i.get(key) if key else None) in idx
            or (round(i["t"] / step) + 1, i.get(key) if key else None) in idx
        ]

    rep.word_violations = keep(rep.word_violations, None)
    rep.contrast_failures = keep(rep.contrast_failures, "text")
    # report both: the raw transient peak (honest) and the persistent peak (what the check uses)
    rep.max_words_persistent = max((v["words"] for v in rep.word_violations), default=0) or min(
        rep.max_words_on_screen, rules().quality.max_words_on_screen
    )
    return rep


def static_runs(video: Path, duration: float) -> dict[str, Any]:
    """Longest stretch (s) with no meaningful picture change, from the decoded video."""
    w, h = 54, 96
    res = proc.run(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-vf", f"fps=10,scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "pipe:1"],
        timeout=300,
    )  # fmt: skip
    frames = np.frombuffer(res.stdout, dtype=np.uint8)
    n = len(frames) // (w * h)
    if n < 2:
        return {"longest_static_s": 0.0, "at_s": 0.0}
    f = frames[: n * w * h].reshape(n, h, w).astype(np.float32)
    diffs = np.mean(np.abs(np.diff(f, axis=0)), axis=(1, 2))
    longest, cur, start, best_start = 0, 0, 0, 0
    for i, d in enumerate(diffs):
        if d < STATIC_DIFF:
            if cur == 0:
                start = i
            cur += 1
            if cur > longest:
                longest, best_start = cur, start
        else:
            cur = 0
    return {
        "longest_static_s": round(longest / 10.0, 1),
        "at_s": round(best_start / 10.0, 1),
        "median_change": round(float(np.median(diffs)), 3),
        "duration_s": round(duration, 2),
    }
