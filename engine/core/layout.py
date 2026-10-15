"""Formats, safe zones and render-context construction.

Formats: 9:16 Shorts (1080x1920) and 16:9 long-form (1920x1080) from the same specs. Safe zones
come from ``config/rules.yaml`` and keep text clear of platform UI (Shorts: top ~10 %,
bottom ~20 %, right-edge buttons).
"""

from __future__ import annotations

from engine.core.canvas import Fonts
from engine.core.scene import Layout, Rect, RenderContext
from engine.core.theme import Theme, load_theme
from src.common.config import rules

FORMATS: dict[str, tuple[int, int]] = {"short": (1080, 1920), "long": (1920, 1080)}


def safe_rect(layout: Layout, width: int, height: int) -> Rect:
    """The region where text and key visuals may go for ``layout``."""
    q = rules().quality
    z = q.safe_zone_short if layout == "short" else q.safe_zone_long
    x0, y0 = z.left * width, z.top * height
    return Rect(x0, y0, width * (1 - z.left - z.right), height * (1 - z.top - z.bottom))


def make_context(
    theme: Theme | str,
    layout: Layout = "short",
    *,
    fps: int = 30,
    seed: int = 0,
    scale: float = 1.0,
    duration: float = 0.0,
) -> RenderContext:
    """Build a :class:`RenderContext` for a format (``scale`` < 1 renders fast previews)."""
    th = load_theme(theme) if isinstance(theme, str) else theme
    w, h = FORMATS[layout]
    w, h = int(round(w * scale / 2) * 2), int(round(h * scale / 2) * 2)
    return RenderContext(
        width=w,
        height=h,
        fps=fps,
        layout=layout,
        theme=th,
        fonts=Fonts(),
        seed=seed,
        safe=safe_rect(layout, w, h),
        duration=duration,
    )
