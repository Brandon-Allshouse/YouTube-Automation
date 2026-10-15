"""Writing checks for scripts (the Quality Bar's writing section).

* **Hook:** the first sentence is short (≤ 16 words) and carries a hook signal: a question, a
  number, a contrast/surprise word, or direct address.
* **Readability:** Flesch reading ease (our syllable counter) ≥ 55 for Shorts (plain spoken English).
* **Rhythm:** sentence-length standard deviation ≥ 2.5 words (no robotic uniform sentences).
* **Banned filler:** global + channel ``banned_phrases`` must not appear.
* **Re-hooks (long-form):** a question or pivot ("but", "here's", "so why") at least every ~30 s.
* **Payoff:** the last segment resolves (no trailing question as the final line).
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any

from src.audio.captions import spoken_text, syllables
from src.audio.tts import split_sentences
from src.common.channels import load_channel
from src.production.script import Script

GLOBAL_BANNED = (
    "in today's video", "let's dive in", "without further ado", "smash that like", "don't forget to subscribe",
    "you won't believe", "mind-blowing", "game-changer", "in this day and age", "at the end of the day",
)  # fmt: skip
HOOK_WORDS = re.compile(
    r"\b(you|your|never|isn't|aren't|wasn't|actually|really|lie|lies|lying|wrong|secret|nobody|why|how|what|"
    r"bigger|smaller|than|but|only|first|last|worst|best)\b",
    re.I,
)
PIVOTS = re.compile(r"\?|\b(but|here's|so why|the catch|turns out|which means)\b", re.I)
WORDS_PER_S = 2.4


@dataclass
class WritingReport:
    """Findings and pass/fail per rule."""

    checks: dict[str, bool] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """All checks passed."""
        return all(self.checks.values())


def flesch(sentences: list[str]) -> float:
    """Flesch reading ease."""
    words = [w for s in sentences for w in re.findall(r"[A-Za-z0-9']+", s)]
    if not words or not sentences:
        return 0.0
    syl = sum(syllables(w) for w in words)
    return 206.835 - 1.015 * (len(words) / len(sentences)) - 84.6 * (syl / len(words))


def check_script(script: Script) -> WritingReport:
    """Run all writing checks on a script."""
    rep = WritingReport()
    sentences = [spoken_text(s) for seg in script.segments for s in split_sentences(seg.text)]
    first = sentences[0] if sentences else ""
    first_words = len(first.split())
    hook_signal = "?" in first or bool(re.search(r"\d", first)) or bool(HOOK_WORDS.search(first))
    rep.checks["hook_first_sentence"] = first_words <= 16 and hook_signal
    rep.metrics["first_sentence_words"] = first_words
    fre = flesch(sentences)
    rep.metrics["flesch_reading_ease"] = round(fre, 1)
    rep.checks["readability"] = fre >= (55 if script.format == "short" else 50)
    lengths = [len(s.split()) for s in sentences]
    sd = statistics.pstdev(lengths) if len(lengths) > 1 else 0.0
    rep.metrics["sentence_len_sd"] = round(sd, 2)
    rep.checks["sentence_variety"] = sd >= 2.5
    text = " ".join(sentences).lower()
    banned = list(GLOBAL_BANNED) + [b.lower() for b in load_channel(script.channel).writing.banned_phrases]
    hits = sorted({b for b in banned if b in text})
    rep.checks["no_banned_phrases"] = not hits
    if hits:
        rep.notes.append("banned phrases: " + ", ".join(hits))
    words = len(text.split())
    rep.metrics["estimated_duration_s"] = round(words / WORDS_PER_S, 1)
    if script.format == "long":
        gaps, last, t = [], 0.0, 0.0
        for s in sentences:
            t += len(s.split()) / WORDS_PER_S
            if PIVOTS.search(s):
                gaps.append(t - last)
                last = t
        gaps.append(t - last)
        rep.metrics["max_rehook_gap_s"] = round(max(gaps), 1)
        rep.checks["rehooks"] = max(gaps) <= 35
    rep.checks["payoff_resolves"] = not sentences[-1].rstrip().endswith("?") if sentences else False
    rep.checks["has_sources"] = len(script.sources) >= 1
    return rep


def as_dict(rep: WritingReport) -> dict[str, Any]:
    """JSON-friendly form."""
    return {"passed": rep.passed, "checks": rep.checks, "metrics": rep.metrics, "notes": rep.notes}
