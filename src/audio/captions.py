"""Word-level caption timing from per-sentence audio (our own alignment algorithm).

For each sentence we know the exact audio. We then:

1. find the speech span (onset/offset) from a 10 ms energy envelope;
2. detect real pauses inside the sentence and match them to clause punctuation (, ; : —) by
   expected position, so clause boundaries land where the voice actually breathes;
3. distribute each clause's time across its words weighted by syllable count (+ a small
   per-word constant for consonant clusters; numbers are expanded to spoken syllables).

Script markup: ``*word*`` marks an emphasised word (accent colour in captions); the asterisks are
stripped before synthesis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

FRAME_S = 0.01
GAP_MIN_S = 0.07
CLAUSE_PUNCT = (",", ";", ":", "—", "–")

_NUM_SYLL = {
    "0": 2, "1": 1, "2": 1, "3": 1, "4": 1, "5": 1, "6": 2, "7": 2, "8": 1, "9": 1,
}  # fmt: skip


def syllables(word: str) -> int:
    """Estimate spoken syllables in an English word (numbers expanded heuristically)."""
    w = word.lower()
    digits = re.sub(r"[^0-9]", "", w)
    if digits:
        if len(digits) == 4 and digits[:2] in {"18", "19", "20"}:  # years: "nineteen thirty-one"
            return 5
        n = sum(_NUM_SYLL[d] for d in digits) + max(0, len(digits) - 2)  # "hundred", "thousand"
        letters = re.sub(r"[^a-z]", "", w)
        return max(1, n + (syllables(letters) if letters else 0))
    w = re.sub(r"[^a-z]", "", w)
    if not w:
        return 0
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups)
    if w.endswith("e") and not w.endswith(("le", "ee", "ye")) and n > 1:
        n -= 1
    if w.endswith(("ed",)) and not w.endswith(("ted", "ded")) and n > 1:
        n -= 1
    # vowel pairs spoken as two syllables (ra-ti-o, us-u-al), except -tion/-sion/-cia/-gio/qua
    n += len(re.findall(r"(?<![tscgq])i[aou]|eo|(?<!q)ua", w))
    return max(1, n)


def word_weight(word: str) -> float:
    """Relative spoken duration of a word."""
    letters = len(re.sub(r"[^A-Za-z0-9]", "", word))
    return syllables(word) + 0.35 + 0.02 * letters


@dataclass(frozen=True)
class WordTiming:
    """A word with start/end (seconds) and emphasis flag."""

    text: str
    start: float
    end: float
    emph: bool = False


@dataclass(frozen=True)
class Token:
    """A displayed caption token and the words actually spoken for it."""

    display: str
    spoken: tuple[str, ...]
    emph: bool


_TOKEN_RE = re.compile(r"(\*?)\{([^{}|]+)\|([^{}]+)\}(\*?)(\S*)|(\S+)")


def parse_markup(sentence: str) -> list[Token]:
    """Parse script markup.

    ``*word*`` marks emphasis; ``{1569|fifteen sixty-nine}`` shows "1569" in captions while the
    voice says "fifteen sixty-nine". Trailing punctuation after a brace group is kept for display.
    """
    tokens: list[Token] = []
    for m in _TOKEN_RE.finditer(sentence):
        if m.group(6) is not None:
            raw = m.group(6)
            emph = bool(re.match(r"^[\"“‘(]*\*.+\*[\W]*$", raw))
            clean = raw.replace("*", "")
            tokens.append(Token(clean, (clean,), emph))
        else:
            emph = bool(m.group(1) and m.group(4))
            tail = m.group(5) or ""
            spoken = m.group(3).split()
            spoken[-1] = spoken[-1] + tail
            tokens.append(Token(m.group(2).strip() + tail, tuple(spoken), emph))
    return tokens


def spoken_text(sentence: str) -> str:
    """The text sent to TTS (markup resolved to spoken words)."""
    return " ".join(w for t in parse_markup(sentence) for w in t.spoken)


def strip_markup(sentence: str) -> tuple[str, list[bool]]:
    """Display text and per-token emphasis flags."""
    toks = parse_markup(sentence)
    return " ".join(t.display for t in toks), [t.emph for t in toks]


def _energy_db(samples: npt.NDArray[np.floating], sr: int) -> npt.NDArray[np.float64]:
    hop = max(1, int(sr * FRAME_S))
    n = len(samples) // hop
    if n == 0:
        return np.array([-120.0])
    frames = np.asarray(samples[: n * hop], dtype=np.float64).reshape(n, hop)
    rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    return np.asarray(db - db.max(), dtype=np.float64)  # relative to loudest frame


def speech_span(samples: npt.NDArray[np.floating], sr: int, floor_db: float = -38.0) -> tuple[float, float]:
    """(onset, offset) seconds of speech inside a clip."""
    db = _energy_db(samples, sr)
    voiced = np.nonzero(db > floor_db)[0]
    if voiced.size == 0:
        return 0.0, len(samples) / sr
    return float(voiced[0] * FRAME_S), float((voiced[-1] + 1) * FRAME_S)


def internal_gaps(samples: npt.NDArray[np.floating], sr: int, floor_db: float = -36.0) -> list[tuple[float, float]]:
    """Silent stretches longer than ``GAP_MIN_S`` strictly inside the speech span."""
    db = _energy_db(samples, sr)
    on, off = speech_span(samples, sr)
    quiet = db < floor_db
    gaps: list[tuple[float, float]] = []
    i, n = 0, len(quiet)
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            s, e = i * FRAME_S, j * FRAME_S
            if e - s >= GAP_MIN_S and s > on + 0.05 and e < off - 0.05:
                gaps.append((s, e))
            i = j
        else:
            i += 1
    return gaps


def _align_words(words: list[str], samples: npt.NDArray[np.floating], sr: int) -> list[tuple[float, float]]:
    """(start, end) per spoken word, relative to the clip start."""
    on, off = speech_span(samples, sr)
    weights = [word_weight(w) for w in words]
    total = sum(weights)
    bounds = [i for i, w in enumerate(words[:-1]) if w.endswith(CLAUSE_PUNCT)]
    gaps = internal_gaps(samples, sr)
    span = off - on
    cuts: list[tuple[int, float, float]] = []  # (word index, clause end, next clause start)
    used: set[int] = set()
    for b in bounds:
        expected = on + span * sum(weights[: b + 1]) / total
        best, best_d = -1, span * 0.25
        for gi, (gs, ge) in enumerate(gaps):
            d = abs((gs + ge) / 2 - expected)
            if gi not in used and d < best_d:
                best, best_d = gi, d
        if best >= 0:
            used.add(best)
            cuts.append((b, gaps[best][0], gaps[best][1]))
        else:
            cuts.append((b, expected, expected))
    cuts.sort()
    out: list[tuple[float, float]] = []
    seg_start, w0 = on, 0
    for b, cut_end, nstart in [*cuts, (len(words) - 1, off, off)]:
        cend = max(cut_end, seg_start + 0.05 * (b - w0 + 1))
        seg_w = weights[w0 : b + 1]
        seg_total = sum(seg_w)
        t = seg_start
        for k, wt in enumerate(seg_w):
            dur = (cend - seg_start) * wt / seg_total
            end = t + dur * (0.94 if w0 + k < b else 1.0)
            out.append((t, end))
            t += dur
        seg_start, w0 = max(nstart, cend), b + 1
    return out


def align_sentence(sentence: str, samples: npt.NDArray[np.floating], sr: int) -> list[WordTiming]:
    """Caption-token timings for one sentence (markup-aware), relative to its clip start."""
    tokens = parse_markup(sentence)
    spoken = [w for t in tokens for w in t.spoken]
    if not spoken:
        return []
    times = _align_words(spoken, samples, sr)
    out: list[WordTiming] = []
    i = 0
    for tok in tokens:
        n = len(tok.spoken)
        start, end = times[i][0], times[i + n - 1][1]
        out.append(WordTiming(tok.display, round(start, 3), round(end, 3), tok.emph))
        i += n
    return out
