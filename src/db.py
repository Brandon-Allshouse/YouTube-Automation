"""SQLite persistence for the pipeline, plus a small JSON CLI that agents call.

Design rules (see src/CLAUDE.md):
* Parameterized queries only: no string-formatted SQL anywhere.
* Every video moves through an explicit state machine; every transition is audited in
  ``video_events``. Jobs are idempotent: a stage checks state before doing work.
* The human review gate lives here in code: a video cannot enter ``reviewed`` without a named
  human approver, and the ``agent`` actor is refused.

CLI examples::

    python -m src.db init
    python -m src.db add-video --channel atlas --format short --title "..."
    python -m src.db list --state scripted
    python -m src.db transition <video_id> voiced
    python -m src.db quota
"""

from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.common.paths import DATA_DIR, ensure_dir, ensure_within, validate_id

DEFAULT_DB = DATA_DIR / "pipeline.sqlite"

# Ordered production states. "specd" is the spec'd stage (scene spec written).
STATES: tuple[str, ...] = (
    "idea",
    "scripted",
    "reviewed",
    "voiced",
    "specd",
    "rendered",
    "checked",
    "scheduled",
    "published",
)
TERMINAL_SIDE_STATES: tuple[str, ...] = ("rejected", "failed")

# Allowed transitions: forward one step, plus explicit rework loops and side exits.
_FORWARD = dict(itertools.pairwise(STATES))
_REWORK = {
    "reviewed": {"scripted"},  # owner requested changes
    "specd": {"voiced"},
    "rendered": {"specd"},
    "checked": {"specd", "rendered"},  # quality check failed -> re-spec / re-render
    "scheduled": {"checked"},  # unschedule
    "failed": {"idea", "scripted"},  # retry requires going back through human review
}

AGENT_ACTORS = frozenset({"agent", "claude", "bot", "cron"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    niche TEXT NOT NULL,
    theme TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    format TEXT NOT NULL CHECK (format IN ('short','long')),
    title TEXT NOT NULL,
    state TEXT NOT NULL,
    seed INTEGER NOT NULL,
    arm_id TEXT,
    script_path TEXT,
    spec_path TEXT,
    output_path TEXT,
    approved_by TEXT,
    approved_at TEXT,
    scheduled_for TEXT,
    youtube_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_videos_state ON videos(state);
CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel_id);
CREATE TABLE IF NOT EXISTS video_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    actor TEXT NOT NULL,
    note TEXT,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    video_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('running','done','failed')),
    detail TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS api_quota (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    units INTEGER NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quota_day ON api_quota(day);
CREATE TABLE IF NOT EXISTS api_cache (
    key TEXT PRIMARY KEY,
    endpoint TEXT NOT NULL,
    response_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('title','thumbnail')),
    label TEXT NOT NULL,
    content TEXT NOT NULL,
    impressions INTEGER NOT NULL DEFAULT 0,
    clicks INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    pulled_at TEXT NOT NULL,
    hours_since_publish REAL NOT NULL,
    views INTEGER NOT NULL,
    impressions INTEGER,
    ctr REAL,
    avg_view_pct REAL,
    retained_30s REAL,
    likes INTEGER,
    comments INTEGER
);
CREATE TABLE IF NOT EXISTS bandit_arms (
    arm_id TEXT PRIMARY KEY,
    alpha REAL NOT NULL,
    beta REAL NOT NULL,
    pulls INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fingerprints (
    video_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class TransitionError(ValueError):
    """Raised for an illegal state transition or a review-gate violation."""


def utcnow() -> str:
    """Current UTC time as ISO-8601 with seconds precision."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open (and initialise) the pipeline database.

    Args:
        path: Database file; defaults to ``data/pipeline.sqlite``. ``:memory:`` is allowed in tests.
            An explicit path (the hidden ``--db`` option of every CLI) must sit under ``data/`` or
            the system temp directory, so a CLI argument can't create or open files elsewhere.

    Raises:
        ValueError: ``path`` is outside the allowed directories.
    """
    if path is not None and str(path) != ":memory:":
        import tempfile

        resolved = Path(path).resolve()
        allowed = (DATA_DIR.resolve(), Path(tempfile.gettempdir()).resolve())
        if not any(resolved == a or a in resolved.parents for a in allowed):
            raise ValueError("database path must be under data/ or the temp directory")
    target = path or DEFAULT_DB
    if str(target) != ":memory:":
        ensure_dir(Path(target).parent)
    conn = sqlite3.connect(str(target), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(target) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(videos)")}
    # sha256 of the approved script file and of the quality-checked render (security review: bind the
    # human approval to the exact content that gets produced and uploaded)
    if "script_sha256" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN script_sha256 TEXT")
    if "output_sha256" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN output_sha256 TEXT")
    return conn


def file_sha256(path: Path) -> str:
    """Hex SHA-256 of a file."""
    import hashlib

    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on any exception."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def allowed_transitions(state: str) -> set[str]:
    """Return the set of states reachable from ``state`` in one transition."""
    nxt: set[str] = set()
    if state in _FORWARD:
        nxt.add(_FORWARD[state])
    nxt |= _REWORK.get(state, set())
    if state not in ("published", "rejected"):
        nxt |= {"failed"}
    if state in ("idea", "scripted", "reviewed"):
        nxt.add("rejected")
    return nxt


def add_channel(conn: sqlite3.Connection, channel_id: str, name: str, niche: str, theme: str) -> None:
    """Insert or update a channel row."""
    validate_id(channel_id, kind="channel id")
    with transaction(conn):
        conn.execute(
            "INSERT INTO channels(id, name, niche, theme, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, niche=excluded.niche, "
            "theme=excluded.theme",
            (channel_id, name, niche, theme, utcnow()),
        )


def add_video(
    conn: sqlite3.Connection,
    channel_id: str,
    fmt: str,
    title: str,
    *,
    seed: int | None = None,
    video_id: str | None = None,
    arm_id: str | None = None,
) -> str:
    """Create a video in state ``idea`` and return its id.

    Raises:
        ValueError: On invalid ids or format.
    """
    validate_id(channel_id, kind="channel id")
    if fmt not in ("short", "long"):
        raise ValueError("format must be 'short' or 'long'")
    vid = validate_id(video_id or f"v-{uuid.uuid4().hex[:10]}", kind="video id")
    now = utcnow()
    with transaction(conn):
        conn.execute(
            "INSERT INTO videos(id, channel_id, format, title, state, seed, arm_id, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, 'idea', ?, ?, ?, ?)",
            (vid, channel_id, fmt, title[:300], seed if seed is not None else 0, arm_id, now, now),
        )
        conn.execute(
            "INSERT INTO video_events(video_id, from_state, to_state, actor, note, at) "
            "VALUES (?, NULL, 'idea', 'pipeline', 'created', ?)",
            (vid, now),
        )
    return vid


def get_video(conn: sqlite3.Connection, video_id: str) -> dict[str, Any] | None:
    """Return a video row as a dict, or None."""
    row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    return dict(row) if row else None


def transition(
    conn: sqlite3.Connection,
    video_id: str,
    to_state: str,
    *,
    actor: str = "pipeline",
    note: str = "",
    approved_by: str | None = None,
) -> dict[str, Any]:
    """Move a video to ``to_state`` if legal, enforcing the human review gate.

    Idempotent: transitioning to the current state is a no-op that returns the row.

    Raises:
        TransitionError: Unknown video, illegal transition, or a review/publish gate violation.
    """
    video = get_video(conn, video_id)
    if video is None:
        raise TransitionError("unknown video")
    current = video["state"]
    if current == to_state:
        return video
    if to_state not in allowed_transitions(current):
        raise TransitionError(f"illegal transition {current} -> {to_state}")

    if to_state == "reviewed":
        if not approved_by or approved_by.strip().lower() in AGENT_ACTORS:
            raise TransitionError("review gate: a named human approver is required")
        if actor.strip().lower() in AGENT_ACTORS:
            raise TransitionError("review gate: agents cannot approve scripts")
    if to_state in ("scheduled", "published") and not video["approved_by"]:
        raise TransitionError("publish gate: video was never approved by a human")

    now = utcnow()
    with transaction(conn):
        if to_state == "reviewed":
            conn.execute(
                "UPDATE videos SET state = ?, approved_by = ?, approved_at = ?, updated_at = ? WHERE id = ?",
                (to_state, approved_by, now, now, video_id),
            )
        elif to_state in ("scripted", "idea"):
            # Any return to drafting invalidates the previous approval and checked render.
            conn.execute(
                "UPDATE videos SET state = ?, approved_by = NULL, approved_at = NULL, script_sha256 = NULL, "
                "output_sha256 = NULL, updated_at = ? WHERE id = ?",
                (to_state, now, video_id),
            )
        else:
            conn.execute(
                "UPDATE videos SET state = ?, updated_at = ? WHERE id = ?",
                (to_state, now, video_id),
            )
        conn.execute(
            "INSERT INTO video_events(video_id, from_state, to_state, actor, note, at) VALUES (?, ?, ?, ?, ?, ?)",
            (video_id, current, to_state, actor[:64], note[:500], now),
        )
    updated = get_video(conn, video_id)
    if updated is None:  # just written in this connection; explicit check survives python -O
        raise TransitionError("video vanished during transition")
    return updated


_PATH_COLUMNS = frozenset({"script_path", "spec_path", "output_path"})


def set_path(conn: sqlite3.Connection, video_id: str, column: str, value: str) -> None:
    """Record an artifact path for a video. ``column`` is checked against a fixed allowlist."""
    if column not in _PATH_COLUMNS:
        raise ValueError("unknown path column")
    # Column name comes from the allowlist above, never from input, so this is not injectable.
    sql = {
        "script_path": "UPDATE videos SET script_path = ?, updated_at = ? WHERE id = ?",
        "spec_path": "UPDATE videos SET spec_path = ?, updated_at = ? WHERE id = ?",
        "output_path": "UPDATE videos SET output_path = ?, updated_at = ? WHERE id = ?",
    }[column]
    with transaction(conn):
        conn.execute(sql, (value, utcnow(), video_id))


def check_script_matches(video: dict[str, Any], script_file: Path) -> None:
    """Validate a script file and require it to belong to this video's channel and format.

    A script written for another channel would otherwise be rendered in that channel's style and
    uploaded with the wrong account.

    Raises:
        ValueError: Invalid script, or channel/format mismatch.
    """
    from src.production.script import load_script

    try:
        script = load_script(script_file)
    except Exception as exc:
        raise ValueError(f"invalid script: {str(exc)[:300]}") from None
    if script.channel != video["channel_id"]:
        raise ValueError(f"script is for channel '{script.channel}', video is on '{video['channel_id']}'")
    if script.format != video["format"]:
        raise ValueError(f"script format '{script.format}' does not match video format '{video['format']}'")


def list_videos(
    conn: sqlite3.Connection, *, state: str | None = None, channel_id: str | None = None
) -> list[dict[str, Any]]:
    """List videos filtered by optional state and/or channel, newest first."""
    rows = conn.execute(
        "SELECT * FROM videos WHERE (? IS NULL OR state = ?) AND (? IS NULL OR channel_id = ?) "
        "ORDER BY created_at DESC",
        (state, state, channel_id, channel_id),
    ).fetchall()
    return [dict(r) for r in rows]


# --- jobs -----------------------------------------------------------------------------------


def start_job(conn: sqlite3.Connection, job_id: str, kind: str, video_id: str | None) -> None:
    """Record that a job started."""
    with transaction(conn):
        conn.execute(
            "INSERT OR REPLACE INTO jobs(id, kind, video_id, status, started_at) VALUES (?, ?, ?, 'running', ?)",
            (job_id, kind, video_id, utcnow()),
        )


def finish_job(conn: sqlite3.Connection, job_id: str, *, ok: bool, detail: str = "") -> None:
    """Record job completion."""
    with transaction(conn):
        conn.execute(
            "UPDATE jobs SET status = ?, detail = ?, finished_at = ? WHERE id = ?",
            ("done" if ok else "failed", detail[:2000], utcnow(), job_id),
        )


# --- quota + cache --------------------------------------------------------------------------


def quota_used_today(conn: sqlite3.Connection, day: str | None = None) -> int:
    """Total YouTube API units recorded for ``day`` (UTC date, default today)."""
    d = day or datetime.now(UTC).date().isoformat()
    row = conn.execute("SELECT COALESCE(SUM(units), 0) FROM api_quota WHERE day = ?", (d,)).fetchone()
    return int(row[0])


def record_quota(conn: sqlite3.Connection, endpoint: str, units: int) -> None:
    """Record units spent on an endpoint call."""
    with transaction(conn):
        conn.execute(
            "INSERT INTO api_quota(day, endpoint, units, at) VALUES (?, ?, ?, ?)",
            (datetime.now(UTC).date().isoformat(), endpoint, units, utcnow()),
        )


def cache_get(conn: sqlite3.Connection, key: str, *, fetched_after: str = "") -> Any | None:
    """Return a cached API response if present, unexpired and fetched after ``fetched_after`` (ISO UTC)."""
    row = conn.execute(
        "SELECT response_json FROM api_cache WHERE key = ? AND expires_at > ? AND fetched_at > ?",
        (key, utcnow(), fetched_after),
    ).fetchone()
    return json.loads(row[0]) if row else None


def cache_put(conn: sqlite3.Connection, key: str, endpoint: str, response: Any, expires_at: str) -> None:
    """Store an API response in the cache."""
    with transaction(conn):
        conn.execute(
            "INSERT OR REPLACE INTO api_cache"
            "(key, endpoint, response_json, fetched_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, endpoint, json.dumps(response), utcnow(), expires_at),
        )


# --- CLI ------------------------------------------------------------------------------------


def _print(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, indent=2, default=str) + "\n")


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m src.db``. Output is JSON for agents."""
    p = argparse.ArgumentParser(prog="src.db", description=__doc__.splitlines()[0])
    p.add_argument("--db", type=Path, default=None, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    a = sub.add_parser("add-video")
    a.add_argument("--channel", required=True)
    a.add_argument("--format", choices=("short", "long"), required=True)
    a.add_argument("--title", required=True)
    a.add_argument("--seed", type=int, default=0)
    ls = sub.add_parser("list")
    ls.add_argument("--state", choices=STATES + TERMINAL_SIDE_STATES)
    ls.add_argument("--channel")
    g = sub.add_parser("get")
    g.add_argument("video_id")
    t = sub.add_parser("transition", help="non-review transitions (approval uses src.review.queue)")
    t.add_argument("video_id")
    # agents may only move drafts; reviewed/voiced/specd/rendered/checked/scheduled/published are set by
    # the review queue, production, quality checks, timing and publish code after their own gates
    t.add_argument("to_state", choices=("scripted", "rejected", "failed"))
    t.add_argument("--note", default="")
    ss = sub.add_parser("set-script", help="attach a script file (under data/scripts/) to a video")
    ss.add_argument("video_id")
    ss.add_argument("path")
    sub.add_parser("quota")
    args = p.parse_args(argv)

    conn = connect(args.db)
    try:
        if args.cmd == "init":
            _print({"ok": True})
        elif args.cmd == "add-video":
            _print({"video_id": add_video(conn, args.channel, args.format, args.title, seed=args.seed)})
        elif args.cmd == "list":
            _print(list_videos(conn, state=args.state, channel_id=args.channel))
        elif args.cmd == "get":
            _print(get_video(conn, args.video_id))
        elif args.cmd == "transition":
            _print(transition(conn, args.video_id, args.to_state, actor="agent", note=args.note))
        elif args.cmd == "set-script":
            script = ensure_within(Path(args.path), DATA_DIR / "scripts")
            cur = get_video(conn, args.video_id)
            if cur is None:
                raise ValueError("unknown video")
            if cur["state"] not in ("idea", "scripted"):
                raise ValueError("script can only be changed before approval")
            check_script_matches(cur, script)
            set_path(conn, args.video_id, "script_path", str(script.relative_to(DATA_DIR.parent)))
            _print({"ok": True})
        elif args.cmd == "quota":
            _print({"used_today": quota_used_today(conn)})
    except (TransitionError, ValueError) as exc:
        _print({"error": str(exc)})
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
