"""Multi-armed bandit over video formats (Beta-Bernoulli Thompson sampling).

Unrelated to the ``bandit`` security linter; import it as ``src.learning.bandit``.

An *arm* is ``niche|format|hook|length|visual-mix`` (e.g. ``map-oddities|short|question|s45|maps-heavy``).
A video is a *success* when, at 48 h, it beats its channel's median views **and** keeps at least
the channel's median average-view-percentage (both leading indicators, so the bandit can't learn to
chase clickbait that viewers abandon). Each arm's posterior is Beta(1 + successes, 1 + failures).

Planned upgrade (≥ ~50 videos): hierarchical Thompson sampling sharing strength across arms that
share a niche or hook.

``python -m src.learning.bandit update | show | recommend --n 3 | plan --channel atlas --n 3``
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from src import db as dbm


@dataclass(frozen=True)
class ArmState:
    """Posterior of one arm."""

    arm_id: str
    alpha: float
    beta: float
    pulls: int

    @property
    def mean(self) -> float:
        """Posterior mean success rate."""
        return self.alpha / (self.alpha + self.beta)


def outcomes(conn: sqlite3.Connection) -> list[tuple[str, bool]]:
    """(arm_id, success) for every published video with 48 h stats."""
    rows = conn.execute(
        "SELECT v.channel_id, v.arm_id, s.views, s.avg_view_pct FROM videos v JOIN stats s ON s.video_id = v.id "
        "WHERE v.arm_id IS NOT NULL AND s.hours_since_publish = 48"
    ).fetchall()
    by_channel: dict[str, list[tuple[str, int, float]]] = defaultdict(list)
    for ch, arm, views, avp in rows:
        by_channel[ch].append((arm, int(views), float(avp or 0)))
    out = []
    for vids in by_channel.values():
        med_v = statistics.median(v for _, v, _ in vids)
        med_p = statistics.median(p for _, _, p in vids)
        out += [(arm, views > med_v and avp >= med_p) for arm, views, avp in vids]
    return out


def update(conn: sqlite3.Connection) -> list[ArmState]:
    """Recompute all arm posteriors from outcomes (idempotent) and store them."""
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for arm, ok in outcomes(conn):
        counts[arm][0 if ok else 1] += 1
    states = [ArmState(arm, 1.0 + s, 1.0 + f, s + f) for arm, (s, f) in sorted(counts.items())]
    with dbm.transaction(conn):
        for st in states:
            conn.execute(
                "INSERT OR REPLACE INTO bandit_arms(arm_id, alpha, beta, pulls, updated_at) VALUES (?, ?, ?, ?, ?)",
                (st.arm_id, st.alpha, st.beta, st.pulls, dbm.utcnow()),
            )
    return states


def load(conn: sqlite3.Connection) -> list[ArmState]:
    """Stored arm posteriors."""
    return [
        ArmState(r[0], r[1], r[2], r[3]) for r in conn.execute("SELECT arm_id, alpha, beta, pulls FROM bandit_arms")
    ]


def recommend(states: list[ArmState], n: int, *, seed: int = 0, candidates: list[str] | None = None) -> list[str]:
    """Thompson sampling: draw from each posterior, return the top-n arms.

    ``candidates`` adds untried arms (prior Beta(1, 1)) so new formats get explored.
    """
    rng = np.random.default_rng(seed)
    pool = {s.arm_id: s for s in states}
    for c in candidates or []:
        pool.setdefault(c, ArmState(c, 1.0, 1.0, 0))
    draws = {a: float(rng.beta(s.alpha, s.beta)) for a, s in pool.items()}
    return sorted(draws, key=lambda a: -draws[a])[:n]


@dataclass(frozen=True)
class PlanSlot:
    """One recommended video formula from :func:`plan`."""

    arm: str
    niche: str
    hook: str
    length: str
    visual: str
    explore: bool  # True when at least one dimension was picked from an untried value


def _dimension_counts(rows: list[tuple[str, bool]], fmt: str) -> dict[int, dict[str, list[int]]]:
    """Successes/failures per (arm position, value) over arms of one format."""
    counts: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for arm, ok in rows:
        parts = arm.split("|")
        if len(parts) != 5 or parts[1] != fmt:
            continue
        for pos in (0, 2, 3, 4):
            counts[pos][parts[pos]][0 if ok else 1] += 1
    return counts


def plan(
    conn: sqlite3.Connection,
    channel: str,
    *,
    fmt: str = "short",
    n: int = 3,
    seed: int = 0,
    niche_priors: dict[str, float] | None = None,
    top_niches: int = 4,
    prior_strength: float = 4.0,
) -> list[PlanSlot]:
    """Recommend ``n`` distinct video formulas for a channel with factored Thompson sampling.

    A full arm (niche × hook × length × visual) has hundreds of combinations, far too many to learn
    one by one at a few videos a week. Each dimension is learned on its own instead: every value
    has a Beta posterior over all outcomes that share it (hooks and lengths learn across channels),
    and a slot draws one sample per value and takes the best of each dimension.

    Niche priors come from research (``niche_score``, 0-1) as Beta(1 + k·s, 1 + k·(1 - s)), so
    the research ranking steers exploration until real results outweigh it. Only the channel's
    ``top_niches`` best-researched niches are probed (CLAUDE.md: 4-8 niches across the portfolio).

    Returns:
        Up to ``n`` distinct slots (fewer only if the option space is smaller).
    """
    from src.learning.arms import CHANNEL_VISUALS, HOOKS, VISUAL_MIXES, lengths_for
    from src.research.common import load_niches

    rng = np.random.default_rng(seed)
    priors = niche_priors or {}
    niches = [x.id for x in load_niches() if x.channel == channel]
    if not niches:
        raise ValueError(f"no seed niches for channel '{channel}'")
    niches = sorted(niches, key=lambda nid: (-priors.get(nid, 0.5), nid))[: max(1, top_niches)]
    options = {
        0: niches,
        2: list(HOOKS),
        3: list(lengths_for(fmt)),
        4: list(CHANNEL_VISUALS.get(channel, VISUAL_MIXES)),
    }
    counts = _dimension_counts(outcomes(conn), fmt)
    out: list[PlanSlot] = []
    seen: set[str] = set()
    for _ in range(n * 20):
        if len(out) >= n:
            break
        pick: dict[int, str] = {}
        explore = False
        for pos, values in options.items():
            draws = {}
            for v in values:
                s, f = counts[pos].get(v, [0, 0])  # .get: never inserts, so "untried" stays untried
                a0, b0 = 1.0, 1.0
                if pos == 0 and v in priors:
                    p = min(max(priors[v], 0.0), 1.0)
                    a0, b0 = 1.0 + prior_strength * p, 1.0 + prior_strength * (1 - p)
                draws[v] = float(rng.beta(a0 + s, b0 + f))
            pick[pos] = max(values, key=lambda v: draws[v])
            explore |= pick[pos] not in counts[pos]
        arm = "|".join((pick[0], fmt, pick[2], pick[3], pick[4]))
        if arm in seen:
            continue
        seen.add(arm)
        out.append(PlanSlot(arm, pick[0], pick[2], pick[3], pick[4], explore))
    return out


def research_niche_priors() -> dict[str, float]:
    """Niche scores (0-1) from the latest live ``niche_score`` output; empty if none."""
    from src.research.common import latest_output

    data = latest_output("niche_score", True) or {}
    ranking = data.get("ranking")
    if not isinstance(ranking, list):
        return {}
    return {
        str(r["niche"]): float(r["score"])
        for r in ranking
        if isinstance(r, dict) and isinstance(r.get("niche"), str) and isinstance(r.get("score"), int | float)
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    p = argparse.ArgumentParser(description="Format bandit")
    p.add_argument("--db", type=Path, default=None, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("update")
    sub.add_parser("show")
    r = sub.add_parser("recommend")
    r.add_argument("--n", type=int, default=3)
    r.add_argument("--seed", type=int, default=0)
    pl = sub.add_parser("plan", help="next video formulas for a channel (factored Thompson sampling)")
    pl.add_argument("--channel", required=True)
    pl.add_argument("--format", choices=("short", "long"), default="short")
    pl.add_argument("--n", type=int, default=3)
    pl.add_argument("--seed", type=int, default=0)
    pl.add_argument("--top-niches", type=int, default=4)
    a = p.parse_args(argv)
    conn = dbm.connect(a.db)
    if a.cmd == "update":
        states = update(conn)
        print(json.dumps({"arms": len(states)}))
    elif a.cmd == "show":
        states = sorted(load(conn), key=lambda s: -s.mean)
        print(json.dumps([{"arm": s.arm_id, "mean": round(s.mean, 3), "pulls": s.pulls} for s in states], indent=1))
    elif a.cmd == "plan":
        slots = plan(
            conn,
            a.channel,
            fmt=a.format,
            n=max(1, min(a.n, 20)),
            seed=a.seed,
            niche_priors=research_niche_priors(),
            top_niches=a.top_niches,
        )
        print(json.dumps([s.__dict__ for s in slots], indent=1))
    else:
        print(json.dumps({"recommend": recommend(load(conn), max(1, a.n), seed=a.seed)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
