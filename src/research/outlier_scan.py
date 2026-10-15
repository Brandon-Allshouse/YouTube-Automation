"""Outlier scan: videos whose views are ≥ N× their channel's subscribers (last 30-90 days).

Outliers show topics that travel beyond an existing audience: the strongest demand signal for a
small new channel. Thresholds live in ``config/rules.yaml`` (``outliers``).

``python -m src.research.outlier_scan [--live] [--niches a,b]``
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from src.common.config import rules
from src.common.log import get_logger, log_event, new_job_id
from src.research.common import (
    Context,
    NicheData,
    add_common_args,
    age_days,
    make_context,
    median,
    niche_videos,
    write_output,
)

log = get_logger("src.research.outliers")

SHORTS_MAX_S = 180.0


def find_outliers(nd: NicheData, ctx: Context) -> dict[str, Any]:
    """Outlier statistics for one niche."""
    r = rules().outliers
    rows: list[dict[str, Any]] = []
    for v in nd.videos:
        ch = nd.channels.get(v.channel_id)
        if ch is None or ch.hidden_subscribers:
            continue
        age = age_days(v, ctx.now)
        subs = max(ch.subscribers, 1)
        rows.append(
            {
                "video_id": v.id,
                "title": v.title,
                "channel": ch.title,
                "views": v.views,
                "subscribers": ch.subscribers,
                "ratio": round(v.views / subs, 2),
                "age_days": round(age, 1),
                "duration_s": v.duration_s,
            }
        )
    window = [x for x in rows if 2 <= x["age_days"] <= r.window_days_max]
    outliers = sorted((x for x in window if x["ratio"] >= r.min_views_to_subs_ratio), key=lambda x: -x["ratio"])
    shorts = [x for x in window if x["duration_s"] <= SHORTS_MAX_S]
    longs = [x for x in window if x["duration_s"] > SHORTS_MAX_S]

    def density(xs: list[dict[str, Any]]) -> float:
        hits = sum(1 for x in xs if x["ratio"] >= r.min_views_to_subs_ratio)
        return round(hits / len(xs), 3) if xs else 0.0

    return {
        "niche": nd.niche.id,
        "channel": nd.niche.channel,
        "videos_scanned": len(window),
        "outliers": len(outliers),
        "outlier_density": round(len(outliers) / len(window), 3) if window else 0.0,
        "median_views": int(median([x["views"] for x in window])),
        "median_subscribers": int(median([x["subscribers"] for x in window])),
        "shorts_share": round(len(shorts) / len(window), 2) if window else 0.0,
        # Shorts routinely out-view subscriber counts, so judge the formats separately
        "outlier_density_long": density(longs),
        "outlier_density_shorts": density(shorts),
        "top_outliers": outliers[:5],
    }


def run(ctx: Context) -> dict[str, Any]:
    """Scan every niche and rank by outlier density."""
    results = [find_outliers(niche_videos(ctx, n), ctx) for n in ctx.niches]
    results.sort(key=lambda x: (-x["outlier_density"], -x["outliers"]))
    return {"niches": results}


def main(argv: list[str] | None = None) -> int:
    """CLI: print a compact summary; full data goes to ``data/research/``."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_common_args(p)
    a = p.parse_args(argv)
    new_job_id("outliers")
    ctx = make_context(a)
    data = run(ctx)
    path = write_output("outliers", data, ctx)
    log_event(log, "outlier scan done", niches=len(data["niches"]), units=ctx.client.units)
    summary = [
        {k: n[k] for k in ("niche", "outlier_density", "outliers", "videos_scanned", "median_views")}
        for n in data["niches"]
    ]
    print(
        json.dumps(
            {
                "output": path.name,
                "mode": "live" if ctx.live else "fixture",
                "api_units": ctx.client.units,
                "ranking": summary,
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
