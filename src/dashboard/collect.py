"""Gather everything the dashboard shows, read-only.

Sources: the pipeline database (opened with SQLite ``mode=ro``, so the dashboard can never change
state), channel configs, ``config/rules.yaml``, the latest research outputs in ``data/research/``
and cross-post export folders. Every string that came from outside (YouTube titles, research
niche names) is kept as data and escaped by the renderer; nothing here is executed or followed.

The result is a plain, JSON-serializable dict (also written as ``dashboard.json`` for agents).
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import statistics
import urllib.parse
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src import db as dbm
from src.analytics.retention_forensics import analyze_video
from src.analytics.weekly_brief import kill_scale
from src.common.channels import all_channels
from src.common.config import rules
from src.common.paths import DATA_DIR, OUTPUT_DIR
from src.dashboard import score as sc

STATE_ORDER = ("idea", "scripted", "reviewed", "voiced", "specd", "rendered", "checked", "scheduled", "published")
RESEARCH_DIR = DATA_DIR / "research"
EXPORTS_DIR = OUTPUT_DIR / "exports"
_RESEARCH_FILE = re.compile(r"^(niche_score|velocity)-(\d{4}-\d{2}-\d{2})(-fixture)?\.json$")
MAX_RESEARCH_BYTES = 5 * 1024 * 1024


def open_readonly(path: Path) -> sqlite3.Connection:
    """Open the pipeline database read-only (path confined like every ``--db``).

    Raises:
        ValueError: Path outside ``data/`` or the temp directory.
        FileNotFoundError: The database doesn't exist.
    """
    resolved = dbm.check_db_path(path)
    if not resolved.is_file():
        raise FileNotFoundError("pipeline database not found; run any pipeline command first")
    uri = f"file:{urllib.parse.quote(resolved.as_posix())}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=10)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
    return row is not None


def _parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def snapshot_metrics(conn: sqlite3.Connection, hours: float, tolerance: float = 12.0) -> list[sc.VideoMetrics]:
    """Per published video, the stats pull closest to ``hours`` after publish (within ``tolerance``)."""
    rows = conn.execute(
        "SELECT v.id, v.channel_id, v.format, s.hours_since_publish, s.views, s.impressions, s.ctr, "
        "s.avg_view_pct, s.retained_30s, s.likes, s.comments "
        "FROM videos v JOIN stats s ON s.video_id = v.id WHERE s.hours_since_publish BETWEEN ? AND ?",
        (hours - tolerance, hours + tolerance),
    ).fetchall()
    best: dict[str, tuple[float, sc.VideoMetrics]] = {}
    for vid, ch, fmt, h, views, imp, ctr, avp, r30, likes, comments in rows:
        d = abs(float(h) - hours)
        if vid in best and best[vid][0] <= d:
            continue
        best[vid] = (
            d,
            sc.VideoMetrics(
                str(vid), str(ch), str(fmt),
                _num(views), _num(imp), _num(ctr), _num(avp), _num(r30), _num(likes), _num(comments),
            ),
        )  # fmt: skip
    return [m for _, m in best.values()]


def _num(x: Any) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None  # drop NaN/inf


def latest_views(conn: sqlite3.Connection) -> dict[str, int]:
    """Most recent view count per video (any age)."""
    rows = conn.execute(
        "SELECT video_id, views FROM stats s WHERE hours_since_publish = "
        "(SELECT MAX(hours_since_publish) FROM stats WHERE video_id = s.video_id)"
    ).fetchall()
    return {str(v): int(n) for v, n in rows}


def _latest_research(kind: str) -> tuple[dict[str, Any] | None, str]:
    """Newest research output of ``kind`` (live preferred over fixture), parsed defensively."""
    if not RESEARCH_DIR.is_dir():
        return None, ""
    found: list[tuple[str, bool, Path]] = []
    for p in RESEARCH_DIR.iterdir():
        m = _RESEARCH_FILE.match(p.name)
        if m and m.group(1) == kind and p.is_file() and not p.is_symlink():
            found.append((m.group(2), m.group(3) is None, p))
    if not found:
        return None, ""
    path = max(found, key=lambda t: (t[1], t[0]))[2]
    if path.stat().st_size > MAX_RESEARCH_BYTES:
        return None, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, ""
    return (data if isinstance(data, dict) else None), path.name


def research_summary() -> dict[str, Any]:
    """Top niches and velocity from the latest research run (third-party strings stay data)."""
    ns, ns_file = _latest_research("niche_score")
    vel, vel_file = _latest_research("velocity")
    niches = []
    for r in (ns or {}).get("ranking", [])[:12] if isinstance((ns or {}).get("ranking"), list) else []:
        if isinstance(r, dict) and _num(r.get("score")) is not None:
            niches.append(
                {
                    "niche": str(r.get("name") or r.get("niche") or "")[:80],
                    "channel": str(r.get("channel") or "")[:40],
                    "score": round(float(r["score"]), 3),
                }
            )
    accel = 0
    for n in (vel or {}).get("niches", []) if isinstance((vel or {}).get("niches"), list) else []:
        if isinstance(n, dict) and isinstance(n.get("accelerating_videos"), int):
            accel += n["accelerating_videos"]
    return {
        "niche_file": ns_file,
        "niche_mode": str((ns or {}).get("mode", ""))[:60],
        "niches": niches,
        "velocity_file": vel_file,
        "accelerating_videos": accel,
        "velocity_note": str((vel or {}).get("note", ""))[:200],
    }


def exports_ready() -> dict[str, int]:
    """Cross-post packages on disk per platform folder (e.g. tiktok, reels)."""
    out: Counter[str] = Counter()
    if EXPORTS_DIR.is_dir():
        for vdir in EXPORTS_DIR.iterdir():
            if vdir.is_dir() and not vdir.is_symlink():
                for plat in vdir.iterdir():
                    if plat.is_dir() and re.fullmatch(r"[a-z0-9_-]{1,32}", plat.name) and any(plat.iterdir()):
                        out[plat.name] += 1
    return dict(sorted(out.items()))


def retention_penalties(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Mean excess viewer loss per visual tag (read-only version of the forensics table)."""
    if not _has_table(conn, "retention"):
        return []
    by_tag: dict[str, list[float]] = {}
    for dur, curve, segs in conn.execute("SELECT duration_s, curve_json, segments_json FROM retention"):
        try:
            res = analyze_video(float(dur), json.loads(curve), json.loads(segs))
        except (ValueError, TypeError, KeyError):
            continue
        for x in res["segments"]:
            by_tag.setdefault(str(x["tag"]), []).append(float(x["excess"]))
    table = [
        {"tag": t, "excess_loss_per_s": round(statistics.fmean(v), 5), "segments": len(v)} for t, v in by_tag.items()
    ]
    return sorted(table, key=lambda r: -float(r["excess_loss_per_s"]))


def collect(
    conn: sqlite3.Connection, *, now: datetime | None = None, source: str = "pipeline database"
) -> dict[str, Any]:
    """Everything the dashboard needs, computed from ``conn`` (read-only use)."""
    now = now or datetime.now(UTC)
    sr = rules().scoring
    videos = [
        dict(zip([c[0] for c in cur.description], row, strict=True))
        for cur in [conn.execute("SELECT * FROM videos ORDER BY created_at DESC")]
        for row in cur.fetchall()
    ]
    by_id = {v["id"]: v for v in videos}
    metrics = snapshot_metrics(conn, sr.snapshot_hours)
    scores = sc.score_all(metrics, sr)
    lifetime = latest_views(conn)

    def published_at(v: dict[str, Any]) -> datetime | None:
        return _parse_time(v.get("scheduled_for")) if v.get("state") == "published" else None

    # --- channels / accounts -------------------------------------------------------------
    chan_rows = []
    channel_scores = []
    for ch in all_channels():
        mine = [v for v in videos if v["channel_id"] == ch.id]
        scored = []
        for m in metrics:
            if m.channel_id != ch.id or m.video_id not in scores:
                continue
            pub = published_at(by_id.get(m.video_id, {}))
            age = (now - pub).total_seconds() / 86400 if pub else 0.0
            scored.append((age, scores[m.video_id].vpi, scores[m.video_id].breakout, m.views))
        cs = sc.channel_score(ch.id, scored, sr)
        channel_scores.append(cs)
        recent = Counter(
            v["format"] for v in mine if (p := published_at(v)) is not None and now - timedelta(days=7) <= p <= now
        )
        cad = sc.cadence(recent, ch.cadence.shorts_per_day, ch.cadence.long_per_week)
        own = [m for m in metrics if m.channel_id == ch.id]
        verdict = "no data"
        if own:
            km = {
                "videos": len(own),
                "median_views": statistics.median(m.views or 0 for m in own),
                "median_ctr": statistics.median(m.ctr or 0 for m in own),
                "median_avg_view_pct": statistics.median(m.avg_view_pct or 0 for m in own),
                "median_retained_30s": statistics.median(m.retained_30s or 0 for m in own),
            }
            verdict = kill_scale(km, None)
        pubs = [p for v in mine if (p := published_at(v)) is not None]
        upcoming = sorted(
            t for v in mine if v["state"] == "scheduled" and (t := _parse_time(v.get("scheduled_for"))) and t > now
        )
        chan_rows.append(
            {
                "id": ch.id,
                "name": ch.name,
                "niche": ch.niche,
                "platform": "youtube",
                "theme": ch.theme,
                "voice": "+".join(n for n, _ in ch.voice.blend),
                "music": ch.music.style,
                "timezone": ch.cadence.timezone,
                "videos_total": len(mine),
                "published": len(pubs),
                "last_published": max(pubs).isoformat() if pubs else None,
                "next_scheduled": upcoming[0].isoformat() if upcoming else None,
                "views_total": sum(lifetime.get(v["id"], 0) for v in mine),
                "score": asdict(cs),
                "cadence": {**asdict(cad), "adherence": cad.adherence},
                "verdict": verdict,
            }
        )

    # --- videos -----------------------------------------------------------------------------
    video_rows = []
    by_metric = {x.video_id: x for x in metrics}
    for v in videos:
        s = scores.get(v["id"])
        snap_m = by_metric.get(v["id"])
        video_rows.append(
            {
                "id": v["id"],
                "channel": v["channel_id"],
                "format": v["format"],
                "title": str(v["title"])[:300],
                "state": v["state"],
                "published_at": p.isoformat() if (p := published_at(v)) else None,
                "scheduled_for": v.get("scheduled_for"),
                "youtube_id": v.get("youtube_id"),
                "arm_id": v.get("arm_id"),
                "views_lifetime": lifetime.get(v["id"]),
                "views_48h": snap_m.views if snap_m else None,
                "ctr": snap_m.ctr if snap_m else None,
                "avg_view_pct": snap_m.avg_view_pct if snap_m else None,
                "retained_30s": snap_m.retained_30s if snap_m else None,
                "engagement": round(snap_m.engagement, 4) if snap_m and snap_m.engagement is not None else None,
                "vpi": s.vpi if s else None,
                "breakout": bool(s and s.breakout),
                "baseline": s.baseline_source if s else None,
                "confidence": s.confidence if s else None,
                "components": s.components if s else {},
            }
        )

    # --- pipeline -------------------------------------------------------------------------
    states = Counter(v["state"] for v in videos)
    funnel = [{"state": st, "count": states.get(st, 0)} for st in STATE_ORDER]
    extra = {st: n for st, n in states.items() if st not in STATE_ORDER}
    events = [
        {"video_id": r[0], "from": r[1], "to": r[2], "actor": str(r[3])[:40], "note": str(r[4] or "")[:120], "at": r[5]}
        for r in conn.execute(
            "SELECT video_id, from_state, to_state, actor, note, at FROM video_events ORDER BY id DESC LIMIT 15"
        )
    ]
    failed_jobs = [
        {"id": r[0], "kind": r[1], "video_id": r[2], "detail": str(r[3] or "")[:160], "at": r[4]}
        for r in conn.execute(
            "SELECT id, kind, video_id, detail, started_at FROM jobs WHERE status = 'failed' "
            "ORDER BY started_at DESC LIMIT 10"
        )
    ]
    schedule = sorted(
        (
            {"id": v["id"], "channel": v["channel_id"], "format": v["format"], "title": str(v["title"])[:120],
             "slot": v["scheduled_for"]}
            for v in videos
            if v["state"] == "scheduled" and v.get("scheduled_for")
        ),
        key=lambda r: str(r["slot"]),
    )  # fmt: skip

    # --- learning -------------------------------------------------------------------------
    arms = [(str(a), float(al), float(be), int(n)) for a, al, be, n in conn.execute(
        "SELECT arm_id, alpha, beta, pulls FROM bandit_arms")]  # fmt: skip
    posteriors = sc.arm_posteriors(arms)
    lever_rows = [
        (str(by_id[vid]["arm_id"]), s.vpi) for vid, s in scores.items() if vid in by_id and by_id[vid].get("arm_id")
    ]

    # --- totals -----------------------------------------------------------------------------
    published = [v for v in video_rows if v["state"] == "published"]
    cap = rules().quota.session_cap_units
    quota_used = dbm.quota_used_today(conn) if _has_table(conn, "api_quota") else 0
    scored_vpis = [s.vpi for s in scores.values()]
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "source": source,
        "portfolio": {
            "score": sc.portfolio_score(channel_scores),
            "channels": len(chan_rows),
            "videos_total": len(videos),
            "published": len(published),
            "views_total": sum(lifetime.values()),
            "scored": len(scored_vpis),
            "median_vpi": round(statistics.median(scored_vpis), 1) if scored_vpis else None,
            "hits": sum(1 for x in scored_vpis if x >= sr.hit_threshold),
            "breakouts": sum(1 for s in scores.values() if s.breakout),
            "review_backlog": states.get("scripted", 0),
            "scheduled": len(schedule),
            "failed_jobs": len(failed_jobs),
        },
        "channels": chan_rows,
        "videos": video_rows,
        "pipeline": {"funnel": funnel, "other_states": extra, "events": events, "failed_jobs": failed_jobs},
        "schedule": schedule,
        "learning": {
            "arms": [asdict(p) for p in posteriors[:15]],
            "levers": [asdict(lv) for lv in sc.levers(lever_rows)],
            "retention_penalties": retention_penalties(conn),
        },
        "research": research_summary(),
        "quota": {"used_today": quota_used, "cap": cap},
        "exports": exports_ready(),
        "scoring": {
            "snapshot_hours": sr.snapshot_hours,
            "hit_threshold": sr.hit_threshold,
            "breakout_multiple": sr.breakout_multiple,
        },
    }
