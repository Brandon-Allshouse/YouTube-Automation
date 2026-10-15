"""Pull performance stats for our published videos into SQLite.

* Fixture mode (default): loads ``tests/fixtures/analytics/videos.json`` (synthetic).
* Live mode (``--live``, needs the owner's OAuth): YouTube Analytics API ``reports.query`` for
  views, impressions, CTR, average view percentage and the audience-retention curve. Not run
  during the overnight build.

Rows go to ``stats`` (48 h leading indicators) and ``retention`` (100-point curve + segment plan).

``python -m src.analytics.pull_stats [--live]``
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from src import db as dbm
from src.common.log import get_logger, log_event, new_job_id
from src.common.paths import FIXTURES_DIR

log = get_logger("src.analytics.stats")

FIXTURE = FIXTURES_DIR / "analytics" / "videos.json"
ANALYTICS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"

RETENTION_SQL = """
CREATE TABLE IF NOT EXISTS retention (
    video_id TEXT PRIMARY KEY,
    duration_s REAL NOT NULL,
    curve_json TEXT NOT NULL,
    segments_json TEXT NOT NULL,
    pulled_at TEXT NOT NULL
)
"""


def ensure_tables(conn: sqlite3.Connection) -> None:
    """Create analytics tables if missing."""
    conn.execute(RETENTION_SQL)


def load_fixture_into(conn: sqlite3.Connection, path: Path = FIXTURE) -> int:
    """Insert fixture videos (as published, approved by 'fixture-owner'), 48 h stats and curves."""
    ensure_tables(conn)
    data = json.loads(path.read_text(encoding="utf-8"))
    n = 0
    with dbm.transaction(conn):
        for v in data["videos"]:
            conn.execute(
                "INSERT OR IGNORE INTO videos(id, channel_id, format, title, state, seed, arm_id, approved_by, "
                "approved_at, scheduled_for, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'published', 0, ?, 'fixture-owner', ?, ?, ?, ?)",
                (
                    v["id"],
                    v["channel_id"],
                    v["format"],
                    v["title"],
                    v["arm_id"],
                    v["published_at"],
                    v["published_at"],
                    v["published_at"],
                    v["published_at"],
                ),
            )
            st = v["stats_48h"]
            conn.execute("DELETE FROM stats WHERE video_id = ? AND hours_since_publish = 48", (v["id"],))
            conn.execute(
                "INSERT INTO stats(video_id, pulled_at, hours_since_publish, views, impressions, ctr, avg_view_pct, "
                "retained_30s, likes, comments) VALUES (?, ?, 48, ?, ?, ?, ?, ?, ?, ?)",
                (
                    v["id"],
                    dbm.utcnow(),
                    st["views"],
                    st["impressions"],
                    st["ctr"],
                    st["avg_view_pct"],
                    st["retained_30s"],
                    st["likes"],
                    st["comments"],
                ),
            )
            conn.execute(
                "INSERT OR REPLACE INTO retention(video_id, duration_s, curve_json, segments_json, pulled_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (v["id"], v["duration_s"], json.dumps(v["retention"]), json.dumps(v["segments"]), dbm.utcnow()),
            )
            n += 1
    return n


def query_live(token: str, video_id: str, start: str, end: str) -> dict[str, Any]:  # pragma: no cover - needs OAuth
    """One Analytics API report for a video (owner OAuth; not used in tests)."""
    params = {
        "ids": "channel==MINE",
        "startDate": start,
        "endDate": end,
        "filters": f"video=={video_id}",
        "metrics": "views,averageViewPercentage,estimatedMinutesWatched,likes,comments",
    }
    url = f"{ANALYTICS_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})  # noqa: S310 - fixed https endpoint
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310  # nosec B310
        data = json.loads(resp.read(2_000_000))
    return data if isinstance(data, dict) else {}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", type=Path, default=None, help=argparse.SUPPRESS)
    p.add_argument("--live", action="store_true")
    a = p.parse_args(argv)
    new_job_id("stats")
    conn = dbm.connect(a.db)
    if a.live:
        print(
            json.dumps(
                {"error": "live analytics requires owner OAuth (python -m src.publish.oauth authorize); not configured"}
            )
        )
        return 2
    n = load_fixture_into(conn)
    log_event(log, "stats loaded (fixture)", videos=n)
    print(json.dumps({"mode": "fixture (SYNTHETIC)", "videos": n}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
