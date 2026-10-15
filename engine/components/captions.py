"""WordCaptions: word-timed animated captions (our own caption system).

Paging: words are grouped into short "pages" that break on sentence punctuation, on pauses, and
on the theme's max words/characters, so captions follow the rhythm of speech rather than a fixed
word count. Styles:

* ``pop``: the page pops in; the active word scales up and takes the highlight colour.
* ``box``: an accent box glides from word to word behind the active word.
* ``karaoke``: upcoming words are dimmed and light up as they are spoken.

Words flagged ``emph`` keep the accent colour. Text has a heavy outline + soft shadow so it stays
legible over any visual.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.components.base import cased
from engine.core.canvas import Canvas
from engine.core.color import RGBA
from engine.core.easing import get_easing
from engine.core.scene import Node, RenderContext
from engine.core.text import TextBlock, layout_text
from engine.schema.spec import CaptionWord

PAUSE_BREAK_S = 0.32
TRAILING_STRIP = ",.;:"


@dataclass(frozen=True)
class Page:
    """A group of words shown together."""

    words: tuple[CaptionWord, ...]
    start: float
    end: float


def display_word(w: str) -> str:
    """Caption form of a word: trailing commas/periods dropped (? and ! kept)."""
    out = w.rstrip(TRAILING_STRIP)
    return out or w


def paginate(words: list[CaptionWord], max_words: int, max_chars: int, hold: float = 0.6) -> list[Page]:
    """Group words into caption pages.

    A page ends after ``max_words`` words, before exceeding ``max_chars`` characters, after
    sentence-final punctuation, or before a pause longer than ``PAUSE_BREAK_S``.
    """
    pages: list[list[CaptionWord]] = []
    cur: list[CaptionWord] = []
    chars = 0
    for i, w in enumerate(words):
        wlen = len(display_word(w.w))
        if cur and (len(cur) >= max_words or chars + 1 + wlen > max_chars):
            pages.append(cur)
            cur, chars = [], 0
        cur.append(w)
        chars += wlen + (1 if chars else 0)
        nxt = words[i + 1] if i + 1 < len(words) else None
        sentence_end = w.w.rstrip("\"'”’)").endswith((".", "?", "!", ":", ";"))
        pause = nxt is not None and nxt.start - w.end > PAUSE_BREAK_S
        comma = w.w.endswith(",") and len(cur) >= 2
        if sentence_end or pause or comma:
            pages.append(cur)
            cur, chars = [], 0
    if cur:
        pages.append(cur)
    out: list[Page] = []
    for k, pw in enumerate(pages):
        start = pw[0].start
        nxt_start = pages[k + 1][0].start if k + 1 < len(pages) else None
        end = pw[-1].end + hold
        if nxt_start is not None:
            end = min(end, nxt_start)
        out.append(Page(tuple(pw), start, max(end, pw[-1].end)))
    return out


class WordCaptions(Node):
    """Global caption overlay (drawn in global time)."""

    def __init__(self, words: list[CaptionWord], ctx: RenderContext, mode: str | None = None) -> None:
        super().__init__(key="WordCaptions")
        cs = ctx.theme.captions
        self.mode = mode or cs.mode
        max_words = cs.max_words if ctx.layout == "short" else cs.max_words + 3
        max_chars = cs.max_chars if ctx.layout == "short" else cs.max_chars + 16
        self.pages = paginate(words, max_words, max_chars)
        role = ctx.theme.fonts.caption
        size = cs.size * ctx.unit * (1.0 if ctx.layout == "short" else 0.72)
        self.font = role.spec(size)
        self.spacing = role.tracking * size
        self.max_w = ctx.safe.w * 0.92
        # centre on the safe area, not the frame: Shorts' safe zone is offset left of the
        # right-edge buttons, so a frame-centred full-width line would cross into them
        self.cx = ctx.safe.x + ctx.safe.w / 2
        self.cy = ctx.height * (cs.y if ctx.layout == "short" else cs.y_long)
        self._layouts: dict[int, TextBlock] = {}

    def _block(self, i: int, ctx: RenderContext) -> TextBlock:
        blk = self._layouts.get(i)
        if blk is None:
            role = ctx.theme.fonts.caption
            text = " ".join(cased(display_word(w.w), role) for w in self.pages[i].words)
            gap = 0.34 if self.mode == "box" else 0.16
            blk = layout_text(
                text,
                ctx.fonts,
                self.font,
                self.max_w,
                letter_spacing=self.spacing,
                line_height=1.16,
                word_gap_em=gap,
            )
            self._layouts[i] = blk
        return blk

    def _page_at(self, t: float) -> int | None:
        lo, hi = 0, len(self.pages) - 1
        found = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.pages[mid].start <= t:
                found = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if found is None or t >= self.pages[found].end:
            return None
        return found

    def render(self, cv: Canvas, t: float, ctx: RenderContext) -> None:
        """Draw the active page at global time ``t`` (tagged as captions for quality checks)."""
        prev = cv.record_role
        cv.record_role = "caption"
        try:
            self._render(cv, t, ctx)
        finally:
            cv.record_role = prev

    def _render(self, cv: Canvas, t: float, ctx: RenderContext) -> None:
        pi = self._page_at(t)
        if pi is None:
            return
        page = self.pages[pi]
        blk = self._block(pi, ctx)
        th = ctx.theme
        u = ctx.unit
        age = t - page.start
        pop = get_easing("back_out(2.4)")(min(age / 0.16, 1.0))
        page_scale = 0.82 + 0.18 * pop
        page_alpha = min(1.0, age / 0.06)
        tail = page.end - t
        if tail < 0.08:
            page_alpha *= max(tail / 0.08, 0.0)
        # word geometry
        top = self.cy - blk.height / 2
        boxes: list[tuple[float, float, float, CaptionWord]] = []  # (x, baseline, width, word)
        y = top
        for ln in blk.lines:
            lx = self.cx - ln.width / 2
            for lw in ln.words:
                boxes.append((lx + lw.x, y + blk.line_height * 0.78, lw.width, page.words[lw.index]))
            y += blk.line_height
        active = -1
        for k, (_, _, _, spoken) in enumerate(boxes):
            if spoken.start <= t:
                active = k
        text_c = th.color("text")
        hi_c = th.color("highlight")
        acc = th.color("accent")
        stroke_c: RGBA = (0.02, 0.02, 0.04, 1.0)
        with cv.saved():
            cv.translate(self.cx, self.cy)
            cv.scale(page_scale)
            cv.translate(-self.cx, -self.cy)
            with cv.layer(alpha=page_alpha):
                if self.mode == "box" and active >= 0:
                    self._box(cv, ctx, t, boxes, active, acc)
                for k, (x, base, w, word) in enumerate(boxes):
                    label = cased(display_word(word.w), th.fonts.caption)
                    is_active = k == active
                    color = text_c
                    alpha = 1.0
                    scale = 1.0
                    if word.emph:
                        color = acc
                    if self.mode == "pop" and is_active:
                        color = acc if word.emph else hi_c
                        wa = get_easing("back_out(3)")(min((t - word.start) / 0.12, 1.0))
                        scale = 1.0 + 0.06 * wa
                    elif self.mode == "box" and is_active:
                        color = th.color("bg")
                    elif self.mode == "karaoke" and k > active:
                        alpha = 0.45
                    stroke = None if self.mode == "box" and is_active else stroke_c
                    sw = th.captions.stroke * u
                    with cv.saved():
                        cv.translate(x + w / 2, base)
                        if scale != 1.0:
                            cv.scale(scale)
                        cv.text(
                            label, 0, 0, self.font, color, align="center",
                            letter_spacing=self.spacing, alpha=alpha,
                            stroke=stroke if sw > 0 else None, stroke_width=sw,
                            shadow=(0, 6 * u, 10 * u, (0, 0, 0, 0.55)),
                        )  # fmt: skip

    def _box(
        self,
        cv: Canvas,
        ctx: RenderContext,
        t: float,
        boxes: list[tuple[float, float, float, CaptionWord]],
        active: int,
        color: RGBA,
    ) -> None:
        u = ctx.unit
        x, base, w, word = boxes[active]
        if active > 0:
            px, pbase, pw, _ = boxes[active - 1]
            k = get_easing("out_expo")(min((t - word.start) / 0.14, 1.0))
            x = px + (x - px) * k
            w = pw + (w - pw) * k
            base = pbase + (base - pbase) * k
        pad_x, pad_y = self.font.size * 0.14, self.font.size * 0.12
        cap = self.font.size * 0.72
        cv.rect(
            x - pad_x,
            base - cap - pad_y,
            w + 2 * pad_x,
            cap + 2 * pad_y + 6 * u,
            fill=color,
            radius=ctx.theme.corner_radius * u * 0.6,
        )
