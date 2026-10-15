"""ListReveal: up to six items revealed one at a time, with focus on the newest.

Each item slides in with its badge popping (overshoot); a soft surface bar sits behind the current
item and earlier items dim, so the eye always knows where to look.
"""

from __future__ import annotations

from engine.components.base import cased, entrance, exit_alpha, region_rect
from engine.core.canvas import Canvas
from engine.core.color import mix, with_alpha
from engine.core.easing import get_easing
from engine.core.scene import Node, RenderContext
from engine.core.text import fit_text
from engine.schema.spec import ListRevealSpec


class ListReveal(Node):
    """Sequential list component."""

    def __init__(self, spec: ListRevealSpec, ctx: RenderContext) -> None:
        super().__init__(start=spec.start, end=spec.end, key="ListReveal")
        self.spec = spec
        th = ctx.theme
        u = ctx.unit
        self.box = region_rect(ctx, spec.region).inset(16 * u, 0)
        n = len(spec.items)
        title_h = 150 * u if spec.title else 0.0
        self.title_h = title_h
        self.row_h = min((self.box.h - title_h) / max(n, 3), 230 * u)
        self.badge_r = self.row_h * 0.26
        text_x0 = self.badge_r * 2 + 34 * u
        self.text_x0 = text_x0
        self.blocks = [
            fit_text(
                it,
                ctx.fonts,
                th.fonts.body.spec(self.row_h * 0.40),
                self.box.w - text_x0 - 24 * u,
                self.row_h * 0.82,
                max_lines=2,
                min_size=30 * u,
            )
            for it in spec.items
        ]
        if spec.title:
            self.title_blk = fit_text(
                cased(spec.title, th.fonts.display),
                ctx.fonts,
                th.fonts.display.spec(100 * u),
                self.box.w,
                title_h * 0.9,
                max_lines=1,
                min_size=40 * u,
            )
        total = title_h + self.row_h * n
        self.top = self.box.cy - total / 2
        first = 0.35 if spec.title else 0.1
        self.times = list(spec.reveal_at) if spec.reveal_at else [first + i * spec.interval for i in range(n)]

    def render(self, cv: Canvas, t: float, ctx: RenderContext) -> None:
        """Draw title and revealed items."""
        th = ctx.theme
        u = ctx.unit
        fade = exit_alpha(ctx, t + self.start, self.end)
        if fade <= 0:
            return
        with cv.layer(alpha=fade):
            y = self.top
            if self.spec.title:
                m = entrance(ctx, t, 0.0, "rise")
                blk = self.title_blk
                title = " ".join(w.text for w in blk.lines[0].words)
                cv.text(
                    title,
                    self.box.x,
                    y + blk.line_height * 0.72 + m.dy,
                    blk.font,
                    with_alpha(th.color("text"), m.alpha),
                    letter_spacing=blk.letter_spacing,
                )
                cv.line(
                    self.box.x,
                    y + self.title_h - 22 * u,
                    self.box.x + 120 * u * m.progress,
                    y + self.title_h - 22 * u,
                    th.color("accent"),
                    8 * u,
                )
                y += self.title_h
            revealed = [i for i, rt in enumerate(self.times) if t >= rt]
            current = revealed[-1] if revealed else -1
            for i, item_blk in enumerate(self.blocks):
                rt = self.times[i]
                if t < rt:
                    break
                ry = y + i * self.row_h
                m = entrance(ctx, t, rt, "slide")
                # dim earlier items once the next one has arrived
                dim = 1.0
                if i < current:
                    k = get_easing("out_cubic")(min((t - self.times[i + 1]) / 0.35, 1.0))
                    dim = 1.0 - 0.70 * k  # earlier points recede: one idea at a time on a phone
                alpha = m.alpha * dim
                if i == current:
                    bar = get_easing(th.motion.enter)(min((t - rt) / 0.5, 1.0))
                    cv.rect(
                        self.box.x - 14 * u,
                        ry + self.row_h * 0.08,
                        (self.box.w + 28 * u) * bar,
                        self.row_h * 0.84,
                        fill=with_alpha(th.color("surface"), 0.85 * m.alpha),
                        radius=th.corner_radius * u,
                    )
                bx, by = self.box.x + self.badge_r + 10 * u + m.dx, ry + self.row_h / 2
                bm = entrance(ctx, t, rt + 0.05, "pop", 0.45)
                badge_col = th.color("accent") if i == current else mix(th.color("accent"), th.color("surface"), 0.45)
                cv.circle(bx, by, self.badge_r * bm.scale, fill=with_alpha(badge_col, alpha))
                if self.spec.numbered:
                    nf = th.fonts.display.spec(self.badge_r * 1.15)
                    cv.text(
                        str(i + 1),
                        bx,
                        by,
                        nf,
                        with_alpha(th.color("bg"), alpha),
                        align="center",
                        baseline="middle",
                    )
                tx = self.box.x + self.text_x0 + m.dx
                ty = by - item_blk.height / 2
                for ln in item_blk.lines:
                    cv.text(
                        " ".join(w.text for w in ln.words),
                        tx,
                        ty + item_blk.line_height * 0.78,
                        item_blk.font,
                        with_alpha(th.color("text"), alpha),
                        letter_spacing=item_blk.letter_spacing,
                    )
                    ty += item_blk.line_height
