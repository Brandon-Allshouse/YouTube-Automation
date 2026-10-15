"""Distinctness guard: block near-duplicates within and across channels.

YouTube's inauthentic-content policy targets mass-produced, repetitive, template-driven channels,
so a script is compared against:

* the same channel's last N videos (``config/rules.yaml: distinctness``), threshold
  ``max_similarity_same_channel``
* every other channel's videos, stricter threshold ``max_similarity_cross_channel``

Signals: narration similarity (word TF-IDF incl. bigrams + character 4-grams, our own
``src.common.textsim``), title similarity, and structure similarity (sequence of segment visual
tags), which catches template reuse even when the wording differs.

CLI::

    python -m src.compliance.distinctness check --script data/scripts/x.yaml
    python -m src.compliance.distinctness register --script data/scripts/x.yaml --video-id v-123

``db set-script`` runs ``check`` (refusing a veto) and ``register`` on every script it attaches, so the
guard is enforced in code and its history always holds every live draft.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from src import db as dbm
from src.common.config import rules
from src.common.paths import validate_id
from src.common.textsim import similarity
from src.production.script import Script, load_script

TITLE_THRESHOLD = 0.70
STRUCTURE_THRESHOLD = 0.92


@dataclass
class Match:
    """A prior video that resembles the candidate."""

    video_id: str
    channel_id: str
    narration: float
    title: float
    structure: float


@dataclass
class Verdict:
    """Outcome of a distinctness check."""

    passed: bool
    max_same_channel: float = 0.0
    max_cross_channel: float = 0.0
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    top_matches: list[Match] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly form."""
        return asdict(self)


def fingerprint(script: Script) -> str:
    """Stored representation: titles, narration and the visual-tag sequence."""
    return json.dumps(
        {
            "titles": script.titles,
            "narration": script.narration,
            "tags": [s.tag for s in script.segments],
        }
    )


def _ensure_columns(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS fingerprints (video_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, "
        "text TEXT NOT NULL, created_at TEXT NOT NULL)"
    )


def history(conn: sqlite3.Connection) -> list[tuple[str, str, dict[str, Any]]]:
    """Stored fingerprints of every video that isn't rejected, newest first: (video_id, channel_id, parsed).

    Scripts awaiting review count too, so two drafts in one batch can't duplicate each other.
    """
    _ensure_columns(conn)
    rows = conn.execute(
        "SELECT f.video_id, f.channel_id, f.text FROM fingerprints f LEFT JOIN videos v ON v.id = f.video_id "
        "WHERE v.state IS NULL OR v.state != 'rejected' ORDER BY f.created_at DESC"
    ).fetchall()
    out = []
    for vid, ch, text in rows:
        try:
            out.append((vid, ch, json.loads(text)))
        except json.JSONDecodeError:
            continue
    return out


def check(script: Script, conn: sqlite3.Connection, *, exclude_video: str | None = None) -> Verdict:
    """Compare a script against history; fail if any threshold is exceeded."""
    r = rules().distinctness
    hist = [h for h in history(conn) if h[0] != exclude_video]
    same = [h for h in hist if h[1] == script.channel][: r.compare_last_n_videos]
    cross = [h for h in hist if h[1] != script.channel]
    corpus = [h[2]["narration"] for h in same + cross]
    tags = [s.tag for s in script.segments]
    verdict = Verdict(passed=True)
    matches: list[Match] = []
    for vid, ch, fp in same + cross:
        n = similarity(script.narration, fp["narration"], corpus)
        t = max((similarity(a, b) for a in script.titles for b in fp["titles"]), default=0.0)
        s = SequenceMatcher(None, tags, fp.get("tags", [])).ratio() if tags and fp.get("tags") else 0.0
        matches.append(Match(vid, ch, round(n, 3), round(t, 3), round(s, 3)))
        if ch == script.channel:
            verdict.max_same_channel = max(verdict.max_same_channel, n)
            if n > r.max_similarity_same_channel:
                verdict.reasons.append(f"narration {n:.2f} > {r.max_similarity_same_channel} vs same-channel {vid}")
        else:
            verdict.max_cross_channel = max(verdict.max_cross_channel, n)
            if n > r.max_similarity_cross_channel:
                verdict.reasons.append(f"narration {n:.2f} > {r.max_similarity_cross_channel} vs other-channel {vid}")
        if t > TITLE_THRESHOLD:
            verdict.reasons.append(f"title similarity {t:.2f} > {TITLE_THRESHOLD} vs {vid}")
        if s >= STRUCTURE_THRESHOLD and len(tags) >= 5:
            verdict.warnings.append(f"segment structure {s:.2f} matches {vid}: vary the format")
    verdict.max_same_channel = round(verdict.max_same_channel, 3)
    verdict.max_cross_channel = round(verdict.max_cross_channel, 3)
    verdict.top_matches = sorted(matches, key=lambda m: -m.narration)[:5]
    verdict.passed = not verdict.reasons
    return verdict


def register(script: Script, video_id: str, conn: sqlite3.Connection) -> None:
    """Store (or replace) a video's fingerprint. ``db set-script`` calls this for every script."""
    _ensure_columns(conn)
    with dbm.transaction(conn):
        conn.execute(
            "INSERT OR REPLACE INTO fingerprints(video_id, channel_id, text, created_at) VALUES (?, ?, ?, ?)",
            (
                validate_id(video_id, kind="video id"),
                script.channel,
                fingerprint(script),
                dbm.utcnow(),
            ),
        )


def main(argv: list[str] | None = None) -> int:
    """CLI: ``check`` (exit 1 on veto) or ``register``."""
    p = argparse.ArgumentParser(description="Distinctness guard")
    p.add_argument("--db", type=Path, default=None, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--script", type=Path, required=True)
    rg = sub.add_parser("register")
    rg.add_argument("--script", type=Path, required=True)
    rg.add_argument("--video-id", required=True)
    a = p.parse_args(argv)
    conn = dbm.connect(a.db)
    script = load_script(a.script)
    if a.cmd == "check":
        v = check(script, conn)
        print(json.dumps(v.as_dict(), indent=2))
        return 0 if v.passed else 1
    register(script, a.video_id, conn)
    print(json.dumps({"registered": a.video_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
