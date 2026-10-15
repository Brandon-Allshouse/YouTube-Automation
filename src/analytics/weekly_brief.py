"""Weekly operator brief: one page the owner can read in a few minutes.

Per channel: 48 h leading indicators (CTR, retained at 30 s, average view %, views vs channel
median), kill/scale evaluation against ``config/rules.yaml`` (only with ≥ ``min_videos_before_decision``
videos), bandit leaders, the retention penalty table, and a short action list.

``python -m src.analytics.weekly_brief`` → ``data/briefs/weekly-<YYYY>-W<WW>.md``
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src import db as dbm
from src.analytics.retention_forensics import penalty_table
from src.common.config import rules
from src.common.paths import DATA_DIR, ensure_dir, safe_join
from src.learning.bandit import load as load_arms
from src.learning.bandit import update as update_arms


def channel_metrics(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Median 48 h indicators per channel."""
    rows = conn.execute(
        "SELECT v.channel_id, s.views, s.ctr, s.avg_view_pct, s.retained_30s "
        "FROM videos v JOIN stats s ON s.video_id = v.id WHERE s.hours_since_publish = 48"
    ).fetchall()
    by: dict[str, list[tuple[int, float, float, float]]] = {}
    for ch, views, ctr, avp, r30 in rows:
        by.setdefault(ch, []).append((int(views), float(ctr or 0), float(avp or 0), float(r30 or 0)))
    out = {}
    for ch, xs in by.items():
        out[ch] = {
            "videos": len(xs),
            "median_views": int(statistics.median(x[0] for x in xs)),
            "median_ctr": round(statistics.median(x[1] for x in xs), 4),
            "median_avg_view_pct": round(statistics.median(x[2] for x in xs), 3),
            "median_retained_30s": round(statistics.median(x[3] for x in xs), 3),
        }
    return out


def kill_scale(m: dict[str, Any], niche_median_views: float | None) -> str:
    """Decision per rules; 'insufficient data' below the minimum sample."""
    ks = rules().kill_scale
    if m["videos"] < ks.min_videos_before_decision:
        return f"insufficient data ({m['videos']}/{ks.min_videos_before_decision} videos)"
    k, s = ks.kill_if, ks.scale_if
    rel = m["median_views"] / niche_median_views if niche_median_views else None
    if (
        (k.ctr_below and m["median_ctr"] < k.ctr_below)
        or (k.avg_view_pct_below and m["median_avg_view_pct"] < k.avg_view_pct_below)
        or (rel is not None and k.median_views_vs_niche_median_below and rel < k.median_views_vs_niche_median_below)
    ):
        return "KILL candidate (owner decides)"
    if (s.retained_30s_above and m["median_retained_30s"] > s.retained_30s_above) and (
        rel is None or not s.median_views_vs_niche_median_above or rel > s.median_views_vs_niche_median_above
    ):
        return "SCALE candidate"
    return "keep testing"


def build(conn: sqlite3.Connection, now: datetime | None = None) -> str:
    """Markdown brief."""
    now = now or datetime.now(UTC)
    update_arms(conn)
    metrics = channel_metrics(conn)
    arms = sorted(load_arms(conn), key=lambda a: -a.mean)
    pens = penalty_table(conn)
    iso = now.isocalendar()
    lines = [
        f"# Weekly brief {iso.year}-W{iso.week:02d}",
        "",
        f"_Generated {now.date().isoformat()}. Data may be synthetic fixtures until live analytics is enabled._",
        "",
    ]
    lines += [
        "## Channels (48 h medians)",
        "",
        "| channel | videos | views | CTR | avg view % | retained 30 s | decision |",
        "|---|---|---|---|---|---|---|",
    ]
    for ch, m in sorted(metrics.items()):
        lines.append(
            f"| {ch} | {m['videos']} | {m['median_views']:,} | {m['median_ctr']:.1%} | "
            f"{m['median_avg_view_pct']:.0%} | {m['median_retained_30s']:.0%} | {kill_scale(m, None)} |"
        )
    lines += ["", "## Format bandit (top arms by posterior mean)", ""]
    lines += [f"- `{a.arm_id}`: {a.mean:.0%} success (n={a.pulls})" for a in arms[:5]] or ["- no data"]
    lines += ["", "## Retention penalty table (excess audience loss per second by visual type)", ""]
    lines += [
        f"- {p['tag']}: {p['mean_excess_loss_per_s'] * 100:+.2f} %/s over the video median (n={p['segments']})"
        for p in pens
    ] or ["- no data"]
    worst = [p["tag"] for p in pens[:2] if p["mean_excess_loss_per_s"] > 0]
    best = [p["tag"] for p in pens[-2:] if p["mean_excess_loss_per_s"] < 0]
    lines += ["", "## Actions (≤ 10 minutes)", ""]
    if worst:
        lines.append(
            f"- Use fewer/shorter **{', '.join(worst)}** beats; "
            f"prefer **{', '.join(best) or 'map/comparison'}** (visual-director)."
        )
    if arms:
        lines.append(f"- Next week's Shorts: lean into `{arms[0].arm_id}`; keep 1 in 4 slots for exploration.")
    lines.append("- Review queued scripts (`python -m src.review.queue list`).")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    p = argparse.ArgumentParser(description="Weekly operator brief")
    p.add_argument("--db", type=Path, default=None, help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    conn = dbm.connect(a.db)
    now = datetime.now(UTC)
    iso = now.isocalendar()
    out = safe_join(ensure_dir(DATA_DIR / "briefs"), f"weekly-{iso.year}-W{iso.week:02d}.md")
    out.write_text(build(conn, now), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
