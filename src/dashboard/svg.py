"""Tiny SVG chart primitives for the dashboard (our own code; no chart library).

Every chart returns an SVG string. All text goes through :func:`esc`. Styling uses CSS classes only
(no ``style`` attributes), so the page can run under a strict Content-Security-Policy whose only
allowed style is the hashed ``<style>`` block. Missing values render as gaps, never as zeros.
"""

from __future__ import annotations

import html
import math
from collections.abc import Sequence

W_DEFAULT = 640


def esc(x: object) -> str:
    """HTML-escape any value for text or attribute context (quotes included)."""
    return html.escape("" if x is None else str(x), quote=True)


def fmt_num(x: float | None, digits: int = 0) -> str:
    """1234567 → '1.23M'; None → '–'."""
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "–"
    a = abs(x)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if a >= div:
            return f"{x / div:.{2 if a < 10 * div else 1}f}{suf}"
    return f"{x:.{digits}f}"


def fmt_pct(x: float | None, digits: int = 1) -> str:
    """0.1234 → '12.3%'; None → '–'."""
    return "–" if x is None or not math.isfinite(x) else f"{100 * x:.{digits}f}%"


def score_class(v: float | None) -> str:
    """CSS class for a 0-100 score."""
    if v is None:
        return "s-none"
    if v >= 65:
        return "s-good"
    if v >= 45:
        return "s-mid"
    return "s-bad"


def gauge(value: float | None, label: str, *, size: int = 220) -> str:
    """Half-circle 0-100 gauge."""
    r = size * 0.4
    cx, cy = size / 2, size * 0.55
    start = (cx - r, cy)

    def point(frac: float) -> tuple[float, float]:
        ang = math.pi * (1 - frac)
        return cx + r * math.cos(ang), cy - r * math.sin(ang)

    track = f'<path class="g-track" d="M{start[0]:.1f},{start[1]:.1f} A{r:.1f},{r:.1f} 0 0 1 {cx + r:.1f},{cy:.1f}"/>'
    arc = ""
    if value is not None:
        frac = max(0.0, min(1.0, value / 100))
        ex, ey = point(frac)
        if frac > 0:
            d = f"M{start[0]:.1f},{start[1]:.1f} A{r:.1f},{r:.1f} 0 0 1 {ex:.1f},{ey:.1f}"
            arc = f'<path class="g-arc {score_class(value)}" d="{d}"/>'
    shown = "–" if value is None else f"{value:.0f}"
    return (
        f'<svg class="gauge" viewBox="0 0 {size} {size * 0.7:.0f}" role="img" aria-label="{esc(label)}: {esc(shown)}">'
        f"{track}{arc}"
        f'<text class="g-value" x="{cx:.1f}" y="{cy - 4:.1f}" text-anchor="middle">{esc(shown)}</text>'
        f'<text class="g-label" x="{cx:.1f}" y="{cy + 22:.1f}" text-anchor="middle">{esc(label)}</text></svg>'
    )


def sparkline(values: Sequence[float | None], *, w: int = 120, h: int = 32, lo: float = 0, hi: float = 100) -> str:
    """Small line of recent values (e.g. VPI per video, oldest → newest)."""
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(pts) < 2:
        return f'<svg class="spark" viewBox="0 0 {w} {h}" role="img" aria-label="not enough data"></svg>'
    n = len(values) - 1 or 1
    span = (hi - lo) or 1.0

    def xy(i: int, v: float) -> str:
        return f"{2 + (w - 4) * i / n:.1f},{h - 2 - (h - 4) * (min(max(v, lo), hi) - lo) / span:.1f}"

    mid = h - 2 - (h - 4) * (50 - lo) / span
    poly = " ".join(xy(i, v) for i, v in pts)
    return (
        f'<svg class="spark" viewBox="0 0 {w} {h}" role="img" aria-label="trend">'
        f'<line class="baseline" x1="0" x2="{w}" y1="{mid:.1f}" y2="{mid:.1f}"/>'
        f'<polyline class="spark-line" points="{poly}"/></svg>'
    )


def hbars(
    items: Sequence[tuple[str, float, str]],
    *,
    w: int = W_DEFAULT,
    row: int = 26,
    label_w: int = 190,
    center: float | None = None,
    value_fmt: str = "{:.0f}",
) -> str:
    """Horizontal bars: (label, value, css class). With ``center`` the bars diverge from that value."""
    if not items:
        return '<p class="empty">No data yet.</p>'
    vals = [v for _, v, _ in items]
    h = row * len(items) + 8
    plot = w - label_w - 70
    out = [f'<svg class="hbars" viewBox="0 0 {w} {h}" role="img" aria-label="bar chart">']
    if center is None:
        top = max(*vals, 1e-9)
        for i, (lab, v, cls) in enumerate(items):
            y = 4 + i * row
            bw = plot * max(v, 0) / top
            out.append(
                f'<text class="axis" x="{label_w - 8}" y="{y + row * 0.62:.1f}" text-anchor="end">{esc(lab)}</text>'
            )
            out.append(
                f'<rect class="bar {esc(cls)}" x="{label_w}" y="{y + 4}" width="{bw:.1f}" height="{row - 9}" rx="3"/>'
            )
            out.append(
                f'<text class="val" x="{label_w + bw + 6:.1f}" y="{y + row * 0.62:.1f}">'
                f"{esc(value_fmt.format(v))}</text>"
            )
    else:
        reach = max(*(abs(v - center) for v in vals), 1e-9)
        x0 = label_w + plot / 2
        out.append(f'<line class="baseline" x1="{x0:.1f}" x2="{x0:.1f}" y1="0" y2="{h}"/>')
        for i, (lab, v, cls) in enumerate(items):
            y = 4 + i * row
            d = (plot / 2) * (v - center) / reach
            x = x0 + min(d, 0)
            out.append(
                f'<text class="axis" x="{label_w - 8}" y="{y + row * 0.62:.1f}" text-anchor="end">{esc(lab)}</text>'
            )
            out.append(
                f'<rect class="bar {esc(cls)}" x="{x:.1f}" y="{y + 4}" width="{abs(d):.1f}" height="{row - 9}" rx="3"/>'
            )
            tx = x0 + d + (6 if d >= 0 else -6)
            anchor = "start" if d >= 0 else "end"
            out.append(
                f'<text class="val" x="{tx:.1f}" y="{y + row * 0.62:.1f}" text-anchor="{anchor}">'
                f"{esc(value_fmt.format(v))}</text>"
            )
    out.append("</svg>")
    return "".join(out)


def vpi_timeline(series: dict[str, list[float]], *, w: int = W_DEFAULT, h: int = 240, hit: float = 65) -> str:
    """VPI per video in publish order, one line per channel, with the 50 (typical) and hit lines."""
    if not any(len(v) for v in series.values()):
        return '<p class="empty">No scored videos yet. Scores appear 48 h after the first upload.</p>'
    pad_l, pad_r, pad_t, pad_b = 34, 12, 10, 22
    n = max(len(v) for v in series.values())
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b

    def x(i: int) -> float:
        return pad_l + (pw * i / (n - 1) if n > 1 else pw / 2)

    def y(v: float) -> float:
        return pad_t + ph * (1 - max(0.0, min(100.0, v)) / 100)

    out = [f'<svg class="timeline" viewBox="0 0 {w} {h}" role="img" aria-label="VPI per video by channel">']
    for g in (0, 25, 50, 75, 100):
        cls = "baseline" if g == 50 else "grid"
        out.append(f'<line class="{cls}" x1="{pad_l}" x2="{w - pad_r}" y1="{y(g):.1f}" y2="{y(g):.1f}"/>')
        out.append(f'<text class="axis" x="{pad_l - 6}" y="{y(g) + 4:.1f}" text-anchor="end">{g}</text>')
    out.append(f'<line class="hitline" x1="{pad_l}" x2="{w - pad_r}" y1="{y(hit):.1f}" y2="{y(hit):.1f}"/>')
    for k, (name, vals) in enumerate(sorted(series.items())):
        cls = f"series-{k % 6}"
        if len(vals) > 1:
            pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
            out.append(f'<polyline class="line {cls}" points="{pts}"/>')
        for i, v in enumerate(vals):
            out.append(
                f'<circle class="dot {cls}" cx="{x(i):.1f}" cy="{y(v):.1f}" r="3.5">'
                f"<title>{esc(name)} #{i + 1}: {v:.0f}</title></circle>"
            )
    out.append(f'<text class="axis" x="{w - pad_r}" y="{h - 4}" text-anchor="end">videos, oldest → newest</text>')
    out.append("</svg>")
    return "".join(out)


def intervals(
    rows: Sequence[tuple[str, float, float, float]], *, w: int = W_DEFAULT, row: int = 24, label_w: int = 260
) -> str:
    """Credible intervals: (label, low, mean, high) on a 0-1 axis."""
    if not rows:
        return '<p class="empty">No bandit arms yet.</p>'
    h = row * len(rows) + 22
    plot = w - label_w - 20

    def x(v: float) -> float:
        return label_w + plot * max(0.0, min(1.0, v))

    out = [f'<svg class="intervals" viewBox="0 0 {w} {h}" role="img" aria-label="arm success intervals">']
    for g in (0, 0.25, 0.5, 0.75, 1):
        out.append(f'<line class="grid" x1="{x(g):.1f}" x2="{x(g):.1f}" y1="0" y2="{h - 18}"/>')
        out.append(f'<text class="axis" x="{x(g):.1f}" y="{h - 4}" text-anchor="middle">{g:.2f}</text>')
    for i, (lab, lo, mean, hi) in enumerate(rows):
        yc = 4 + i * row + row / 2
        out.append(f'<text class="axis" x="{label_w - 8}" y="{yc + 4:.1f}" text-anchor="end">{esc(lab)}</text>')
        out.append(f'<line class="ci" x1="{x(lo):.1f}" x2="{x(hi):.1f}" y1="{yc:.1f}" y2="{yc:.1f}"/>')
        out.append(f'<circle class="ci-dot" cx="{x(mean):.1f}" cy="{yc:.1f}" r="4"/>')
    out.append("</svg>")
    return "".join(out)


def meter(used: float, cap: float, *, w: int = 260, h: int = 14) -> str:
    """Usage bar (e.g. API quota)."""
    frac = 0.0 if cap <= 0 else max(0.0, min(1.0, used / cap))
    cls = "s-bad" if frac > 0.9 else "s-mid" if frac > 0.6 else "s-good"
    return (
        f'<svg class="meter" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(fmt_num(used))} of {esc(fmt_num(cap))}">'
        f'<rect class="g-track-fill" x="0" y="0" width="{w}" height="{h}" rx="7"/>'
        f'<rect class="bar {cls}" x="0" y="0" width="{w * frac:.1f}" height="{h}" rx="7"/></svg>'
    )
