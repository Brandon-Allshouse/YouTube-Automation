"""StatCounter: a big number that counts up, lands with a pulse, and carries a label.

Layout is computed from the *final* formatted value so the number does not jitter sideways while
it counts. An optional progress ring fills in sync with the count.
"""

from __future__ import annotations

import math

from engine.components.base import entrance, exit_alpha, region_rect
from engine.core.canvas import Canvas, FontSpec, PathBuilder
from engine.core.color import with_alpha
from engine.core.easing import get_easing
from engine.core.scene import Node, RenderContext
from engine.core.text import fit_text
from engine.schema.spec import StatCounterSpec


def format_number(value: float, decimals: int = 0, compact: bool = False) -> str:
    """Format with thousands separators, or compact (1.2K/3.4M/5.6B/7.8T)."""
    if compact:
        for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
            if abs(value) >= div:
                return f"{value / div:,.{max(decimals, 1)}f}".rstrip("0").rstrip(".") + suf
    return f"{value:,.{decimals}f}"


class StatCounter(Node):
    """Counting statistic component."""

    def __init__(self, spec: StatCounterSpec, ctx: RenderContext) -> None:
        super().__init__(start=spec.start, end=spec.end, key="StatCounter")
        self.spec = spec
        self.box = region_rect(ctx, spec.region)
        th = ctx.theme
        final = spec.prefix + format_number(spec.value, spec.decimals, spec.compact) + spec.suffix
        has_ring = spec.ring is not None
        num_h = self.box.h * (0.42 if has_ring else 0.55)
        max_w = self.box.w * (0.62 if has_ring else 0.92)
        blk = fit_text(
            final,
            ctx.fonts,
            th.fonts.display.spec(400 * ctx.unit),
            max_w,
            num_h,
            max_lines=1,
            min_size=60 * ctx.unit,
        )
        self.num_font: FontSpec = blk.font
        self.num_w = ctx.fonts.measure(final, self.num_font)
        self.label_font = th.fonts.body.spec(min(64 * ctx.unit, max(self.num_font.size * 0.2, 44 * ctx.unit)))
        self.count_start = (spec.count_at - spec.start) if spec.count_at is not None else 0.18
        self.count_start = max(self.count_start, 0.18)
        self.land = self.count_start + spec.count_duration

    def _value(self, t: float) -> float:
        s = self.spec
        u = (t - self.count_start) / s.count_duration
        if u <= 0:
            return s.from_value
        if u >= 1:
            return s.value
        return s.from_value + (s.value - s.from_value) * get_easing("out_expo")(u)

    def render(self, cv: Canvas, t: float, ctx: RenderContext) -> None:
        """Draw ring, number and label."""
        th = ctx.theme
        s = self.spec
        u = ctx.unit
        fade = exit_alpha(ctx, t + self.start, self.end)
        m = entrance(ctx, t, 0.0, "pop", 0.5)
        if m.alpha <= 0 or fade <= 0:
            return
        cx, cy = self.box.cx, self.box.cy - (40 * u if s.label else 0)
        pulse_u = (t - self.land) / 0.45
        pulse = 1.0 + 0.07 * math.sin(math.pi * pulse_u) if 0 <= pulse_u <= 1 else 1.0
        with cv.layer(alpha=m.alpha * fade):
            if s.ring is not None:
                r = min(self.box.w, self.box.h) * 0.36
                cv.circle(cx, cy, r, stroke=with_alpha(th.color("text"), 0.10), stroke_width=22 * u)
                frac = s.ring * min(max((t - self.count_start) / s.count_duration, 0.0), 1.0)
                frac = get_easing("out_expo")(frac / s.ring) * s.ring if s.ring > 0 else 0.0
                if frac > 0:
                    arc = PathBuilder().arc(cx, cy, r, -90, 360 * frac)
                    cv.path(
                        arc,
                        stroke=with_alpha(th.color("accent"), 0.35),
                        stroke_width=40 * u,
                        blur=14 * u,
                    )
                    cv.path(arc, stroke=th.color("accent"), stroke_width=22 * u)
            if 0 <= pulse_u <= 1:
                cv.radial_glow(
                    cx,
                    cy,
                    self.num_w * 0.8,
                    with_alpha(th.color("accent"), 0.30 * math.sin(math.pi * pulse_u)),
                    blend="screen",
                )
            text = s.prefix + format_number(self._value(t), s.decimals, s.compact) + s.suffix
            with cv.saved():
                cv.translate(cx, cy)
                cv.scale(m.scale * pulse)
                cv.text(
                    text, -self.num_w / 2, 0, self.num_font, th.color("text"), baseline="middle",
                    shadow=(0, 8 * u, 20 * u, (0, 0, 0, 0.5)),
                )  # fmt: skip
            if s.label:
                lm = entrance(ctx, t, 0.35, "rise")
                ly = cy + self.num_font.size * 0.55 + 40 * u + lm.dy
                if s.ring is not None:
                    ly = cy + min(self.box.w, self.box.h) * 0.36 + 80 * u + lm.dy
                cv.text(
                    s.label,
                    cx,
                    ly,
                    self.label_font,
                    with_alpha(th.color("text_muted"), lm.alpha),
                    align="center",
                    baseline="middle",
                )
