"""Bandit arms derived from a script by code, so every real video joins the learning loop.

An arm is ``niche|format|hook|length|visual``. The scriptwriter declares the niche and hook type in
the script (both validated against fixed vocabularies); the length bucket and visual mix are computed
here from the narration and the visual components, so an agent can't mislabel them. Production
refines the length bucket with the real narration duration.

``python -m src.learning.arms --channel atlas`` lists candidate arms for a channel (for exploration).
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.production.script import Script

HOOKS = ("question", "statement", "number", "stakes", "story")
VISUAL_MIXES = ("maps-heavy", "data-heavy", "diagram-heavy", "type-heavy", "mixed")
SHORT_BUCKETS = (35, 45, 55)
LONG_BUCKETS = (6, 8, 10, 12, 14)
WORDS_PER_SECOND = 2.65  # fallback; each channel sets its measured rate (writing.words_per_second)
HEAVY_SHARE = 0.45  # a category owning at least this share of the narration names the mix
# Visual mixes each channel's identity supports (atlas tells stories with maps; neon with diagrams).
CHANNEL_VISUALS = {
    "atlas": ("maps-heavy", "data-heavy", "type-heavy", "mixed"),
    "neon": ("data-heavy", "diagram-heavy", "type-heavy", "mixed"),
}

# Component → visual category. Captions and backgrounds are on every video, so they don't count.
_CATEGORY = {
    "AnimatedMap": "maps",
    "LineChartReveal": "data",
    "BarRace": "data",
    "StatCounter": "data",
    "ComparisonSplit": "data",
    "Timeline": "data",
    "DiagramBuild": "diagram",
    "IconScene": "diagram",
    "KineticTitle": "type",
    "ListReveal": "type",
    "QuoteCard": "type",
}
_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")  # unicode letters/digits; isn't = 1 word
_MARKUP = re.compile(r"\{([^|}]*)\|([^}]*)\}")


def spoken_words(text: str) -> int:
    """Words the voice will say (``{display|spoken}`` counts the spoken side)."""
    return len(_WORD.findall(_MARKUP.sub(r"\2", text)))


def words_per_second(channel: str) -> float:
    """The channel's measured narration rate (voices differ: am_fenrir ≈ 2.5, af_heart ≈ 2.8)."""
    from src.common.channels import load_channel

    try:
        return load_channel(channel).writing.words_per_second
    except (OSError, ValueError):  # unknown channel (e.g. tests): use the fallback
        return WORDS_PER_SECOND


def estimate_duration(script: Script) -> float:
    """Rough narration length in seconds before any audio exists."""
    return sum(spoken_words(s.text) for s in script.segments) / words_per_second(script.channel)


def length_bucket(fmt: str, duration_s: float) -> str:
    """``s35|s45|s55`` for Shorts (nearest), ``l6..l14`` minutes for long-form."""
    if fmt == "short":
        return f"s{min(SHORT_BUCKETS, key=lambda b: abs(b - duration_s))}"
    return f"l{min(LONG_BUCKETS, key=lambda b: abs(b - duration_s / 60))}"


def visual_mix(script: Script) -> str:
    """Dominant visual category, weighted by how much narration each segment carries."""
    share: dict[str, float] = defaultdict(float)
    for seg in script.segments:
        cats = {_CATEGORY[t] for el in seg.visual if isinstance(t := el.get("type"), str) and t in _CATEGORY}
        words = max(spoken_words(seg.text), 1)
        for c in cats:
            share[c] += words / len(cats)
    total = sum(share.values())
    if not total:
        return "mixed"
    top, val = max(sorted(share.items()), key=lambda kv: kv[1])
    return f"{top}-heavy" if val / total >= HEAVY_SHARE else "mixed"


def arm_for(script: Script, duration_s: float | None = None) -> str:
    """The script's arm id. ``duration_s`` (real narration length) overrides the estimate.

    Raises:
        ValueError: The script does not declare a niche or hook.
    """
    if not script.niche or not script.hook:
        raise ValueError("script must declare `niche` and `hook` to join the bandit")
    dur = estimate_duration(script) if duration_s is None else duration_s
    return "|".join((script.niche, script.format, script.hook, length_bucket(script.format, dur), visual_mix(script)))


def check_niche(script: Script) -> None:
    """Require the script's niche to be a seed niche owned by the script's channel.

    Raises:
        ValueError: Unknown niche or a niche that belongs to another channel.
    """
    from src.research.common import load_niches

    if script.niche is None:
        return
    owner = {n.id: n.channel for n in load_niches()}
    if script.niche not in owner:
        raise ValueError(f"unknown niche '{script.niche}' (see config/seed_niches.yaml)")
    if owner[script.niche] != script.channel:
        raise ValueError(f"niche '{script.niche}' belongs to channel '{owner[script.niche]}'")


def candidates(channel: str, fmt: str = "short") -> list[str]:
    """Every arm a channel could try (its niches × hooks × lengths × visual mixes)."""
    from src.research.common import load_niches

    return [
        f"{n.id}|{fmt}|{h}|{ln}|{v}"
        for n in load_niches()
        if n.channel == channel
        for h in HOOKS
        for ln in lengths_for(fmt)
        for v in CHANNEL_VISUALS.get(channel, VISUAL_MIXES)
    ]


def lengths_for(fmt: str) -> tuple[str, ...]:
    """Length buckets of a format."""
    return tuple(f"s{b}" for b in SHORT_BUCKETS) if fmt == "short" else tuple(f"l{b}" for b in LONG_BUCKETS)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: list candidate arms for a channel."""
    p = argparse.ArgumentParser(description="Candidate bandit arms")
    p.add_argument("--channel", required=True)
    p.add_argument("--format", choices=("short", "long"), default="short")
    a = p.parse_args(argv)
    print(json.dumps(candidates(a.channel, a.format), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
