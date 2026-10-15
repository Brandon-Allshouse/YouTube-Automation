"""Scene transitions: wipe, zoom, slide, glitch, fade, iris.

Each compositor receives the outgoing scene ``a`` and incoming scene ``b`` as full-frame pictures
and eased progress ``u`` in [0, 1] (may overshoot slightly for springy easings).
"""

from __future__ import annotations

import math

from engine.core.canvas import Canvas, PathBuilder, Picture
from engine.core.color import with_alpha
from engine.core.scene import RenderContext, register_transition


def _clamp(u: float) -> float:
    return max(0.0, min(1.0, u))


@register_transition("fade")
def fade(cv: Canvas, a: Picture, b: Picture, u: float, ctx: RenderContext, seed: int) -> None:
    """Cross-dissolve."""
    cv.picture(a, 0, 0)
    cv.picture(b, 0, 0, alpha=_clamp(u))


@register_transition("wipe")
def wipe(cv: Canvas, a: Picture, b: Picture, u: float, ctx: RenderContext, seed: int) -> None:
    """Angled wipe led by an accent-coloured bar; direction alternates per scene for variety."""
    w, h = ctx.width, ctx.height
    u = _clamp(u)
    slant = 0.18 * h if ctx.layout == "short" else 0.25 * w * 0.3
    direction = 1 if seed % 2 == 0 else -1
    span = w + slant
    edge = -slant + u * (span + slant)
    cv.picture(a, 0, 0)

    def poly(e: float) -> PathBuilder:
        if direction > 0:
            pts = [(0.0, 0.0), (e + slant, 0.0), (e, float(h)), (0.0, float(h))]
        else:
            pts = [(w, 0.0), (w - e - slant, 0.0), (w - e, float(h)), (w, float(h))]
        return PathBuilder().polyline(pts, closed=True)

    bar = 28 * ctx.unit
    with cv.saved():
        cv.clip_path(poly(edge + bar))
        cv.rect(0, 0, w, h, fill=ctx.theme.color("accent"))
    with cv.saved():
        cv.clip_path(poly(edge))
        cv.picture(b, 0, 0)


@register_transition("zoom")
def zoom(cv: Canvas, a: Picture, b: Picture, u: float, ctx: RenderContext, seed: int) -> None:
    """Push through: outgoing scene scales up and blurs away; incoming settles from 0.86x."""
    w, h = ctx.width, ctx.height
    u = _clamp(u)
    sa = 1.0 + 0.6 * u
    cv.picture(
        a,
        w / 2 - w * sa / 2,
        h / 2 - h * sa / 2,
        w * sa,
        h * sa,
        alpha=1 - u,
        blur=18 * u * ctx.unit,
    )
    sb = 0.86 + 0.14 * u
    cv.picture(b, w / 2 - w * sb / 2, h / 2 - h * sb / 2, w * sb, h * sb, alpha=u)


@register_transition("slide")
def slide(cv: Canvas, a: Picture, b: Picture, u: float, ctx: RenderContext, seed: int) -> None:
    """Push: incoming scene slides in, pushing the outgoing one (vertical on Shorts)."""
    w, h = ctx.width, ctx.height
    if ctx.layout == "short":
        off = h * u
        cv.picture(a, 0, -off)
        cv.picture(b, 0, h - off)
    else:
        off = w * u
        cv.picture(a, -off, 0)
        cv.picture(b, w - off, 0)


@register_transition("iris")
def iris(cv: Canvas, a: Picture, b: Picture, u: float, ctx: RenderContext, seed: int) -> None:
    """Circular reveal from centre with an accent ring."""
    w, h = ctx.width, ctx.height
    r = math.hypot(w, h) / 2 * _clamp(u)
    cv.picture(a, 0, 0)
    with cv.saved():
        cv.clip_path(PathBuilder().arc(w / 2, h / 2, max(r, 0.1), 0, 359.9).close())
        cv.picture(b, 0, 0)
    if 0 < u < 1:
        cv.circle(w / 2, h / 2, r, stroke=ctx.theme.color("accent"), stroke_width=14 * ctx.unit)


@register_transition("glitch")
def glitch(cv: Canvas, a: Picture, b: Picture, u: float, ctx: RenderContext, seed: int) -> None:
    """Digital glitch: horizontal slice displacement + RGB split peaking mid-transition."""
    w, h = ctx.width, ctx.height
    u = _clamp(u)
    src = a if u < 0.5 else b
    intensity = math.sin(math.pi * u)
    rng = ctx.rng(f"glitch:{seed}:{int(u * 12)}")  # changes ~12 times across the transition
    cv.picture(src, 0, 0)
    if intensity < 0.02:
        return
    split = 22 * ctx.unit * intensity
    cv.picture(src, -split, 0, channels=(1, 0, 0), blend="screen", alpha=0.85)
    cv.picture(src, split, 0, channels=(0, 0.6, 1), blend="screen", alpha=0.85)
    n = 9
    for _ in range(n):
        sy = float(rng.uniform(0, h))
        sh = float(rng.uniform(12, 90)) * ctx.unit
        dx = float(rng.normal(0, 70)) * intensity * ctx.unit
        cv.picture(src, dx, sy, w, sh, src=(0, sy, w, sh))
    band_y = float(rng.uniform(0.1, 0.9)) * h
    cv.rect(0, band_y, w, 6 * ctx.unit, fill=with_alpha(ctx.theme.color("accent"), 0.8 * intensity))
