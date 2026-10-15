"""Thumbnail legibility checks at phone size.

Checks (all measured on real pixels):
* word count ≤ 4
* cap height on the smallest common phone placement (168 px wide "up next" thumbnail) ≥ 9 px,
  and on the 360 px home-feed placement ≥ 18 px
* WCAG contrast of text fill vs. the background *behind* it (sampled from a text-free plate):
  median ≥ 4.5:1 and worst case (95th-percentile closest background) ≥ 3:1; highlight boxes are
  checked against their own fill
* nothing in YouTube's bottom-right duration-badge zone
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from engine.core.canvas import Picture
from engine.core.color import RGBA, contrast_ratio, relative_luminance

SMALL_W = 168  # "up next" / suggested thumbnail width on phones
FEED_W = 360  # home feed thumbnail width on a typical phone
MIN_CAP_SMALL_PX = 9.0
MIN_CAP_FEED_PX = 18.0
BADGE_ZONE = (0.80, 0.82)  # x ≥ 80 %, y ≥ 82 % of the frame


@dataclass(frozen=True)
class TextBox:
    """Rendered text extent (cap-height box) and its fill colour."""

    x: float
    y: float
    w: float
    h: float
    color: RGBA
    on_highlight: bool = False
    box_fill: RGBA | None = None  # solid box drawn behind the text (known exactly, not sampled)


@dataclass
class LegibilityReport:
    """Result of all checks."""

    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly form."""
        return asdict(self)


def _lum_ratio(a: float, b: float) -> float:
    """WCAG contrast ratio from two relative luminances."""
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _load(path: Path) -> npt.NDArray[np.uint8]:
    return Picture.from_png(path.read_bytes()).to_array()


def _region(arr: npt.NDArray[np.uint8], b: TextBox) -> npt.NDArray[np.float64]:
    h, w = arr.shape[:2]
    x0, y0 = max(int(b.x), 0), max(int(b.y), 0)
    x1, y1 = min(int(b.x + b.w), w), min(int(b.y + b.h), h)
    return arr[y0:y1, x0:x1, :3].reshape(-1, 3) / 255.0


def check_legibility(plate: Path, boxes: list[TextBox], *, word_count: int) -> LegibilityReport:
    """Run all legibility checks; ``plate`` is the thumbnail rendered without its text."""
    bg = _load(plate)
    height, width = bg.shape[:2]
    rep = LegibilityReport(passed=True)
    rep.checks["max_4_words"] = word_count <= 4
    if not boxes:
        rep.notes.append("no text boxes")
    caps = [b.h for b in boxes] or [0.0]
    cap_small = min(caps) * SMALL_W / width
    cap_feed = min(caps) * FEED_W / width
    rep.metrics["cap_px_at_168w"] = round(cap_small, 1)
    rep.metrics["cap_px_at_360w"] = round(cap_feed, 1)
    rep.checks["cap_height_small"] = cap_small >= MIN_CAP_SMALL_PX
    rep.checks["cap_height_feed"] = cap_feed >= MIN_CAP_FEED_PX
    medians, worsts = [], []
    for b in boxes:
        if b.box_fill is not None:
            c = contrast_ratio(b.color, b.box_fill)
            medians.append(c)
            worsts.append(c)
            continue
        px = _region(bg, b)
        if px.size == 0:
            continue
        lum = np.array([relative_luminance((r, g, bl, 1.0)) for r, g, bl in px[:: max(1, len(px) // 4000)]])
        text_l = relative_luminance(b.color)
        # worst case: the background tail nearest the text's luminance
        worst_l = float(np.percentile(lum, 95 if text_l > np.median(lum) else 5))
        med_l = float(np.median(lum))
        medians.append(_lum_ratio(text_l, med_l))
        worsts.append(_lum_ratio(text_l, worst_l))
    rep.metrics["contrast_median"] = round(min(medians), 2) if medians else 0.0
    rep.metrics["contrast_worst"] = round(min(worsts), 2) if worsts else 0.0
    rep.checks["contrast_median_4_5"] = bool(medians) and min(medians) >= 4.5
    rep.checks["contrast_worst_3"] = bool(worsts) and min(worsts) >= 3.0
    bx, by = BADGE_ZONE[0] * width, BADGE_ZONE[1] * height
    rep.checks["clear_of_duration_badge"] = all(b.x + b.w <= bx or b.y + b.h <= by for b in boxes)
    rep.passed = all(rep.checks.values())
    return rep


def phone_preview(png: Path, out_png: Path) -> Path:
    """Side-by-side previews at 2x of the 168 px and 360 px phone placements (for human review)."""
    pic = Picture.from_png(png.read_bytes())
    ratio = pic.height / pic.width
    small = pic.resized(SMALL_W, round(SMALL_W * ratio)).resized(SMALL_W * 2, round(SMALL_W * ratio) * 2)
    feed = pic.resized(FEED_W, round(FEED_W * ratio)).resized(FEED_W * 2, round(FEED_W * ratio) * 2)
    w = small.width + feed.width + 30
    h = max(small.height, feed.height) + 20
    canvas_arr = np.full((h, w, 4), 32, dtype=np.uint8)
    canvas_arr[..., 3] = 255
    canvas_arr[10 : 10 + small.height, 10 : 10 + small.width] = small.to_array()
    canvas_arr[10 : 10 + feed.height, 20 + small.width : 20 + small.width + feed.width] = feed.to_array()
    out_png.write_bytes(Picture.from_array(canvas_arr).to_png())
    return out_png
