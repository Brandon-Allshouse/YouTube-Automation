"""QuoteCard: a quotation on a card, words revealing in reading order, key words highlighted.

Highlighted words get a marker swipe behind them after they appear, the way an editor would mark
up a page.
"""

from __future__ import annotations

from engine.components.base import entrance, exit_alpha, norm_word, region_rect
from engine.core.canvas import Canvas
from engine.core.color import with_alpha
from engine.core.easing import get_easing
from engine.core.scene import Node, RenderContext
from engine.core.text import fit_text
from engine.core.timeline import stagger
from engine.schema.spec import QuoteCardSpec


class QuoteCard(Node):
    """Quote card component."""

    def __init__(self, spec: QuoteCardSpec, ctx: RenderContext) -> None:
        super().__init__(start=spec.start, end=spec.end, key="QuoteCard")
        self.spec = spec
        th = ctx.theme
        u = ctx.unit
        region = region_rect(ctx, spec.region)
        self.card = region.inset(10 * u, region.h * 0.06)
        self.pad = 64 * u
        inner_w = self.card.w - 2 * self.pad
        attrib_h = 90 * u if spec.attribution else 0.0
        self.block = fit_text(
            f"“{spec.quote}”", ctx.fonts, th.fonts.display.spec(118 * u), inner_w,
            self.card.h - 2 * self.pad - attrib_h - 80 * u, max_lines=6, min_size=40 * u,
            line_height=1.18,
        )  # fmt: skip
        self.hl = {norm_word(h) for h in spec.highlight}
        n = sum(len(ln.words) for ln in self.block.lines)
        self.word_starts = stagger(n, 0.045, start=0.35)
        self.text_top = self.card.y + self.pad + 70 * u
        self.attrib_h = attrib_h
        # tighten the card to its content so short quotes don't float in a huge box
        content_h = 70 * u + self.block.height + attrib_h + 2 * self.pad
        if content_h < self.card.h:
            dy = (self.card.h - content_h) / 2
            self.card = type(self.card)(self.card.x, self.card.y + dy, self.card.w, content_h)
            self.text_top = self.card.y + self.pad + 70 * u

    def render(self, cv: Canvas, t: float, ctx: RenderContext) -> None:
        """Draw card, quote mark, words, highlights, attribution."""
        th = ctx.theme
        u = ctx.unit
        fade = exit_alpha(ctx, t + self.start, self.end)
        cm = entrance(ctx, t, 0.0, "rise", 0.6)
        if cm.alpha <= 0 or fade <= 0:
            return
        c = self.card
        with cv.layer(alpha=fade), cv.saved():
            cv.translate(0, cm.dy)
            cv.rect(
                c.x + 10 * u,
                c.y + 24 * u,
                c.w,
                c.h,
                fill=(0, 0, 0, 0.35 * cm.alpha),
                radius=th.corner_radius * u,
                blur=24 * u,
            )
            cv.rect(
                c.x,
                c.y,
                c.w,
                c.h,
                fill=with_alpha(th.color("surface"), 0.94 * cm.alpha),
                radius=th.corner_radius * u,
            )
            bar_h = c.h * get_easing(th.motion.enter)(min(t / 0.7, 1.0))
            cv.rect(
                c.x,
                c.y + (c.h - bar_h) / 2,
                10 * u,
                bar_h,
                fill=th.color("accent"),
                radius=5 * u,
            )
            qm = entrance(ctx, t, 0.15, "pop", 0.5)
            with cv.saved():
                cv.translate(c.x + self.pad, c.y + self.pad + 60 * u)
                cv.scale(qm.scale)
                cv.text(
                    "“",
                    0,
                    60 * u,
                    th.fonts.display.spec(220 * u),
                    with_alpha(th.color("accent"), 0.30 * qm.alpha),
                )
            blk = self.block
            y = self.text_top
            wi = 0
            for ln in blk.lines:
                base = y + blk.line_height * 0.78
                for w in ln.words:
                    st = self.word_starts[wi]
                    wi += 1
                    wm = entrance(ctx, t, st, "rise", 0.45)
                    if wm.alpha <= 0:
                        continue
                    x = c.x + self.pad + w.x
                    is_hl = norm_word(w.text) in self.hl
                    if is_hl:
                        k = get_easing("out_expo")(min((t - st - 0.25) / 0.4, 1.0)) if t > st + 0.25 else 0.0
                        if k > 0:
                            cv.rect(
                                x - 6 * u,
                                base - blk.font.size * 0.42,
                                (w.width + 12 * u) * k,
                                blk.font.size * 0.5,
                                fill=with_alpha(th.color("accent"), 0.38),
                                radius=6 * u,
                            )
                    cv.text(
                        w.text,
                        x,
                        base + wm.dy * 0.4,
                        blk.font,
                        with_alpha(th.color("text"), wm.alpha),
                        letter_spacing=blk.letter_spacing,
                    )
                y += blk.line_height
            if self.spec.attribution:
                am = entrance(ctx, t, self.word_starts[-1] + 0.3 if self.word_starts else 0.6, "rise")
                af = th.fonts.body.spec(46 * u)
                cv.text(
                    f"— {self.spec.attribution}",
                    c.x + self.pad,
                    y + 50 * u + am.dy * 0.5,
                    af,
                    with_alpha(th.color("text_muted"), am.alpha),
                )
