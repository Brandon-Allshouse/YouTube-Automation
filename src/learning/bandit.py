"""Multi-armed bandit over video formats (Beta-Bernoulli Thompson sampling).

Unrelated to the ``bandit`` security linter; import it as ``src.learning.bandit``.

An *arm* is ``niche|format|hook|length|visual-mix`` (e.g. ``map-oddities|short|question|s45|maps-heavy``).
A video is a *success* when, at 48 h, it beats its channel's median views **and** keeps at least
the channel's median average-view-percentage (both leading indicators, so the bandit can't learn to
chase clickbait that viewers abandon). Each arm's posterior is Beta(1 + successes, 1 + failures).

Planned upgrade (≥ ~50 videos): hierarchical Thompson sampling sharing strength across arms that
share a niche or hook.

``python -m src.learning.bandit update | recommend --channel atlas --n 3 | show``
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
    a = p.parse_args(argv)
    conn = dbm.connect(a.db)
    if a.cmd == "update":
        states = update(conn)
        print(json.dumps({"arms": len(states)}))
    elif a.cmd == "show":
        states = sorted(load(conn), key=lambda s: -s.mean)
        print(json.dumps([{"arm": s.arm_id, "mean": round(s.mean, 3), "pulls": s.pulls} for s in states], indent=1))
    else:
        print(json.dumps({"recommend": recommend(load(conn), max(1, a.n), seed=a.seed)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
