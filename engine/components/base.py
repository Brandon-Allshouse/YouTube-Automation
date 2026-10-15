"""Shared component helpers: content regions, entrance/exit motion, text casing.

Regions are computed inside the safe area *and* above the caption band, so on Shorts visuals live
in the upper ~half while captions own the lower-middle band, and nothing touches platform UI.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.core.easing import get_easing
from engine.core.scene import Rect, RenderContext
from engine.core.theme import FontRole


def content_rect(ctx: RenderContext) -> Rect:
    """Safe area minus the caption band, shrunk so the scene's camera push-in can't carry content out.

    Scenes scale up by ``1 + camera_drift`` about the frame centre over their duration; a point p
    ends at c + (p - c)(1 + drift). Mapping the safe edges back through that transform gives the
    largest region whose content stays inside the safe zone for the whole shot.
    """
    s = ctx.safe
    if ctx.layout == "short":
        bottom = ctx.height * (ctx.theme.captions.y - 0.085)
    else:
        bottom = ctx.height * (ctx.theme.captions.y_long - 0.07)
    k = 1.0 + ctx.theme.motion.camera_drift
    cx, cy = ctx.width / 2, ctx.height / 2
    x0, x1 = cx + (s.x - cx) / k, cx + (s.right - cx) / k
    y0 = cy + (s.y - cy) / k
    y1 = cy + (max(bottom, s.y + s.h * 0.4) - cy) / k
    return Rect(x0, y0, x1 - x0, y1 - y0)


def region_rect(ctx: RenderContext, region: str) -> Rect:
    """Named layout region (same spec, different geometry per format)."""
    c = content_rect(ctx)
    if region == "full":
        return Rect(0, 0, ctx.width, ctx.height)
    if region == "center":
        return c
    if region == "upper":
        return c.sub(0, 0, 1, 0.55)
    if region == "lower":
        return c.sub(0, 0.45, 1, 0.55)
    if region == "top":
        return c.sub(0, 0, 1, 0.3)
    if ctx.layout == "long":
        if region == "left":
            return c.sub(0, 0, 0.48, 1)
        if region == "right":
            return c.sub(0.52, 0, 0.48, 1)
    # Shorts have no room for side-by-side: left/right stack vertically.
    if region == "left":
        return c.sub(0, 0, 1, 0.5)
    if region == "right":
        return c.sub(0, 0.5, 1, 0.5)
    raise ValueError(f"unknown region {region}")


@dataclass(frozen=True)
class Motion:
    """Resolved entrance state at a moment."""

    alpha: float
    dx: float
    dy: float
    scale: float
    progress: float  # eased 0..1 (may overshoot)


def entrance(ctx: RenderContext, t: float, start: float, kind: str = "rise", duration: float | None = None) -> Motion:
    """Entrance motion in the theme's motion language.

    ``rise``: fade + travel up. ``pop``: scale from 0.6 with overshoot. ``slide``: from the left.
    ``fade``: opacity only.
    """
    m = ctx.theme.motion
    d = duration if duration is not None else m.enter_duration
    raw = (t - start) / d if d > 0 else 1.0
    if raw <= 0:
        return Motion(0.0, 0.0, 0.0, 1.0, 0.0)
    raw = min(raw, 1.0)
    ease = get_easing(m.emphasis if kind == "pop" else m.enter)
    p = ease(raw)
    alpha = min(1.0, raw / 0.45)  # opacity lands quickly; motion carries the gesture
    rise = m.rise * ctx.unit
    if kind == "rise":
        return Motion(alpha, 0.0, (1 - p) * rise, 1.0, p)
    if kind == "pop":
        return Motion(alpha, 0.0, 0.0, 0.6 + 0.4 * p, p)
    if kind == "slide":
        return Motion(alpha, -(1 - p) * rise * 2.0, 0.0, 1.0, p)
    return Motion(alpha, 0.0, 0.0, 1.0, p)


def exit_alpha(ctx: RenderContext, t: float, end: float | None, duration: float = 0.3) -> float:
    """Opacity multiplier for a fade-out ending at ``end`` (1.0 if no end)."""
    if end is None:
        return 1.0
    u = (end - t) / duration
    if u >= 1:
        return 1.0
    if u <= 0:
        return 0.0
    return get_easing("out_cubic")(u)


def cased(text: str, role: FontRole) -> str:
    """Apply the role's casing."""
    return text.upper() if role.uppercase else text


def norm_word(w: str) -> str:
    """Lower-case word with surrounding punctuation stripped (for emphasis matching)."""
    return w.strip(".,;:!?\"'“”‘’()[]—–-").lower()
