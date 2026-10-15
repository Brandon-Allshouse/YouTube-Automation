"""Text layout: balanced line breaking and fit-to-box sizing.

Greedy wrapping leaves ragged last lines ("widows"); we use a small dynamic program that
minimises the squared slack of every line, which gives the balanced, poster-like line breaks
that headline typography needs.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.core.canvas import Fonts, FontSpec


@dataclass(frozen=True)
class LaidOutWord:
    """A word placed within a line (x relative to the line's left edge)."""

    text: str
    index: int  # index in the original word list
    x: float
    width: float


@dataclass(frozen=True)
class LaidOutLine:
    """One line of laid-out words."""

    words: tuple[LaidOutWord, ...]
    width: float


@dataclass(frozen=True)
class TextBlock:
    """A wrapped block at a resolved font size."""

    lines: tuple[LaidOutLine, ...]
    font: FontSpec
    line_height: float
    letter_spacing: float

    @property
    def height(self) -> float:
        """Total height of the block (line count x line height)."""
        return len(self.lines) * self.line_height

    @property
    def width(self) -> float:
        """Widest line."""
        return max((ln.width for ln in self.lines), default=0.0)


def balanced_breaks(widths: list[float], space: float, max_width: float) -> list[list[int]]:
    """Return word-index groups per line minimising the sum of squared slack.

    Words wider than ``max_width`` get a line of their own (the caller shrinks the font).
    """
    n = len(widths)
    if n == 0:
        return []
    inf = float("inf")
    best = [inf] * (n + 1)
    prev = [0] * (n + 1)
    best[0] = 0.0
    for j in range(1, n + 1):
        line_w = -space
        for i in range(j - 1, -1, -1):
            line_w += widths[i] + space
            if line_w > max_width and i < j - 1:
                break
            slack = max(max_width - line_w, 0.0)
            # the last line is allowed some slack, but not to become a lonely widow
            cost = slack * slack if j < n else (slack * slack) * 0.5
            if best[i] + cost < best[j]:
                best[j] = best[i] + cost
                prev[j] = i
    lines: list[list[int]] = []
    j = n
    while j > 0:
        i = prev[j]
        lines.append(list(range(i, j)))
        j = i
    return lines[::-1]


def layout_text(
    text: str,
    fonts: Fonts,
    font: FontSpec,
    max_width: float,
    *,
    letter_spacing: float = 0.0,
    line_height: float = 1.08,
    word_gap_em: float = 0.0,
) -> TextBlock:
    """Wrap ``text`` into balanced lines at a fixed font size (``word_gap_em`` widens spaces)."""
    words = text.split()
    widths = [fonts.measure(w, font, letter_spacing) for w in words]
    space = fonts.measure(" ", font) + letter_spacing + word_gap_em * font.size
    groups = balanced_breaks(widths, space, max_width)
    lines: list[LaidOutLine] = []
    for g in groups:
        x = 0.0
        placed: list[LaidOutWord] = []
        for k, idx in enumerate(g):
            if k:
                x += space
            placed.append(LaidOutWord(words[idx], idx, x, widths[idx]))
            x += widths[idx]
        lines.append(LaidOutLine(tuple(placed), x))
    return TextBlock(tuple(lines), font, font.size * line_height, letter_spacing)


def fit_text(
    text: str,
    fonts: Fonts,
    base: FontSpec,
    max_width: float,
    max_height: float,
    *,
    max_lines: int = 3,
    min_size: float = 24.0,
    letter_spacing_em: float = 0.0,
    line_height: float = 1.08,
) -> TextBlock:
    """Largest size ≤ ``base.size`` whose balanced layout fits the box and line limit."""
    size = base.size
    while True:
        spec = FontSpec(base.family, size, base.weight, base.axes)
        block = layout_text(
            text,
            fonts,
            spec,
            max_width,
            letter_spacing=letter_spacing_em * size,
            line_height=line_height,
        )
        fits = block.width <= max_width + 0.5 and block.height <= max_height and len(block.lines) <= max_lines
        if fits or size <= min_size:
            return block
        size = max(min_size, size * 0.93)
