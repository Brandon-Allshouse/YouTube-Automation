"""ComparisonSplit: two quantities side by side, drawn to honest scale.

``mode="area"`` draws squares whose *areas* are proportional to the values (the truthful way to
compare land area); ``mode="bar"`` draws bar heights proportional to the values. Shapes grow in
with the theme's emphasis curve, values count up, and an optional ratio badge pops in last.
"""

from __future__ import annotations

import math

from engine.components.base import entrance, exit_alpha, region_rect
from engine.components.stat_counter import format_number
from engine.core.canvas import Canvas
from engine.core.color import mix, with_alpha
from engine.core.easing import get_easing
from engine.core.scene import Node, RenderContext
from engine.core.text import fit_text
from engine.schema.spec import ComparisonSplitSpec


class ComparisonSplit(Node):
    """Honest-scale two-way comparison."""

    def __init__(self, spec: ComparisonSplitSpec, ctx: RenderContext) -> None:
        super().__init__(start=spec.start, end=spec.end, key="ComparisonSplit")
        self.spec = spec
        u = ctx.unit
        self.box = region_rect(ctx, spec.region).inset(10 * u, 0)
        vals = (max(spec.left.value, 0.0), max(spec.right.value, 0.0))
        vmax = max(vals) or 1.0
        label_h = 110 * u
        value_h = 80 * u
        col_w = self.box.w / 2 - 24 * u
        gap = 44 * u
        max_side = min(col_w, self.box.h - label_h - value_h - gap - 40 * u)
        if spec.mode == "area":
            self.sides = tuple(max_side * math.sqrt(v / vmax) for v in vals)
        else:
            self.sides = tuple(max_side * (v / vmax) for v in vals)
        total_h = label_h + gap + max_side + value_h
        self.top = self.box.cy - total_h / 2  # centre the whole group in its region
        self.baseline = self.top + label_h + gap + max_side
        th = ctx.theme
        self.label_blocks = [
            fit_text(
                side.label,
                ctx.fonts,
                th.fonts.display.spec(88 * u),
                col_w,
                label_h,
                max_lines=1,
                min_size=30 * u,
            )
            for side in (spec.left, spec.right)
        ]
        self.value_font = th.fonts.body.spec(54 * u)
        self.cols = (self.box.x + self.box.w * 0.25, self.box.x + self.box.w * 0.75)

    def _value_text(self, v: float) -> str:
        s = self.spec
        return format_number(v, s.decimals, s.compact) + (f" {s.unit}" if s.unit else "")

    def render(self, cv: Canvas, t: float, ctx: RenderContext) -> None:
        """Draw both columns and the ratio badge."""
        th = ctx.theme
        s = self.spec
        u = ctx.unit
        fade = exit_alpha(ctx, t + self.start, self.end)
        if fade <= 0:
            return
        colors = (th.color("accent2"), th.color("accent"))
        sides = (s.left, s.right)
        with cv.layer(alpha=fade):
            for i in range(2):
                st = 0.0 if i == 0 else s.stagger
                cx = self.cols[i]
                lm = entrance(ctx, t, st, "rise", 0.5)
                blk = self.label_blocks[i]
                label = " ".join(w.text for w in blk.lines[0].words)
                ly = self.top + blk.line_height * 0.8 + lm.dy
                cv.text(
                    label, cx, ly, blk.font, with_alpha(th.color("text"), lm.alpha),
                    align="center", letter_spacing=blk.letter_spacing,
                )  # fmt: skip
                grow = get_easing(th.motion.emphasis)(min(max((t - st - 0.15) / 0.9, 0.0), 1.0))
                side = self.sides[i]
                if grow > 0:
                    if s.mode == "area":
                        w = h = side * grow
                    else:
                        w, h = 140 * u, side * grow
                    x, y = cx - w / 2, self.baseline - h
                    col = colors[i]
                    cv.rect(
                        x + 8 * u,
                        y + 14 * u,
                        w,
                        h,
                        fill=(0, 0, 0, 0.35),
                        radius=10 * u,
                        blur=14 * u,
                    )
                    cv.gradient_rect(x, y, w, h, [col, mix(col, (0, 0, 0, 1), 0.28)], angle_deg=90, radius=10 * u)
                    cv.rect(
                        x,
                        y,
                        w,
                        h,
                        stroke=with_alpha((1, 1, 1, 1), 0.18),
                        stroke_width=2 * u,
                        radius=10 * u,
                    )
                cnt = get_easing("out_expo")(min(max((t - st - 0.2) / 1.1, 0.0), 1.0))
                if cnt > 0:
                    v = sides[i].value * cnt
                    cv.text(
                        self._value_text(v),
                        cx,
                        self.baseline + 62 * u,
                        self.value_font,
                        with_alpha(th.color("text_muted"), min(1.0, cnt * 3)),
                        align="center",
                    )
            cv.line(
                self.box.x,
                self.baseline,
                self.box.right,
                self.baseline,
                with_alpha(th.color("text"), 0.2),
                2 * u,
            )
            if s.show_ratio and min(s.left.value, s.right.value) > 0:
                bt = s.stagger + 1.2
                bm = entrance(ctx, t, bt, "pop", 0.5)
                if bm.alpha > 0:
                    ratio = max(s.left.value, s.right.value) / min(s.left.value, s.right.value)
                    txt = f"×{ratio:.0f}" if ratio >= 10 else f"×{ratio:.1f}"
                    big = self.sides[1] >= self.sides[0]
                    bx = self.cols[1 if big else 0] + (self.sides[1 if big else 0] / 2) * 0.55
                    by = self.baseline - self.sides[1 if big else 0] * 0.82
                    r = 86 * u
                    with cv.saved():
                        cv.translate(bx, by)
                        cv.scale(bm.scale)
                        cv.circle(0, 0, r, fill=th.color("highlight"))
                        cv.text(
                            txt,
                            0,
                            0,
                            th.fonts.display.spec(64 * u),
                            th.color("bg"),
                            align="center",
                            baseline="middle",
                        )
