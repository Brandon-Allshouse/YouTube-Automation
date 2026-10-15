"""Step-2 engine demo: a 5-second clip exercising easing, stagger, path drawing and a transition.

Run: ``python -m engine.demo_core [--theme neon] [--out output/demo/engine_core.mp4]``
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from engine.components.background import ThemedBackground
from engine.core.canvas import Canvas, PathBuilder
from engine.core.color import with_alpha
from engine.core.layout import make_context
from engine.core.render import render_video
from engine.core.scene import Composition, FnNode, RenderContext, Scene, TransitionSpec
from engine.core.timeline import stagger, tween

EASINGS = (
    "out_cubic",
    "out_expo",
    "back_out",
    "spring",
    "spring_bouncy",
    "elastic_out",
    "bounce_out",
)


def _easing_rows(cv: Canvas, t: float, c: RenderContext) -> None:
    starts = stagger(len(EASINGS), 0.07, start=0.2)
    top = c.safe.y + c.safe.h * 0.10
    gap = c.safe.h * 0.78 / len(EASINGS)
    x0, x1 = c.safe.x + 70 * c.unit, c.safe.right - 70 * c.unit
    for i, (name, st) in enumerate(zip(EASINGS, starts, strict=True)):
        y = top + i * gap
        u = tween(st, 1.3, 0.0, 1.0, name).scalar_at(t)
        fade = min(max((t - st + 0.2) / 0.2, 0.0), 1.0)
        muted = with_alpha(c.theme.color("text_muted"), fade)
        cv.text(name, x0, y - 40 * c.unit, c.theme.fonts.mono.spec(34 * c.unit), muted)
        cv.line(x0, y, x1, y, with_alpha(c.theme.color("text"), 0.14 * fade), 4 * c.unit)
        col = c.theme.palette.series_rgba(i)
        cx = x0 + (x1 - x0) * u
        cv.circle(cx, y, 34 * c.unit, fill=with_alpha(col, 0.25 * fade), blur=10 * c.unit)
        cv.circle(cx, y, 22 * c.unit, fill=with_alpha(col, fade))


def _path_draw(cv: Canvas, t: float, c: RenderContext) -> None:
    cx, cy = c.width / 2, c.height * 0.45
    pts = []
    for i in range(181):
        a = i * 2 * math.pi / 180
        r = (300 + 90 * math.sin(5 * a)) * c.unit
        pts.append((cx + math.cos(a) * r, cy + math.sin(a) * r))
    p = PathBuilder().smooth(pts)
    prog = tween(0.1, 1.6, 0.0, 1.0, "in_out_cubic").scalar_at(t)
    glow = with_alpha(c.theme.color("accent"), 0.25)
    cv.path(p, stroke=glow, stroke_width=26 * c.unit, trim=(0, prog), blur=12 * c.unit)
    cv.path(p, stroke=c.theme.color("accent"), stroke_width=9 * c.unit, trim=(0, prog))
    pop = tween(1.0, 0.8, 0.0, 1.0, c.theme.motion.emphasis).scalar_at(t)
    with cv.saved():
        cv.translate(cx, cy)
        cv.scale(max(pop, 0.0001))
        font = c.theme.fonts.display.spec(150 * c.unit)
        cv.text("ENGINE", 0, 0, font, c.theme.color("text"), align="center", baseline="middle")
    sub = tween(1.4, 0.6, 0.0, 1.0, c.theme.motion.enter).scalar_at(t)
    cv.text(
        "easing · stagger · paths",
        cx,
        cy + 150 * c.unit + (1 - sub) * 40 * c.unit,
        c.theme.fonts.body.spec(46 * c.unit),
        with_alpha(c.theme.color("text_muted"), sub),
        align="center",
    )


def build(theme: str = "neon", seed: int = 7, scale: float = 1.0) -> tuple[Composition, RenderContext]:
    """Construct the demo composition."""
    ctx = make_context(theme, "short", fps=30, seed=seed, scale=scale, duration=5.0)
    th = ctx.theme
    s1 = Scene(
        start=0.0,
        duration=2.6,
        nodes=[FnNode(_easing_rows)],
        background=ThemedBackground("s1"),
        key="s1",
    )
    s2 = Scene(
        start=2.6,
        duration=2.4,
        nodes=[FnNode(_path_draw)],
        background=ThemedBackground("s2"),
        transition_in=TransitionSpec(th.transition.kind, th.transition.duration, th.transition.easing),
        camera_drift=th.motion.camera_drift,
        key="s2",
    )
    comp = Composition(width=ctx.width, height=ctx.height, fps=30, duration=5.0, scenes=[s1, s2])
    return comp, ctx


def main() -> None:
    """CLI entry point."""
    p = argparse.ArgumentParser(description="Render the engine core demo clip")
    p.add_argument("--theme", default="neon")
    p.add_argument("--out", type=Path, default=Path("output/demo/engine_core.mp4"))
    p.add_argument("--workers", type=int, default=None)
    a = p.parse_args()
    print(render_video(build, (a.theme, 7, 1.0), a.out, workers=a.workers))


if __name__ == "__main__":
    main()
