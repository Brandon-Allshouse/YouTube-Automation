"""Generate deterministic SYNTHETIC analytics fixtures (our own channels' stats don't exist yet).

Writes ``tests/fixtures/analytics/videos.json``: published videos with arm ids, 48 h stats,
100-point audience-retention curves and the segment plan (start time + visual tag) each curve
should be aligned to. Built-in signal for tests: map/comparison segments retain better, list
segments lose more viewers, and "question" hooks get higher CTR than "statement" hooks.

Run: ``python -m scripts.make_analytics_fixtures``
"""

from __future__ import annotations

import json

import numpy as np

from src.common.paths import FIXTURES_DIR

OUT = FIXTURES_DIR / "analytics"
TAG_DROP = {
    "kinetic-title": 0.010,
    "map": 0.004,
    "comparison": 0.005,
    "stat": 0.007,
    "list": 0.018,
    "chart": 0.008,
    "timeline": 0.009,
}


def main() -> int:
    """Write the fixture file."""
    rng = np.random.default_rng(4848)
    videos = []
    for i in range(24):
        channel = "atlas" if i % 2 == 0 else "neon"
        niche = ["map-oddities", "border-history", "megaprojects", "space-scale"][i % 4]
        hook = "question" if rng.random() < 0.5 else "statement"
        length = "s45" if rng.random() < 0.6 else "s55"
        visual = "maps-heavy" if rng.random() < 0.5 else "type-heavy"
        duration = 45.0 if length == "s45" else 55.0
        tags = []
        t = 0.0
        while t < duration - 3:
            if visual == "maps-heavy":
                tag = str(rng.choice(["map", "comparison", "kinetic-title", "map", "stat"]))
            else:
                tag = str(rng.choice(["kinetic-title", "list", "kinetic-title", "stat", "list"]))
            tags.append({"start": round(t, 2), "tag": tag})
            t += float(rng.uniform(3.0, 6.0))
        # retention: early swipe-away, then per-second decay set by the current segment's tag
        ts = np.linspace(0, duration, 100)
        r = np.empty(100)
        cur = 1.0 - float(rng.uniform(0.18, 0.30)) * (0.8 if hook == "question" else 1.0)
        for k, tt in enumerate(ts):
            seg = max((s for s in tags if s["start"] <= tt), key=lambda s: s["start"])
            if k:
                cur *= 1 - TAG_DROP[seg["tag"]] * (ts[1] - ts[0]) * float(rng.uniform(0.8, 1.2))
            r[k] = cur
        ctr = float(np.clip(rng.normal(0.065 if hook == "question" else 0.048, 0.008), 0.01, 0.2))
        impressions = int(rng.integers(8000, 60000))
        views = int(impressions * ctr * float(rng.uniform(0.9, 1.1)))
        videos.append(
            {
                "id": f"v-fx{i:04d}",
                "channel_id": channel,
                "format": "short",
                "title": f"Fixture video {i}",
                "arm_id": f"{niche}|short|{hook}|{length}|{visual}",
                "published_at": f"2026-09-{(i % 28) + 1:02d}T15:0{i % 10}:00+00:00",
                "duration_s": duration,
                "segments": tags,
                "stats_48h": {
                    "views": views,
                    "impressions": impressions,
                    "ctr": round(ctr, 4),
                    "avg_view_pct": round(float(np.mean(r)), 4),
                    "retained_30s": round(float(np.interp(30, ts, r)), 4),
                    "likes": int(views * 0.04),
                    "comments": int(views * 0.002),
                },
                "retention": [round(float(x), 4) for x in r],
            }
        )
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "videos.json").write_text(json.dumps({"synthetic": True, "videos": videos}, indent=1), encoding="utf-8")
    print(f"analytics fixtures: {len(videos)} videos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
