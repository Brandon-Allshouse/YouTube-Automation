"""Predictive scorer: features → expected CTR / average view %, once enough history exists.

Ridge regression in closed form (NumPy) on one-hot arm components (niche, hook, length, visual
mix) plus channel. Below ``MIN_VIDEOS`` it refuses to predict and says so: guessing from a handful
of videos would mislead more than help.

``python -m src.learning.predictor fit`` / ``predict --arm "niche|short|hook|len|visual" --channel atlas``
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from src import db as dbm

MIN_VIDEOS = 100
TARGETS = ("ctr", "avg_view_pct")


class InsufficientDataError(RuntimeError):
    """Raised when there are too few videos to fit honestly."""


def featurize(arm: str, channel: str, vocab: list[str]) -> npt.NDArray[np.float64]:
    """One-hot vector over ``vocab`` for an arm id + channel (unknown tokens ignored)."""
    toks = {f"{i}:{p}" for i, p in enumerate(arm.split("|"))} | {f"ch:{channel}"}
    return np.array([1.0 if v in toks else 0.0 for v in vocab] + [1.0])  # + bias


@dataclass
class Model:
    """Fitted ridge weights per target."""

    vocab: list[str]
    weights: dict[str, list[float]]
    n: int

    def predict(self, arm: str, channel: str) -> dict[str, float]:
        """Predicted targets for an arm on a channel."""
        x = featurize(arm, channel, self.vocab)
        return {t: float(np.dot(x, np.asarray(w))) for t, w in self.weights.items()}


def fit(conn: sqlite3.Connection, *, min_videos: int = MIN_VIDEOS, lam: float = 1.0) -> Model:
    """Fit ridge models from 48 h stats.

    Raises:
        InsufficientDataError: fewer than ``min_videos`` labelled videos.
    """
    rows = conn.execute(
        "SELECT v.arm_id, v.channel_id, s.ctr, s.avg_view_pct FROM videos v JOIN stats s ON s.video_id = v.id "
        "WHERE v.arm_id IS NOT NULL AND s.hours_since_publish = 48"
    ).fetchall()
    if len(rows) < min_videos:
        raise InsufficientDataError(f"need ≥ {min_videos} videos with 48 h stats, have {len(rows)}")
    vocab = sorted(
        {f"{i}:{p}" for arm, *_ in rows for i, p in enumerate(arm.split("|"))} | {f"ch:{r[1]}" for r in rows}
    )
    x = np.stack([featurize(r[0], r[1], vocab) for r in rows])
    weights = {}
    for k, t in enumerate(TARGETS):
        y = np.array([float(r[2 + k] or 0) for r in rows])
        a = x.T @ x + lam * np.eye(x.shape[1])
        weights[t] = np.linalg.solve(a, x.T @ y).tolist()
    return Model(vocab, weights, len(rows))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    p = argparse.ArgumentParser(description="Predictive scorer")
    p.add_argument("--db", type=Path, default=None, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fit")
    pr = sub.add_parser("predict")
    pr.add_argument("--arm", required=True)
    pr.add_argument("--channel", required=True)
    a = p.parse_args(argv)
    conn = dbm.connect(a.db)
    try:
        model = fit(conn)
    except InsufficientDataError as exc:
        print(json.dumps({"status": "not enough data", "detail": str(exc)}))
        return 0
    if a.cmd == "fit":
        print(json.dumps({"fitted_on": model.n, "features": len(model.vocab)}))
    else:
        print(json.dumps(model.predict(a.arm, a.channel)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
