"""Themed animated backgrounds.

Static parts (gradient, pattern, vignette) are rendered once per worker and cached; animated parts
(drifting light pools, film grain) are cheap per frame. Grain cycles through a few pre-generated
noise plates at 12 fps so it reads as film texture, not static.
"""

from __future__ import annotations

import math

import numpy as np

from engine.core.canvas import Canvas, PathBuilder, Picture, Surface
from engine.core.color import mix, with_alpha
from engine.core.scene import Node, RenderContext

GRAIN_PLATES = 4
GRAIN_FPS = 12


def _base_picture(ctx: RenderContext) -> Picture:
    th = ctx.theme
    w, h = ctx.width, ctx.height
    surf = Surface(w, h, ctx.fonts)
    cv = surf.canvas
    bg, bg2 = th.color("bg"), th.color("bg2")
    cv.gradient_rect(0, 0, w, h, [bg2, bg, mix(bg, (0, 0, 0, 1), 0.35)], angle_deg=90, stops=[0.0, 0.55, 1.0])
    kind = th.background.kind
    u = ctx.unit
    if kind == "grid":
        _grid(cv, ctx)
    elif kind == "paper":
        rng = ctx.rng("paper-fibres")
        fibre = with_alpha(th.color("text"), 0.025)
        for _ in range(int(260 * (w * h) / (1080 * 1920))):
            x, y = float(rng.uniform(0, w)), float(rng.uniform(0, h))
            ang = float(rng.uniform(0, math.pi))
            ln = float(rng.uniform(20, 120)) * u
            cv.line(
                x,
                y,
                x + math.cos(ang) * ln,
                y + math.sin(ang) * ln,
                fibre,
                float(rng.uniform(0.6, 1.6)) * u,
            )
        # faint latitude/longitude hairlines: an atlas motif
        hair = with_alpha(th.color("accent"), 0.06)
        for i in range(1, 9):
            y = h * i / 9
            cv.line(0, y, w, y, hair, 1.2 * u)
        for i in range(1, 6):
            x = w * i / 6
            cv.line(x, 0, x, h, hair, 1.2 * u)
    elif kind == "aurora":
        rng = ctx.rng("aurora")
        for i in range(4):
            col = th.palette.series_rgba(i)
            cv.radial_glow(
                float(rng.uniform(0, w)),
                float(rng.uniform(0, h)),
                float(rng.uniform(0.4, 0.8)) * max(w, h),
                with_alpha(col, 0.18),
            )
    return surf.snapshot()


def _grid(cv: Canvas, ctx: RenderContext) -> None:
    th = ctx.theme
    w, h = ctx.width, ctx.height
    u = ctx.unit
    line = with_alpha(th.color("accent"), 0.07)
    step = 72 * u
    x = (w % step) / 2
    while x < w:
        cv.line(x, 0, x, h, line, 1.3 * u)
        x += step
    y = (h % step) / 2
    while y < h:
        cv.line(0, y, w, y, line, 1.3 * u)
        y += step
    # perspective floor in the lower third: a neon horizon
    horizon = h * (0.80 if ctx.layout == "short" else 0.74)
    floor = with_alpha(th.color("accent2"), 0.16)
    cv.gradient_rect(
        0,
        horizon,
        w,
        h - horizon,
        [with_alpha(th.color("accent2"), 0.10), (0, 0, 0, 0)],
        angle_deg=90,
    )
    cx = w / 2
    for i in range(-14, 15):
        cv.line(cx + i * step * 0.6, horizon, cx + i * step * 5.0, h, floor, 1.6 * u)
    for k in range(1, 9):
        yy = horizon + (h - horizon) * (k / 8) ** 1.8
        cv.line(0, yy, w, yy, floor, 1.6 * u)


def _vignette_picture(ctx: RenderContext) -> Picture:
    w, h = ctx.width, ctx.height
    surf = Surface(w, h, ctx.fonts)
    strength = ctx.theme.background.vignette
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx = (xx - w / 2) / (w / 2)
    dy = (yy - h / 2) / (h / 2)
    d = np.sqrt(dx * dx * 0.9 + dy * dy * 0.6)
    a = np.clip((d - 0.55) / 0.85, 0, 1) ** 1.6 * strength
    arr = surf.array
    arr[..., 0:3] = 0
    arr[..., 3] = (a * 255).astype(np.uint8)
    return Picture.from_array(arr.copy())


def _grain_plate(ctx: RenderContext, i: int) -> Picture:
    w, h = ctx.width, ctx.height
    rng = ctx.rng(f"grain-{i}")
    amount = ctx.theme.background.grain
    noise = rng.normal(0.0, 1.0, (h, w)).astype(np.float32)
    a = np.clip(np.abs(noise) * amount * 0.9, 0, 1)
    light = noise > 0
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    # premultiplied RGBA: white specks and black specks
    alpha = (a * 255).astype(np.uint8)
    arr[..., 3] = alpha
    arr[..., 0] = np.where(light, alpha, 0)
    arr[..., 1] = arr[..., 0]
    arr[..., 2] = arr[..., 0]
    return Picture.from_array(arr)


class ThemedBackground(Node):
    """Full-frame background in the theme's treatment, alive but never distracting."""

    def __init__(
        self,
        scene_key: str = "bg",
        glow_positions: tuple[tuple[float, float], ...] | None = None,
        **kw: object,
    ) -> None:
        super().__init__(**kw)  # type: ignore[arg-type]
        self.scene_key = scene_key
        self.glows = glow_positions or ((0.2, 0.25), (0.85, 0.7))

    def render(self, cv: Canvas, t: float, ctx: RenderContext) -> None:
        """Draw cached base, drifting glows, cached vignette, cycling grain."""
        cache = ctx.cache
        base = cache.get("bg:base")
        if base is None:
            base = cache["bg:base"] = _base_picture(ctx)
        cv.picture(base, 0, 0)
        th = ctx.theme
        w, h = ctx.width, ctx.height
        glow = th.background.glow
        if glow > 0:
            for i, (gx, gy) in enumerate(self.glows):
                col = th.color("accent") if i % 2 == 0 else th.color("accent2")
                ph = ctx.rng(f"{self.scene_key}:glow{i}").uniform(0, 2 * math.pi)
                x = (gx + 0.05 * math.sin(t * 0.35 + ph)) * w
                y = (gy + 0.04 * math.cos(t * 0.27 + ph)) * h
                cv.radial_glow(
                    x,
                    y,
                    0.55 * max(w, h) * (1 + 0.06 * math.sin(t * 0.5 + ph)),
                    with_alpha(col, 0.22 * glow),
                    blend="screen",
                )
        vig = cache.get("bg:vignette")
        if vig is None:
            vig = cache["bg:vignette"] = _vignette_picture(ctx)
        cv.picture(vig, 0, 0)
        if th.background.grain > 0:
            idx = int(t * GRAIN_FPS) % GRAIN_PLATES
            key = f"bg:grain{idx}"
            plate = cache.get(key)
            if plate is None:
                plate = cache[key] = _grain_plate(ctx, idx)
            cv.picture(plate, 0, 0, blend="overlay")


def accent_rule(cv: Canvas, x: float, y: float, length: float, ctx: RenderContext, progress: float = 1.0) -> None:
    """A short accent underline that draws on with ``progress`` (shared motif)."""
    if progress <= 0:
        return
    p = PathBuilder().move_to(x, y).line_to(x + length, y)
    cv.path(
        p,
        stroke=ctx.theme.color("accent"),
        stroke_width=8 * ctx.unit,
        trim=(0, min(progress, 1.0)),
        cap="round",
    )
