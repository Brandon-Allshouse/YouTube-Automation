"""Retention forensics: which segments (and which component types) lose viewers.

For each video, the retention curve is aligned to the segment plan (scene start times + visual
tags from the scene spec). Each segment's loss rate (fraction of the audience lost per second) is
compared to the video's own median loss rate; the excess is attributed to the segment's tag.
Aggregated over videos this yields a **penalty table** (mean excess loss per tag, with counts),
which the visual-director uses to prefer components that hold attention.

The first-seconds swipe-away (hook) is reported separately because it behaves differently.

``python -m src.analytics.retention_forensics --video v-123 | --table``
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from src import db as dbm
from src.analytics.pull_stats import ensure_tables

HOOK_S = 3.0


def analyze_video(duration: float, curve: list[float], segments: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-segment loss rates and excess vs the video's median rate."""
    r = np.asarray(curve, dtype=np.float64)
    ts = np.linspace(0, duration, len(r))
    hook_loss = float(1.0 - np.interp(HOOK_S, ts, r))
    rows = []
    segs = sorted(segments, key=lambda s: s["start"])
    for i, s in enumerate(segs):
        t0 = max(float(s["start"]), HOOK_S)
        t1 = float(segs[i + 1]["start"]) if i + 1 < len(segs) else duration
        if t1 - t0 < 0.5:
            continue
        a, b = float(np.interp(t0, ts, r)), float(np.interp(t1, ts, r))
        rate = (a - b) / max(a, 1e-6) / (t1 - t0)  # fraction of remaining audience lost per second
        rows.append({"start": round(t0, 2), "end": round(t1, 2), "tag": s["tag"], "loss_per_s": round(rate, 5)})
    med = statistics.median(x["loss_per_s"] for x in rows) if rows else 0.0
    for x in rows:
        x["excess"] = round(x["loss_per_s"] - med, 5)
    return {"hook_loss_3s": round(hook_loss, 4), "median_loss_per_s": round(med, 5), "segments": rows}


def penalty_table(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Mean excess loss per visual tag across all videos (positive = loses viewers faster)."""
    ensure_tables(conn)
    by_tag: dict[str, list[float]] = defaultdict(list)
    for dur, curve, segs in conn.execute("SELECT duration_s, curve_json, segments_json FROM retention"):
        res = analyze_video(float(dur), json.loads(curve), json.loads(segs))
        for x in res["segments"]:
            by_tag[x["tag"]].append(x["excess"])
    table = [
        {"tag": t, "mean_excess_loss_per_s": round(float(np.mean(v)), 5), "segments": len(v)} for t, v in by_tag.items()
    ]
    return sorted(table, key=lambda r: -r["mean_excess_loss_per_s"])


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    p = argparse.ArgumentParser(description="Retention forensics")
    p.add_argument("--db", type=Path, default=None, help=argparse.SUPPRESS)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--video")
    g.add_argument("--table", action="store_true")
    a = p.parse_args(argv)
    conn = dbm.connect(a.db)
    ensure_tables(conn)
    if a.table:
        print(json.dumps(penalty_table(conn), indent=1))
        return 0
    row = conn.execute(
        "SELECT duration_s, curve_json, segments_json FROM retention WHERE video_id = ?", (a.video,)
    ).fetchone()
    if row is None:
        print(json.dumps({"error": "no retention data for video"}))
        return 2
    print(json.dumps(analyze_video(float(row[0]), json.loads(row[1]), json.loads(row[2])), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
