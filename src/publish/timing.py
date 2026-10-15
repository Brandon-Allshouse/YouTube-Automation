"""Upload-time optimizer with jitter and human-like cadence.

Picks the next publish slot for a video:

* respects per-channel cadence (≤ 1 Short/day; long-form ≤ ``max_long_per_week`` in any rolling
  7 days; from ``config/rules.yaml`` and the channel config)
* prefers the channel's best hours: learned from our own stats once ≥ 20 videos exist (median
  48 h views by local publish hour), otherwise the configured ``preferred_hours_local``
* adds seeded jitter (± ``publish_jitter_minutes``) and never lands on :00, so schedules don't
  look machine-made; keeps ≥ 45 min between any two uploads across the whole portfolio
* computes in the channel's IANA timezone (DST-correct), returns UTC

``python -m src.publish.timing --video-id v-123 [--schedule]``
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from src import db as dbm
from src.common.channels import load_channel
from src.common.config import rules

MIN_GAP_PORTFOLIO = timedelta(minutes=45)
LEARN_MIN_VIDEOS = 20


def _scheduled(conn: sqlite3.Connection, exclude: str = "") -> list[tuple[str, str, datetime]]:
    """Slots already taken, except ``exclude``'s own (so rescheduling doesn't block itself)."""
    rows = conn.execute(
        "SELECT channel_id, format, scheduled_for FROM videos "
        "WHERE scheduled_for IS NOT NULL AND state IN ('scheduled', 'published') AND id != ?",
        (exclude,),
    ).fetchall()
    return [(r[0], r[1], datetime.fromisoformat(r[2])) for r in rows]


def learned_hours(conn: sqlite3.Connection, channel_id: str, tz: ZoneInfo) -> list[int] | None:
    """Best local hours by median 48 h views, if we have enough history."""
    rows = conn.execute(
        "SELECT v.scheduled_for, s.views FROM videos v JOIN stats s ON s.video_id = v.id "
        "WHERE v.channel_id = ? AND v.scheduled_for IS NOT NULL AND s.hours_since_publish BETWEEN 40 AND 56",
        (channel_id,),
    ).fetchall()
    if len(rows) < LEARN_MIN_VIDEOS:
        return None
    by_hour: dict[int, list[int]] = {}
    for when, views in rows:
        h = datetime.fromisoformat(when).astimezone(tz).hour
        by_hour.setdefault(h, []).append(int(views))
    ranked = sorted(by_hour, key=lambda h: -statistics.median(by_hour[h]))
    return ranked[:3]


def propose_slot(
    conn: sqlite3.Connection,
    channel_id: str,
    fmt: str,
    video_id: str,
    *,
    now: datetime | None = None,
) -> datetime:
    """Next valid publish time (UTC) for a video."""
    ch = load_channel(channel_id)
    tz = ZoneInfo(ch.cadence.timezone)
    cad = rules().cadence
    now = (now or datetime.now(UTC)).astimezone(UTC)
    hours = learned_hours(conn, channel_id, tz) or list(ch.cadence.preferred_hours_local)
    rng = np.random.default_rng(int(hashlib.sha256(video_id.encode()).hexdigest()[:12], 16))
    taken = _scheduled(conn, exclude=video_id)
    max_long = min(ch.cadence.long_per_week, cad.max_long_per_week_per_channel)
    for day in range(0, 60):
        local_day = (now.astimezone(tz) + timedelta(days=day)).date()
        for h in sorted(hours, key=lambda x: (rng.random(), x)):  # vary which preferred hour wins
            jitter = float(rng.uniform(-cad.publish_jitter_minutes, cad.publish_jitter_minutes))
            minute_offset = jitter if abs(jitter) >= 1 else 7.0
            local = datetime(local_day.year, local_day.month, local_day.day, h, 0, tzinfo=tz) + timedelta(
                minutes=minute_offset
            )
            if local.minute == 0:
                local += timedelta(minutes=3)
            local = local.replace(second=int(rng.integers(0, 60)), microsecond=0)
            slot = local.astimezone(UTC)
            if slot < now + timedelta(hours=2):
                continue
            same = [t for c, f, t in taken if c == channel_id and f == fmt]
            if fmt == "short":
                if any(t.astimezone(tz).date() == local_day for t in same) or ch.cadence.shorts_per_day < 1:
                    break  # this day already has its Short; try the next day
            else:
                if any(t.astimezone(tz).date() == local_day for t in same):
                    break  # at most one long-form per channel per day: no bulk-looking drops
                # rolling 7-day windows: the 7 days before and after this slot must each stay under the cap
                before = [t for t in same if slot - timedelta(days=7) < t <= slot]
                after = [t for t in same if slot <= t < slot + timedelta(days=7)]
                if len(before) >= max_long or len(after) >= max_long:
                    break
            if any(abs(t - slot) < MIN_GAP_PORTFOLIO for _, _, t in taken):
                continue
            return slot
    raise RuntimeError("no slot found in the next 60 days (cadence too tight?)")


def main(argv: list[str] | None = None) -> int:
    """CLI: propose (and optionally record) a slot."""
    p = argparse.ArgumentParser(description="Upload-time optimizer")
    p.add_argument("--db", type=Path, default=None, help=argparse.SUPPRESS)
    p.add_argument("--video-id", required=True)
    p.add_argument("--schedule", action="store_true", help="record the slot and move checked → scheduled")
    a = p.parse_args(argv)
    conn = dbm.connect(a.db)
    v = dbm.get_video(conn, a.video_id)
    if v is None:
        print(json.dumps({"error": "unknown video"}))
        return 2
    slot = propose_slot(conn, v["channel_id"], v["format"], a.video_id)
    out = {"video_id": a.video_id, "slot_utc": slot.isoformat()}
    if a.schedule:
        import sys

        if not sys.stdin.isatty():
            print(json.dumps({"error": "scheduling is owner-only: run --schedule in an interactive terminal"}))
            return 3
        with dbm.transaction(conn):
            conn.execute("UPDATE videos SET scheduled_for = ? WHERE id = ?", (slot.isoformat(), a.video_id))
        try:
            dbm.transition(conn, a.video_id, "scheduled", actor="pipeline", note=f"slot {slot.isoformat()}")
        except dbm.TransitionError as exc:
            with dbm.transaction(conn):
                conn.execute("UPDATE videos SET scheduled_for = NULL WHERE id = ?", (a.video_id,))
            print(json.dumps({"error": str(exc)}))
            return 3
        out["state"] = "scheduled"
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
