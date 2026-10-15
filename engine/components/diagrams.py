"""Structure components: Timeline and DiagramBuild.

Timeline draws its spine progressively; events pop in along it (vertical on Shorts, horizontal on
long-form). DiagramBuild pops nodes in and draws curved arrows between them step by step.
"""

from __future__ import annotations

import math

from engine.components.base import entrance, exit_alpha, region_rect
from engine.core.canvas import Canvas, PathBuilder
from engine.core.color import with_alpha
from engine.core.easing import get_easing
from engine.core.scene import Node, RenderContext
from engine.core.text import fit_text
from engine.schema.spec import DiagramBuildSpec, TimelineSpec


class Timeline(Node):
    """Dated events along a drawing spine."""

    def __init__(self, spec: TimelineSpec, ctx: RenderContext) -> None:
        super().__init__(start=spec.start, end=spec.end, key="Timeline")
        self.spec = spec
        u = ctx.unit
        self.box = region_rect(ctx, spec.region).inset(30 * u, 20 * u)
        self.vertical = ctx.layout == "short"
        self.times = [0.3 + i * spec.interval for i in range(len(spec.events))]
        th = ctx.theme
        n = len(spec.events)
        if self.vertical:
            slot = self.box.h / n
            self.label_blocks = [
                fit_text(
                    e.label,
                    ctx.fonts,
                    th.fonts.body.spec(min(slot * 0.34, 58 * u)),
                    self.box.w * 0.64,
                    slot * 0.7,
                    max_lines=2,
                    min_size=30 * u,
                )
                for e in spec.events
            ]
        else:
            slot = self.box.w / n
            self.label_blocks = [
                fit_text(
                    e.label,
                    ctx.fonts,
                    th.fonts.body.spec(48 * u),
                    slot * 0.9,
                    self.box.h * 0.32,
                    max_lines=3,
                    min_size=26 * u,
                )
                for e in spec.events
            ]
        self.slot = slot

    def _pos(self, i: int) -> tuple[float, float]:
        if self.vertical:
            return self.box.x + self.box.w * 0.3, self.box.y + self.slot * (i + 0.5)
        return self.box.x + self.slot * (i + 0.5), self.box.cy

    def render(self, cv: Canvas, t: float, ctx: RenderContext) -> None:
        """Draw spine, dots, dates and labels."""
        th = ctx.theme
        u = ctx.unit
        s = self.spec
        fade = exit_alpha(ctx, t + self.start, self.end)
        if fade <= 0:
            return
        n = len(s.events)
        last = self.times[-1] + 0.4
        prog = get_easing("in_out_cubic")(min(max(t / last, 0.0), 1.0))
        x0, y0 = self._pos(0)
        x1, y1 = self._pos(n - 1)
        with cv.layer(alpha=fade):
            spine = PathBuilder().move_to(x0, y0).line_to(x1, y1)
            cv.path(spine, stroke=with_alpha(th.color("text"), 0.15), stroke_width=6 * u)
            cv.path(spine, stroke=th.color("accent"), stroke_width=6 * u, trim=(0, prog))
            current = max((i for i, tt in enumerate(self.times) if t >= tt), default=-1)
            for i, ev in enumerate(s.events):
                if t < self.times[i]:
                    continue
                x, y = self._pos(i)
                pm = entrance(ctx, t, self.times[i], "pop", 0.45)
                hl = i == s.highlight or (s.highlight < 0 and i == current)
                r = (20 if hl else 14) * u * pm.scale
                if hl:
                    cv.circle(x, y, r * 2.2, fill=with_alpha(th.color("accent"), 0.18 * pm.alpha))
                cv.circle(x, y, r, fill=th.color("accent") if hl else th.color("text"))
                dm = entrance(ctx, t, self.times[i] + 0.06, "rise", 0.5)
                blk = self.label_blocks[i]
                dfont = th.fonts.display.spec((84 if hl else 68) * u)
                dim = 1.0 if hl else 0.75
                if self.vertical:
                    tx = x + 50 * u
                    cv.text(
                        ev.date,
                        x - 46 * u,
                        y + dm.dy * 0.4,
                        dfont,
                        with_alpha(th.color("accent") if hl else th.color("text"), dm.alpha * dim),
                        align="right",
                        baseline="middle",
                    )
                    ty = y - blk.height / 2 + dm.dy * 0.4
                    for ln in blk.lines:
                        cv.text(
                            " ".join(w.text for w in ln.words),
                            tx,
                            ty + blk.line_height * 0.78,
                            blk.font,
                            with_alpha(th.color("text"), dm.alpha * dim),
                        )
                        ty += blk.line_height
                else:
                    cv.text(
                        ev.date,
                        x,
                        y - 50 * u + dm.dy * 0.4,
                        dfont,
                        with_alpha(th.color("accent") if hl else th.color("text"), dm.alpha * dim),
                        align="center",
                    )
                    ty = y + 60 * u + dm.dy * 0.4
                    for ln in blk.lines:
                        cv.text(
                            " ".join(w.text for w in ln.words),
                            x,
                            ty + blk.line_height * 0.78,
                            blk.font,
                            with_alpha(th.color("text"), dm.alpha * dim),
                            align="center",
                        )
                        ty += blk.line_height


class DiagramBuild(Node):
    """Nodes and arrows that build step by step."""

    def __init__(self, spec: DiagramBuildSpec, ctx: RenderContext) -> None:
        super().__init__(start=spec.start, end=spec.end, key="DiagramBuild")
        self.spec = spec
        u = ctx.unit
        self.box = region_rect(ctx, spec.region).inset(60 * u, 40 * u)
        th = ctx.theme
        self.font = th.fonts.body.spec(58 * u)
        self.sizes: dict[str, tuple[float, float]] = {}
        self.blocks = {}
        for nd in spec.nodes:
            blk = fit_text(nd.label, ctx.fonts, self.font, 440 * u, 170 * u, max_lines=2, min_size=30 * u)
            self.blocks[nd.id] = blk
            self.sizes[nd.id] = (blk.width + 64 * u, blk.height + 44 * u)
        self.by_id = {nd.id: nd for nd in spec.nodes}

    def _center(self, node_id: str) -> tuple[float, float]:
        nd = self.by_id[node_id]
        return self.box.x + nd.x * self.box.w, self.box.y + nd.y * self.box.h

    def _edge_point(self, node_id: str, toward: tuple[float, float]) -> tuple[float, float]:
        cx, cy = self._center(node_id)
        w, h = self.sizes[node_id]
        dx, dy = toward[0] - cx, toward[1] - cy
        if dx == 0 and dy == 0:
            return cx, cy
        k = min((w / 2 + 10) / abs(dx) if dx else math.inf, (h / 2 + 10) / abs(dy) if dy else math.inf)
        return cx + dx * k, cy + dy * k

    def render(self, cv: Canvas, t: float, ctx: RenderContext) -> None:
        """Draw arrows then nodes."""
        th = ctx.theme
        u = ctx.unit
        fade = exit_alpha(ctx, t + self.start, self.end)
        if fade <= 0:
            return
        with cv.layer(alpha=fade):
            for e in self.spec.edges:
                prog = get_easing("in_out_cubic")(min(max((t - e.at) / 0.6, 0.0), 1.0))
                if prog <= 0:
                    continue
                a, b = self._center(e.src), self._center(e.dst)
                p0 = self._edge_point(e.src, b)
                p1 = self._edge_point(e.dst, a)
                mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
                nx, ny = -(p1[1] - p0[1]), p1[0] - p0[0]
                ln = math.hypot(nx, ny) or 1.0
                bend = 0.12
                cx, cy = (
                    mx + nx / ln * bend * math.hypot(p1[0] - p0[0], p1[1] - p0[1]),
                    my + ny / ln * bend * math.hypot(p1[0] - p0[0], p1[1] - p0[1]),
                )
                path = PathBuilder().move_to(*p0).quad_to(cx, cy, *p1)
                col = th.color("accent")
                cv.path(path, stroke=with_alpha(col, 0.9), stroke_width=6 * u, trim=(0, prog))
                if prog > 0.97:
                    hx, hy, ang = path.point_at(1.0)
                    a1 = math.radians(ang + 150)
                    a2 = math.radians(ang - 150)
                    head = (
                        PathBuilder()
                        .move_to(hx, hy)
                        .line_to(hx + 26 * u * math.cos(a1), hy + 26 * u * math.sin(a1))
                        .line_to(hx + 26 * u * math.cos(a2), hy + 26 * u * math.sin(a2))
                        .close()
                    )
                    cv.path(head, fill=col)
                if e.label and prog > 0.5:
                    lx, ly, _ = path.point_at(0.5)
                    lf = th.fonts.mono.spec(38 * u)
                    w = ctx.fonts.measure(e.label, lf)
                    cv.rect(
                        lx - w / 2 - 14 * u,
                        ly - 26 * u,
                        w + 28 * u,
                        48 * u,
                        fill=with_alpha(th.color("bg"), 0.9),
                        radius=10 * u,
                    )
                    cv.text(
                        e.label,
                        lx,
                        ly,
                        lf,
                        th.color("text_muted"),
                        align="center",
                        baseline="middle",
                    )
            for nd in self.spec.nodes:
                m = entrance(ctx, t, nd.at, "pop", 0.5)
                if m.alpha <= 0:
                    continue
                cx, cy = self._center(nd.id)
                w, h = self.sizes[nd.id]
                fill = th.color("accent") if nd.accent else th.color("surface")
                txt = th.color("bg") if nd.accent else th.color("text")
                with cv.saved():
                    cv.translate(cx, cy)
                    cv.scale(m.scale)
                    cv.rect(
                        -w / 2 + 6 * u,
                        -h / 2 + 12 * u,
                        w,
                        h,
                        fill=(0, 0, 0, 0.35 * m.alpha),
                        radius=th.corner_radius * u,
                        blur=14 * u,
                    )
                    cv.rect(
                        -w / 2,
                        -h / 2,
                        w,
                        h,
                        fill=with_alpha(fill, m.alpha),
                        radius=th.corner_radius * u,
                    )
                    cv.rect(
                        -w / 2,
                        -h / 2,
                        w,
                        h,
                        stroke=with_alpha(th.color("accent"), 0.5 * m.alpha),
                        stroke_width=2.5 * u,
                        radius=th.corner_radius * u,
                    )
                    blk = self.blocks[nd.id]
                    ty = -blk.height / 2
                    for line in blk.lines:
                        cv.text(
                            " ".join(w_.text for w_ in line.words),
                            0,
                            ty + blk.line_height * 0.78,
                            blk.font,
                            with_alpha(txt, m.alpha),
                            align="center",
                        )
                        ty += blk.line_height
