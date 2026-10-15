"""Color parsing, mixing and WCAG contrast math.

Colors are RGBA float tuples in [0, 1] (straight alpha). Hex strings ``#rgb``, ``#rrggbb`` and
``#rrggbbaa`` are accepted everywhere a color is configured.
"""

from __future__ import annotations

import re

RGBA = tuple[float, float, float, float]

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def parse_color(value: str | RGBA | tuple[float, float, float]) -> RGBA:
    """Parse a hex string or a 3/4-tuple of floats into an RGBA tuple.

    Raises:
        ValueError: On malformed input.
    """
    if isinstance(value, tuple):
        if len(value) == 3:
            r, g, b = value
            return (float(r), float(g), float(b), 1.0)
        if len(value) == 4:
            return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
        raise ValueError("color tuple must have 3 or 4 components")
    m = _HEX_RE.fullmatch(value.strip())
    if not m:
        raise ValueError(f"invalid color: {value!r}")
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) == 6:
        h += "ff"
    r, g, b, a = (int(h[i : i + 2], 16) / 255.0 for i in range(0, 8, 2))
    return (r, g, b, a)


def is_valid_color(value: str) -> bool:
    """True if ``value`` parses as a hex color."""
    return bool(_HEX_RE.fullmatch(value.strip()))


def with_alpha(c: RGBA, alpha: float) -> RGBA:
    """Return ``c`` with alpha multiplied by ``alpha``."""
    return (c[0], c[1], c[2], c[3] * alpha)


def mix(a: RGBA, b: RGBA, u: float) -> RGBA:
    """Linear mix from ``a`` (u=0) to ``b`` (u=1)."""
    return (
        a[0] + (b[0] - a[0]) * u,
        a[1] + (b[1] - a[1]) * u,
        a[2] + (b[2] - a[2]) * u,
        a[3] + (b[3] - a[3]) * u,
    )


def to_argb_int(c: RGBA) -> int:
    """Pack into a 32-bit ARGB int (skia's color format)."""

    def ch(v: float) -> int:
        return max(0, min(255, round(v * 255.0)))

    return (ch(c[3]) << 24) | (ch(c[0]) << 16) | (ch(c[1]) << 8) | ch(c[2])


def _linearize(v: float) -> float:
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def relative_luminance(c: RGBA) -> float:
    """WCAG 2.x relative luminance of an opaque color."""
    r, g, b = (_linearize(x) for x in c[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def composite_over(fg: RGBA, bg: RGBA) -> RGBA:
    """Alpha-composite ``fg`` over an opaque ``bg``."""
    a = fg[3]
    return (
        fg[0] * a + bg[0] * (1 - a),
        fg[1] * a + bg[1] * (1 - a),
        fg[2] * a + bg[2] * (1 - a),
        1.0,
    )


def contrast_ratio(fg: RGBA, bg: RGBA) -> float:
    """WCAG contrast ratio (1-21) of ``fg`` composited over ``bg``."""
    l1 = relative_luminance(composite_over(fg, bg))
    l2 = relative_luminance(composite_over(bg, (0.0, 0.0, 0.0, 1.0)))
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)
