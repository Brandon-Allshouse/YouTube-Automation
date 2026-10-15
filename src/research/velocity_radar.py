"""Velocity radar: view acceleration per video and a demand/supply gap score per niche.

* recent daily views = Δviews / Δt between two snapshots (≥ 12 h apart)
* acceleration = recent daily views / lifetime average daily views (> 1 means speeding up)
* demand = sum of recent daily views across the niche's recent videos
* supply = approximate number of videos competing for the query window (search totalResults)
* gap score = demand / (1000 · log10(supply + 10)): attention per competing video. The API's
  ``totalResults`` is a coarse estimate that saturates near 1e6, so supply is log-damped.

Live mode stores snapshots in SQLite (``market_snapshots``); the first live run records a baseline
and acceleration appears on the next run. Fixture mode uses two synthetic snapshots 24 h apart.

``python -m src.research.velocity_radar [--live] [--niches a,b]``
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import datetime
from typing import Any

from src import db as dbm
from src.common.log import get_logger, log_event, new_job_id
from src.common.paths import FIXTURES_DIR
from src.research.common import (
    Context,
    add_common_args,
    age_days,
    make_context,
    niche_videos,
    write_output,
)

log = get_logger("src.research.velocity")

# View counts older than this are refetched for a snapshot (1 unit per 50 videos).
SNAPSHOT_MAX_AGE_H = 1.0

SNAPSHOT_SQL = """
CREATE TABLE IF NOT EXISTS market_snapshots (
    video_id TEXT NOT NULL,
    at TEXT NOT NULL,
    views INTEGER NOT NULL,
    PRIMARY KEY (video_id, at)
)
"""


def _prev_snapshot_fixture() -> dict[str, tuple[int, datetime]]:
    data = json.loads((FIXTURES_DIR / "youtube" / "videos_prev.json").read_text(encoding="utf-8"))
    at = datetime.fromisoformat(data["taken_at"])
    return {it["id"]: (int(it["statistics"]["viewCount"]), at) for it in data["items"]}


def _prev_snapshot_db(conn: sqlite3.Connection, ids: list[str], now: datetime) -> dict[str, tuple[int, datetime]]:
    """Latest snapshot per video at least 12 h old, with its own timestamp (videos differ by minutes)."""
    out: dict[str, tuple[int, datetime]] = {}
    cutoff = now.timestamp() - 12 * 3600
    for vid in ids:
        row = conn.execute(
            "SELECT views, at FROM market_snapshots WHERE video_id = ? ORDER BY at DESC", (vid,)
        ).fetchall()
        for views, at in row:
            t = datetime.fromisoformat(at)
            if t.timestamp() <= cutoff:
                out[vid] = (int(views), t)
                break
    return out


def run(ctx: Context) -> dict[str, Any]:
    """Compute velocity metrics for every niche."""
    conn = ctx.client.conn
    conn.execute(SNAPSHOT_SQL)
    niches_out: list[dict[str, Any]] = []
    baseline_only = False
    for n in ctx.niches:
        # A snapshot must record views as of now; a cached response would store old counts under
        # a new timestamp and read as zero acceleration (and poison later comparisons).
        nd = niche_videos(ctx, n, stats_max_age_hours=SNAPSHOT_MAX_AGE_H)
        ids = [v.id for v in nd.videos]
        if ctx.live:
            prev = _prev_snapshot_db(conn, ids, ctx.now)
            with dbm.transaction(conn):
                conn.executemany(
                    "INSERT OR IGNORE INTO market_snapshots(video_id, at, views) VALUES (?, ?, ?)",
                    [(v.id, ctx.now.isoformat(timespec="seconds"), v.views) for v in nd.videos],
                )
        else:
            prev = _prev_snapshot_fixture()
        rows: list[dict[str, Any]] = []
        for v in nd.videos:
            age = age_days(v, ctx.now)
            lifetime_daily = v.views / age
            hours = (ctx.now - prev[v.id][1]).total_seconds() / 3600 if v.id in prev else 0.0
            if hours >= 12:
                recent_daily = max(v.views - prev[v.id][0], 0) / hours * 24
                accel = recent_daily / lifetime_daily if lifetime_daily > 0 else 0.0
            else:
                recent_daily, accel = lifetime_daily, None
            rows.append(
                {
                    "video_id": v.id,
                    "title": v.title,
                    "views": v.views,
                    "age_days": round(age, 1),
                    "recent_daily_views": int(recent_daily),
                    "acceleration": round(accel, 2) if accel is not None else None,
                }
            )
        if all(r["acceleration"] is None for r in rows):
            baseline_only = True
        recent = [r for r in rows if r["age_days"] <= 30]
        demand = sum(r["recent_daily_views"] for r in recent)
        accelerating = sorted(
            (r for r in rows if (r["acceleration"] or 0) > 1.15),
            key=lambda r: -(r["acceleration"] or 0),
        )
        niches_out.append(
            {
                "niche": n.id,
                "channel": n.channel,
                "demand_daily_views": int(demand),
                "supply": nd.supply,
                "gap_score": round(demand / (1000 * math.log10(nd.supply + 10)), 2),
                "accelerating_videos": len(accelerating),
                "top_accelerating": accelerating[:5],
            }
        )
    niches_out.sort(key=lambda x: -x["gap_score"])
    note = (
        "first live run: baseline snapshot recorded; acceleration available after 12 h"
        if baseline_only and ctx.live
        else ""
    )
    return {"niches": niches_out, "note": note}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_common_args(p)
    a = p.parse_args(argv)
    new_job_id("velocity")
    ctx = make_context(a)
    data = run(ctx)
    path = write_output("velocity", data, ctx)
    log_event(log, "velocity radar done", niches=len(data["niches"]), units=ctx.client.units)
    summary = [
        {k: n[k] for k in ("niche", "gap_score", "demand_daily_views", "supply", "accelerating_videos")}
        for n in data["niches"]
    ]
    print(
        json.dumps(
            {
                "output": path.name,
                "api_units": ctx.client.units,
                "note": data["note"],
                "ranking": summary,
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
