"""Ambient and iconographic components: ParticleBackground and IconScene.

Particles are stateless: every particle's position is an analytic function of time and its
seeded parameters, so any frame renders identically in any worker. Icons are vector drawings made
in code (no icon fonts or image assets), drawn on with a stroke trim, then given a gentle idle
motion so they never sit static.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np

from engine.components.base import entrance, exit_alpha, region_rect
from engine.core.canvas import Canvas, PathBuilder
from engine.core.color import RGBA, with_alpha
from engine.core.easing import get_easing
from engine.core.scene import Node, RenderContext
from engine.schema.spec import IconSceneSpec, ParticleBackgroundSpec


class ParticleBackground(Node):
    """Deterministic ambient particle field."""

    def __init__(self, spec: ParticleBackgroundSpec, ctx: RenderContext) -> None:
        super().__init__(start=spec.start, end=spec.end, key="ParticleBackground")
        self.spec = spec
        self.box = region_rect(ctx, spec.region)
        rng = ctx.rng(f"particles:{spec.style}:{spec.count}")
        n = spec.count
        self.p0 = rng.random((n, 2))
        self.size = rng.uniform(0.6, 1.0, n) ** 2
        self.phase = rng.uniform(0, 2 * math.pi, n)
        speeds = {
            "dust": (0.004, 0.012),
            "embers": (0.03, 0.07),
            "stars": (0.0, 0.0),
            "snow": (0.02, 0.05),
        }[spec.style]
        self.speed = rng.uniform(*speeds, n)
        self.depth = rng.uniform(0.3, 1.0, n)

    def render(self, cv: Canvas, t: float, ctx: RenderContext) -> None:
        """Draw particles at time ``t``."""
        th = ctx.theme
        s = self.spec
        u = ctx.unit
        fade = exit_alpha(ctx, t + self.start, self.end) * min(t / 0.8, 1.0)
        if fade <= 0:
            return
        b = self.box
        if s.style == "embers":
            dy = -self.speed * t
            dx = 0.01 * np.sin(t * 1.3 + self.phase)
            col: RGBA = th.color("accent")
        elif s.style == "snow":
            dy = self.speed * t
            dx = 0.015 * np.sin(t * 0.8 + self.phase)
            col = th.color("text")
        else:
            dy = -self.speed * t
            dx = 0.006 * np.sin(t * 0.5 + self.phase)
            col = th.color("text") if s.style == "stars" else th.color("highlight")
        xs = b.x + ((self.p0[:, 0] + dx) % 1.0) * b.w
        ys = b.y + ((self.p0[:, 1] + dy) % 1.0) * b.h
        tw = 0.55 + 0.45 * np.sin(t * (2.2 if s.style == "stars" else 1.1) + self.phase)
        if s.style == "embers":
            tw = tw * (0.6 + 0.4 * np.sin(t * 9 + self.phase * 3))
        for x, y, sz, a, d in zip(
            xs.tolist(),
            ys.tolist(),
            self.size.tolist(),
            tw.tolist(),
            self.depth.tolist(),
            strict=True,
        ):
            r = (2.0 + 5.0 * sz) * u * d
            alpha = max(0.0, min(1.0, a)) * fade * (0.35 + 0.5 * d)
            cv.circle(x, y, r * 2.4, fill=with_alpha(col, alpha * 0.18), blur=r)
            cv.circle(x, y, r, fill=with_alpha(col, alpha * 0.85))


# --- icons: each draws into a unit box centred on (0, 0), size ~2 (from -1 to 1) --------------
IconFn = Callable[[float], list[tuple[PathBuilder, bool]]]  # (path, filled) at idle time


def _globe(t: float) -> list[tuple[PathBuilder, bool]]:
    out = [(PathBuilder().arc(0, 0, 1, 0, 359.99).close(), False)]
    for k in (-0.5, 0.0, 0.5):
        out.append(
            (
                PathBuilder().move_to(-math.sqrt(1 - k * k), k).line_to(math.sqrt(1 - k * k), k),
                False,
            )
        )
    spin = (t * 0.25) % 1.0
    for j in range(3):
        f = ((j / 3 + spin) % 1.0) * 2 - 1  # -1..1 sweeping meridians
        pts = [(f * math.cos(a), math.sin(a)) for a in np.linspace(-math.pi / 2, math.pi / 2, 30)]
        out.append((PathBuilder().polyline(pts), False))
    return out


def _clock(t: float) -> list[tuple[PathBuilder, bool]]:
    a1 = t * 2.0
    a2 = t * 0.4
    return [
        (PathBuilder().arc(0, 0, 1, 0, 359.99).close(), False),
        (PathBuilder().move_to(0, 0).line_to(0.7 * math.sin(a1), -0.7 * math.cos(a1)), False),
        (PathBuilder().move_to(0, 0).line_to(0.45 * math.sin(a2), -0.45 * math.cos(a2)), False),
    ] + [
        (
            PathBuilder()
            .move_to(0.85 * math.sin(k * math.pi / 6), -0.85 * math.cos(k * math.pi / 6))
            .line_to(0.95 * math.sin(k * math.pi / 6), -0.95 * math.cos(k * math.pi / 6)),
            False,
        )
        for k in range(12)
    ]


def _bolt(t: float) -> list[tuple[PathBuilder, bool]]:
    pts = [(0.15, -1.0), (-0.55, 0.12), (-0.05, 0.12), (-0.2, 1.0), (0.55, -0.15), (0.05, -0.15)]
    return [(PathBuilder().polyline(pts, closed=True), True)]


def _ship(t: float) -> list[tuple[PathBuilder, bool]]:
    bob = 0.04 * math.sin(t * 2.0)
    hull = PathBuilder().polyline(
        [(-1.0, 0.35 + bob), (1.0, 0.35 + bob), (0.7, 0.8 + bob), (-0.7, 0.8 + bob)], closed=True
    )
    mast = PathBuilder().move_to(0, 0.35 + bob).line_to(0, -1.0 + bob)
    sail1 = PathBuilder().polyline([(0.05, -0.9 + bob), (0.75, 0.2 + bob), (0.05, 0.2 + bob)], closed=True)
    sail2 = PathBuilder().polyline([(-0.05, -0.7 + bob), (-0.6, 0.2 + bob), (-0.05, 0.2 + bob)], closed=True)
    wave = PathBuilder().smooth([(-1.1 + 0.2 * k, 0.95 + 0.06 * math.sin(k * 1.6 + t * 3)) for k in range(12)])
    return [(hull, True), (mast, False), (sail1, False), (sail2, False), (wave, False)]


def _mountain(t: float) -> list[tuple[PathBuilder, bool]]:
    return [
        (
            PathBuilder().polyline([(-1, 0.8), (-0.25, -0.75), (0.2, 0.05), (0.45, -0.3), (1, 0.8)], closed=True),
            False,
        ),
        (
            PathBuilder().polyline(
                [
                    (-0.48, -0.27),
                    (-0.25, -0.75),
                    (-0.02, -0.27),
                    (-0.15, -0.35),
                    (-0.25, -0.25),
                    (-0.36, -0.35),
                ],
                closed=True,
            ),
            True,
        ),
    ]


def _atom(t: float) -> list[tuple[PathBuilder, bool]]:
    out: list[tuple[PathBuilder, bool]] = []
    for k in range(3):
        rot = k * math.pi / 3
        pts = [
            (
                math.cos(a) * math.cos(rot) - 0.38 * math.sin(a) * math.sin(rot),
                math.cos(a) * math.sin(rot) + 0.38 * math.sin(a) * math.cos(rot),
            )
            for a in np.linspace(0, 2 * math.pi, 60)
        ]
        out.append((PathBuilder().polyline(pts, closed=True), False))
        a = t * (1.6 + 0.3 * k) + k
        ex, ey = (
            math.cos(a) * math.cos(rot) - 0.38 * math.sin(a) * math.sin(rot),
            math.cos(a) * math.sin(rot) + 0.38 * math.sin(a) * math.cos(rot),
        )
        out.append((PathBuilder().arc(ex, ey, 0.08, 0, 359.99).close(), True))
    out.append((PathBuilder().arc(0, 0, 0.16, 0, 359.99).close(), True))
    return out


def _chart(t: float) -> list[tuple[PathBuilder, bool]]:
    hs = [0.5, 0.9, 0.7, 1.4]
    out = [(PathBuilder().move_to(-1, 0.9).line_to(1, 0.9), False)]
    for i, h in enumerate(hs):
        x = -0.8 + i * 0.5
        hh = h * (0.9 + 0.1 * math.sin(t * 1.5 + i))
        out.append(
            (
                PathBuilder().polyline([(x, 0.9), (x, 0.9 - hh), (x + 0.32, 0.9 - hh), (x + 0.32, 0.9)], closed=True),
                True,
            )
        )
    return out


def _warning(t: float) -> list[tuple[PathBuilder, bool]]:
    return [
        (PathBuilder().polyline([(0, -1), (1, 0.8), (-1, 0.8)], closed=True), False),
        (PathBuilder().move_to(0, -0.35).line_to(0, 0.25), False),
        (PathBuilder().arc(0, 0.52, 0.07, 0, 359.99).close(), True),
    ]


def _coin(t: float) -> list[tuple[PathBuilder, bool]]:
    sx = abs(math.cos(t * 1.2)) * 0.9 + 0.1
    pts = [(sx * math.cos(a), math.sin(a)) for a in np.linspace(0, 2 * math.pi, 60)]
    inner = [(0.75 * sx * math.cos(a), 0.75 * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 60)]
    return [
        (PathBuilder().polyline(pts, closed=True), False),
        (PathBuilder().polyline(inner, closed=True), False),
    ]


def _crown(t: float) -> list[tuple[PathBuilder, bool]]:
    return [
        (
            PathBuilder().polyline(
                [(-1, 0.6), (-1, -0.5), (-0.5, 0.05), (0, -0.8), (0.5, 0.05), (1, -0.5), (1, 0.6)],
                closed=True,
            ),
            False,
        ),
        (PathBuilder().move_to(-1, 0.8).line_to(1, 0.8), False),
    ]


def _book(t: float) -> list[tuple[PathBuilder, bool]]:
    return [
        (PathBuilder().polyline([(0, -0.6), (-1, -0.8), (-1, 0.7), (0, 0.9)], closed=False), False),
        (PathBuilder().polyline([(0, -0.6), (1, -0.8), (1, 0.7), (0, 0.9)], closed=False), False),
        (PathBuilder().move_to(0, -0.6).line_to(0, 0.9), False),
    ]


def _flame(t: float) -> list[tuple[PathBuilder, bool]]:
    w = 0.08 * math.sin(t * 7)
    outer = (
        PathBuilder()
        .move_to(0, 1)
        .cubic_to(-0.9, 0.9, -0.8, -0.1, 0.0 + w, -1.0)
        .cubic_to(0.2, -0.4, 0.9, -0.2, 0.7, 0.4)
        .cubic_to(0.6, 0.85, 0.3, 1, 0, 1)
        .close()
    )
    inner = (
        PathBuilder()
        .move_to(0, 1)
        .cubic_to(-0.4, 0.95, -0.35, 0.4, 0.05 - w, 0.0)
        .cubic_to(0.3, 0.4, 0.4, 0.8, 0, 1)
        .close()
    )
    return [(outer, False), (inner, True)]


ICONS: dict[str, IconFn] = {
    "globe": _globe, "clock": _clock, "bolt": _bolt, "ship": _ship, "mountain": _mountain,
    "atom": _atom, "chart": _chart, "warning": _warning, "coin": _coin, "crown": _crown,
    "book": _book, "flame": _flame,
}  # fmt: skip


class IconScene(Node):
    """A big code-drawn icon with a label; draws on, then idles."""

    def __init__(self, spec: IconSceneSpec, ctx: RenderContext) -> None:
        super().__init__(start=spec.start, end=spec.end, key="IconScene")
        self.spec = spec
        u = ctx.unit
        self.box = region_rect(ctx, spec.region)
        self.size = min(self.box.w, self.box.h) * (0.30 if spec.label else 0.36)
        self.label_font = ctx.theme.fonts.display.spec(76 * u)

    def render(self, cv: Canvas, t: float, ctx: RenderContext) -> None:
        """Draw the icon and label."""
        th = ctx.theme
        u = ctx.unit
        s = self.spec
        fade = exit_alpha(ctx, t + self.start, self.end)
        m = entrance(ctx, t, 0.0, "pop", 0.6)
        if fade <= 0 or m.alpha <= 0:
            return
        draw = get_easing("in_out_cubic")(min(t / 1.0, 1.0))
        col = th.color("accent") if s.accent else th.color("text")
        cx, cy = self.box.cx, self.box.cy - (60 * u if s.label else 0)
        with cv.layer(alpha=fade), cv.saved():
            cv.translate(cx, cy)
            cv.scale(self.size * m.scale)
            sw = 0.075
            cv.circle(0, 0, 1.45, fill=with_alpha(col, 0.10 * m.alpha), blur=0.25)
            for path, filled in ICONS[s.icon](t):
                if filled:
                    cv.path(path, fill=with_alpha(col, draw * m.alpha))
                else:
                    cv.path(
                        path,
                        stroke=with_alpha(col, 0.35 * m.alpha),
                        stroke_width=sw * 2.4,
                        trim=(0, draw),
                        blur=0.04,
                    )
                    cv.path(path, stroke=with_alpha(col, m.alpha), stroke_width=sw, trim=(0, draw))
        if s.label:
            lm = entrance(ctx, t, 0.35, "rise")
            cv.text(
                s.label,
                cx,
                cy + self.size * 1.35 + 40 * u + lm.dy,
                self.label_font,
                with_alpha(th.color("text"), lm.alpha * fade),
                align="center",
                shadow=(0, 6 * u, 14 * u, (0, 0, 0, 0.5)),
            )
