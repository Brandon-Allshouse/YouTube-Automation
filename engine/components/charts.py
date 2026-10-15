"""Data components: LineChartReveal and BarRace.

Charts are honest by construction: line charts use a zero baseline unless every value is far from
zero (then a broken-axis tick marks it), bar lengths are linear in value, and labels show real
numbers.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt

from engine.components.base import cased, entrance, exit_alpha, region_rect
from engine.components.stat_counter import format_number
from engine.core.canvas import Canvas, PathBuilder
from engine.core.color import mix, with_alpha
from engine.core.easing import get_easing
from engine.core.scene import Node, RenderContext
from engine.core.text import fit_text
from engine.schema.spec import BarRaceSpec, LineChartRevealSpec


def nice_ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    """Round tick values covering [lo, hi] (1-2-5 steps)."""
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=10 * mag)
    start = math.floor(lo / step) * step
    ticks: list[float] = []
    v = start
    while not ticks or ticks[-1] < hi - step * 1e-9:
        ticks.append(round(v, 10))
        v += step
    return ticks


class LineChartReveal(Node):
    """Line chart that draws left to right with a labelled head."""

    def __init__(self, spec: LineChartRevealSpec, ctx: RenderContext) -> None:
        super().__init__(start=spec.start, end=spec.end, key="LineChartReveal")
        self.spec = spec
        u = ctx.unit
        box = region_rect(ctx, spec.region).inset(20 * u, 0)
        title_h = 120 * u if spec.title else 0.0
        self.title_h = title_h
        self.plot = type(box)(box.x + 150 * u, box.y + title_h + 40 * u, box.w - 190 * u, box.h - title_h - 130 * u)
        allv = [v for s in spec.series for v in s.values]
        lo, hi = min(allv), max(allv)
        lo = min(lo, 0.0) if lo >= 0 and lo < hi * 0.6 else lo - (hi - lo) * 0.1
        self.ticks = nice_ticks(lo, hi)
        self.ymin, self.ymax = self.ticks[0], self.ticks[-1]
        if spec.title:
            self.title_blk = fit_text(
                cased(spec.title, ctx.theme.fonts.display),
                ctx.fonts,
                ctx.theme.fonts.display.spec(76 * u),
                box.w,
                title_h,
                max_lines=1,
                min_size=36 * u,
            )
        self.box = box

    def _pt(self, i: int, n: int, v: float) -> tuple[float, float]:
        p = self.plot
        x = p.x + p.w * (i / max(n - 1, 1))
        y = p.bottom - p.h * (v - self.ymin) / (self.ymax - self.ymin)
        return x, y

    def render(self, cv: Canvas, t: float, ctx: RenderContext) -> None:
        """Draw axes, gridlines, series and head labels."""
        th = ctx.theme
        s = self.spec
        u = ctx.unit
        fade = exit_alpha(ctx, t + self.start, self.end)
        m = entrance(ctx, t, 0.0, "rise", 0.5)
        if fade <= 0 or m.alpha <= 0:
            return
        p = self.plot
        mono = th.fonts.mono.spec(34 * u)
        with cv.layer(alpha=fade * m.alpha):
            if s.title:
                blk = self.title_blk
                cv.text(
                    " ".join(w.text for w in blk.lines[0].words),
                    self.box.x,
                    self.box.y + blk.line_height * 0.8 + m.dy,
                    blk.font,
                    th.color("text"),
                )
            for tick in self.ticks:
                _, y = self._pt(0, 2, tick)
                cv.line(p.x, y, p.right, y, with_alpha(th.color("text"), 0.08), 2 * u)
                cv.text(
                    format_number(tick, 0 if float(tick).is_integer() else 1, abs(tick) >= 1e4) + s.y_suffix,
                    p.x - 18 * u,
                    y,
                    mono,
                    with_alpha(th.color("text_muted"), 0.9),
                    align="right",
                    baseline="middle",
                )
            n = len(s.x_labels)
            step = max(1, math.ceil(n / 5))
            for i in range(0, n, step):
                x, _ = self._pt(i, n, self.ymin)
                cv.text(
                    s.x_labels[i],
                    x,
                    p.bottom + 46 * u,
                    mono,
                    with_alpha(th.color("text_muted"), 0.9),
                    align="center",
                )
            prog = get_easing("in_out_cubic")(min(max((t - 0.3) / s.draw_duration, 0.0), 1.0))
            for si, series in enumerate(s.series):
                col = th.palette.series_rgba(si)
                vals = series.values
                pts = [self._pt(i, len(vals), v) for i, v in enumerate(vals)]
                line = PathBuilder().smooth(pts, 0.35) if len(pts) > 2 else PathBuilder().polyline(pts)
                if si == 0 and prog > 0:  # soft area fill revealed with the line
                    area = PathBuilder().smooth(pts, 0.35) if len(pts) > 2 else PathBuilder().polyline(pts)
                    area.line_to(pts[-1][0], p.bottom).line_to(pts[0][0], p.bottom).close()
                    with cv.saved():
                        cv.clip_rect(p.x, p.y - 40 * u, p.w * prog + 2, p.h + 40 * u)
                        cv.clip_path(area)
                        cv.gradient_rect(
                            p.x,
                            p.y,
                            p.w,
                            p.h,
                            [with_alpha(col, 0.35), with_alpha(col, 0.0)],
                            angle_deg=90,
                        )
                if prog <= 0:
                    continue
                cv.path(
                    line,
                    stroke=with_alpha(col, 0.3),
                    stroke_width=16 * u,
                    trim=(0, prog),
                    blur=8 * u,
                )
                cv.path(line, stroke=col, stroke_width=7 * u, trim=(0, prog))
                hx, hy, _ = line.point_at(prog)
                cv.circle(hx, hy, 12 * u, fill=col)
                cv.circle(hx, hy, 22 * u, stroke=with_alpha(col, 0.5), stroke_width=3 * u)
                fi = prog * (len(vals) - 1)
                k = min(int(fi), len(vals) - 2)
                v = vals[k] + (vals[k + 1] - vals[k]) * (fi - k)
                label = format_number(v, 1 if abs(v) < 100 else 0, abs(v) >= 1e4) + s.y_suffix
                cv.text(
                    label,
                    hx,
                    hy - 40 * u,
                    th.fonts.display.spec(48 * u),
                    th.color("text"),
                    align="center",
                    shadow=(0, 4 * u, 10 * u, (0, 0, 0, 0.6)),
                )
                if series.name and len(s.series) > 1:
                    cv.text(
                        series.name,
                        p.x + 10 * u,
                        p.y + 40 * u + si * 46 * u,
                        th.fonts.body.spec(36 * u),
                        col,
                    )


class BarRace(Node):
    """Animated ranking bars between data frames."""

    def __init__(self, spec: BarRaceSpec, ctx: RenderContext) -> None:
        super().__init__(start=spec.start, end=spec.end, key="BarRace")
        self.spec = spec
        u = ctx.unit
        self.box = region_rect(ctx, spec.region).inset(20 * u, 0)
        self.vals = np.asarray(spec.frames, dtype=np.float64)
        self.title_h = 120 * u if spec.title else 0.0
        self.footer_h = 150 * u if spec.frame_labels else 0.0
        n = min(spec.top_n, len(spec.labels))
        self.row_h = (self.box.h - self.title_h - self.footer_h - 20 * u) / n
        font = ctx.theme.fonts.body.spec(min(self.row_h * 0.42, 50 * u))
        widest = max(ctx.fonts.measure(lbl, font) for lbl in spec.labels)
        max_label_w = self.box.w * 0.34
        if widest > max_label_w:  # shrink labels rather than clip them at the frame edge
            font = ctx.theme.fonts.body.spec(font.size * max_label_w / widest)
            widest = max_label_w
        self.label_font = font
        self.label_w = widest + 24 * u
        self.colors = {lbl: ctx.theme.palette.series_rgba(i) for i, lbl in enumerate(spec.labels)}

    def _state(self, t: float) -> tuple[npt.NDArray[np.float64], float, int]:
        s = self.spec
        f = max(t - 0.4, 0.0) / s.seconds_per_frame
        i = min(int(f), len(s.frames) - 1)
        if i >= len(s.frames) - 1:
            return self.vals[-1], 0.0, len(s.frames) - 1
        k = get_easing("in_out_sine")(f - i)
        return self.vals[i] + (self.vals[i + 1] - self.vals[i]) * k, k, i

    def render(self, cv: Canvas, t: float, ctx: RenderContext) -> None:
        """Draw ranked bars with smooth rank swaps."""
        th = ctx.theme
        s = self.spec
        u = ctx.unit
        fade = exit_alpha(ctx, t + self.start, self.end)
        m = entrance(ctx, t, 0.0, "rise", 0.5)
        if fade <= 0 or m.alpha <= 0:
            return
        vals, k, i = self._state(t)
        # smooth rank: interpolate each label's rank position between frames
        r0 = np.argsort(np.argsort(-self.vals[i]))
        r1 = np.argsort(np.argsort(-self.vals[min(i + 1, len(self.vals) - 1)]))
        ranks = r0 + (r1 - r0) * get_easing("in_out_cubic")(k)
        n = min(s.top_n, len(s.labels))
        vmax = float(np.max(vals)) or 1.0
        top = self.box.y + self.title_h
        label_w = self.label_w
        bar_x = self.box.x + label_w
        bar_w = self.box.w - label_w - 40 * u
        with cv.layer(alpha=fade * m.alpha):
            if s.title:
                cv.text(
                    cased(s.title, th.fonts.display),
                    self.box.x,
                    top - 40 * u,
                    th.fonts.display.spec(70 * u),
                    th.color("text"),
                )
            if s.frame_labels:
                fl = s.frame_labels[min(i + (1 if k > 0.5 else 0), len(s.frame_labels) - 1)]
                cv.text(
                    fl,
                    self.box.right,
                    self.box.bottom - 20 * u,
                    th.fonts.display.spec(130 * u),
                    with_alpha(th.color("text"), 0.22),
                    align="right",
                )
            for j, lbl in enumerate(s.labels):
                r = float(ranks[j])
                if r > n - 0.01 or vals[j] <= vmax * 0.002:
                    continue  # not yet on the board
                y = top + r * self.row_h + self.row_h * 0.12
                h = self.row_h * 0.76
                w = max(bar_w * vals[j] / vmax, 4 * u)
                col = self.colors[lbl]
                alpha = min(1.0, n - r)
                cv.gradient_rect(
                    bar_x,
                    y,
                    w,
                    h,
                    [col, mix(col, (0, 0, 0, 1), 0.25)],
                    angle_deg=0,
                    radius=h * 0.2,
                    alpha=alpha,
                )
                cv.text(
                    lbl,
                    bar_x - 18 * u,
                    y + h / 2,
                    self.label_font,
                    with_alpha(th.color("text"), alpha),
                    align="right",
                    baseline="middle",
                )
                cv.text(
                    format_number(float(vals[j]), 0, True) + s.value_suffix,
                    bar_x + w + 14 * u,
                    y + h / 2,
                    th.fonts.mono.spec(self.label_font.size * 0.85),
                    with_alpha(th.color("text_muted"), alpha),
                    baseline="middle",
                )
