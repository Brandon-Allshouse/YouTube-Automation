"""KineticTitle: a headline whose words enter one by one in the theme's motion language.

The default ``mask`` entrance slides each word up from behind its own line's clip edge, the
classic editorial reveal; ``rise``/``pop``/``slide`` are alternatives. Emphasis words take the
accent color with a soft glow. An optional kicker sits above and an accent underline draws on
after the words land.
"""

from __future__ import annotations

from engine.components.base import cased, entrance, exit_alpha, norm_word, region_rect
from engine.core.canvas import Canvas, FontSpec
from engine.core.color import with_alpha
from engine.core.easing import get_easing
from engine.core.scene import Node, RenderContext
from engine.core.text import TextBlock, fit_text
from engine.core.timeline import stagger
from engine.schema.spec import KineticTitleSpec

SIZES = {"xl": 168.0, "l": 128.0, "m": 92.0}


class KineticTitle(Node):
    """Animated headline component."""

    def __init__(self, spec: KineticTitleSpec, ctx: RenderContext) -> None:
        super().__init__(start=spec.start, end=spec.end, key="KineticTitle")
        self.spec = spec
        th = ctx.theme
        role = th.fonts.display
        self.box = region_rect(ctx, spec.region).inset(24 * ctx.unit, 0)
        self.text = cased(spec.text, role)
        kicker_h = 70 * ctx.unit if spec.kicker else 0.0
        base = role.spec(
            SIZES[spec.size] * ctx.unit * (0.82 if ctx.layout == "long" and spec.region in ("left", "right") else 1.0)
        )
        self.block: TextBlock = fit_text(
            self.text,
            ctx.fonts,
            base,
            self.box.w,
            self.box.h - kicker_h - 40 * ctx.unit,
            max_lines=3,
            min_size=48 * ctx.unit,
            letter_spacing_em=role.tracking,
            line_height=1.02,
        )
        self.emph = {norm_word(e) for e in spec.emphasis}
        n_words = sum(len(ln.words) for ln in self.block.lines)
        self.starts = stagger(n_words, th.motion.stagger * 1.4, start=0.12 if spec.kicker else 0.0)
        self.kicker_h = kicker_h
        total_h = kicker_h + self.block.height
        self.top = self.box.cy - total_h / 2
        self.land = (self.starts[-1] if self.starts else 0.0) + th.motion.enter_duration

    def render(self, cv: Canvas, t: float, ctx: RenderContext) -> None:
        """Draw kicker, words and underline at local time ``t``."""
        th = ctx.theme
        spec = self.spec
        fade = exit_alpha(ctx, t + self.start, self.end)
        if fade <= 0:
            return
        u = ctx.unit
        with cv.layer(alpha=fade):
            y = self.top
            if spec.kicker:
                m = entrance(ctx, t, 0.0, "rise", 0.5)
                kfont = FontSpec(th.fonts.body.family, 44 * u, 700, th.fonts.body.axes)
                ktext = spec.kicker.upper()
                kx = self.box.cx if spec.align == "center" else self.box.x
                kw = ctx.fonts.measure(ktext, kfont, 6 * u)
                left = kx - kw / 2 if spec.align == "center" else kx + 34 * u
                cv.text(
                    ktext,
                    left,
                    y + 40 * u + m.dy * 0.5,
                    kfont,
                    with_alpha(th.color("accent"), m.alpha),
                    letter_spacing=6 * u,
                )
                bar_x = left - 34 * u
                cv.line(
                    bar_x,
                    y + 26 * u,
                    bar_x + 22 * u * m.progress,
                    y + 26 * u,
                    with_alpha(th.color("accent"), m.alpha),
                    6 * u,
                )
                y += self.kicker_h
            block = self.block
            wi = 0
            for line in block.lines:
                lx = self.box.cx - line.width / 2 if spec.align == "center" else self.box.x
                baseline = y + block.line_height * 0.80
                if spec.entrance == "mask":
                    with cv.saved():
                        cv.clip_rect(
                            self.box.x - 40 * u,
                            y - block.line_height * 0.25,
                            self.box.w + 80 * u,
                            block.line_height * 1.18,
                        )
                        for word in line.words:
                            self._word(
                                cv,
                                ctx,
                                t,
                                wi,
                                word.text,
                                lx + word.x,
                                baseline,
                                block.font,
                                block.letter_spacing,
                                block.line_height,
                            )
                            wi += 1
                else:
                    for word in line.words:
                        self._word(
                            cv,
                            ctx,
                            t,
                            wi,
                            word.text,
                            lx + word.x,
                            baseline,
                            block.font,
                            block.letter_spacing,
                            block.line_height,
                        )
                        wi += 1
                y += block.line_height
            if spec.underline:
                prog = get_easing(th.motion.enter)(min(max((t - self.land + 0.2) / 0.6, 0.0), 1.0))
                if prog > 0:
                    last = block.lines[-1]
                    lx = self.box.cx - last.width / 2 if spec.align == "center" else self.box.x
                    uy = y + 18 * u
                    cv.line(lx, uy, lx + last.width * prog, uy, th.color("accent"), 10 * u, cap="round")

    def _word(
        self,
        cv: Canvas,
        ctx: RenderContext,
        t: float,
        i: int,
        text: str,
        x: float,
        baseline: float,
        font: FontSpec,
        spacing: float,
        line_h: float,
    ) -> None:
        th = ctx.theme
        st = self.starts[i]
        emph = norm_word(text) in self.emph
        color = th.color("accent") if emph else th.color("text")
        kind = self.spec.entrance
        if kind == "mask":
            m = entrance(ctx, t, st, "fade", th.motion.enter_duration)
            dy = (1 - m.progress) * line_h * 1.05
            alpha, dx, scale = min(1.0, m.alpha * 2), 0.0, 1.0
        else:
            m = entrance(ctx, t, st, kind)
            dy, dx, scale, alpha = m.dy, m.dx, m.scale, m.alpha
        if alpha <= 0:
            return
        w = ctx.fonts.measure(text, font, spacing)
        with cv.saved():
            cv.translate(x + dx + w / 2, baseline + dy)
            if scale != 1.0:
                cv.scale(scale)
            if emph:
                cv.text(
                    text,
                    0,
                    0,
                    font,
                    with_alpha(color, 0.45 * alpha),
                    align="center",
                    letter_spacing=spacing,
                    shadow=(0, 0, 18 * ctx.unit, with_alpha(color, 0.6 * alpha)),
                )
            cv.text(
                text, 0, 0, font, color, align="center", letter_spacing=spacing, alpha=alpha,
                shadow=(0, 6 * ctx.unit, 14 * ctx.unit, (0, 0, 0, 0.45)),
            )  # fmt: skip
