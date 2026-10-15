"""Generate deterministic SYNTHETIC YouTube API fixtures for offline research runs and tests.

Responses mirror the real API's JSON shapes (search.list, videos.list, channels.list,
commentThreads.list). Content is invented and marked ``"synthetic": true`` in ``meta.json``; it
must never be presented as real data. Two view snapshots (``videos.json`` and
``videos_prev.json``, 24 h apart) let the velocity radar compute acceleration.

Run: ``python -m scripts.make_fixtures``
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import numpy as np
import yaml

from src.common.paths import CONFIG_DIR, FIXTURES_DIR

OUT = FIXTURES_DIR / "youtube"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)

TITLE_PATTERNS = [
    "Why {t} Is Weirder Than You Think",
    "{T} Explained in 10 Minutes",
    "The Real Reason {t} Exists",
    "How {t} Actually Works",
    "{T}: The Untold Story",
    "I Tried to Understand {t}",
    "{T} in 60 Seconds",
    "What Nobody Tells You About {t}",
    "The Map That Explains {t}",
    "{T} Finally Makes Sense",
]
QUESTION_PATTERNS = [
    "Can you do a video on why {t} differs between countries?",
    "Why does {t} work this way though?",
    "How did people figure out {t} before computers?",
    "Is {t} the same everywhere in the world?",
    "What would happen if {t} changed?",
    "Can someone explain the part about {t} at 3:40?",
    "Why don't schools teach {t} like this?",
    "Does anyone know a good source on {t}?",
]
PRAISE = [
    "Great video!",
    "This was so well explained.",
    "Loved the animations.",
    "Subscribed!",
    "First!",
    "The editing is insane",
]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "q"


def _vid(rng: np.random.Generator) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"  # pragma: allowlist secret
    return "".join(alphabet[int(i)] for i in rng.integers(0, len(alphabet), 11))


def main() -> int:
    """Write fixtures under tests/fixtures/youtube/."""
    niches = yaml.safe_load((CONFIG_DIR / "seed_niches.yaml").read_text(encoding="utf-8"))["niches"]
    rng = np.random.default_rng(20261014)
    (OUT / "search").mkdir(parents=True, exist_ok=True)
    all_comments: dict[str, object] = {}
    channels: dict[str, dict[str, object]] = {}
    videos: list[dict[str, object]] = []
    prev: list[dict[str, object]] = []
    for n in niches:
        # niche-level character: some niches are hot (many outliers), some saturated
        heat = float(rng.uniform(0.2, 1.0))
        pool = []
        for _ in range(int(rng.integers(8, 14))):
            cid = "UC" + _vid(rng) + _vid(rng)[:11]
            subs = int(np.exp(rng.normal(11.0 if heat < 0.5 else 10.0, 1.6)))
            channels[cid] = {
                "kind": "youtube#channel",
                "id": cid,
                "snippet": {"title": f"{n['name'].split()[0]} Lab {len(channels) + 1}"},
                "statistics": {
                    "subscriberCount": str(subs),
                    "videoCount": str(int(rng.integers(20, 900))),
                    "hiddenSubscriberCount": False,
                },
            }
            pool.append(cid)
        for q in n["queries"]:
            topic = q.replace(" explained", "").replace("why ", "").strip()
            items = []
            for k in range(25):
                vid = _vid(rng)
                cid = pool[int(rng.integers(len(pool)))]
                subs = int(channels[cid]["statistics"]["subscriberCount"])  # type: ignore[index]
                age = float(rng.uniform(2, 90))
                ratio = float(np.exp(rng.normal(-0.6 + 1.8 * heat, 1.2)))  # views per subscriber
                views = int(max(200, subs * ratio * min(1.0, age / 20)))
                published = NOW - timedelta(days=age)
                title = TITLE_PATTERNS[int(rng.integers(len(TITLE_PATTERNS)))].format(t=topic, T=topic.title())
                items.append(
                    {
                        "kind": "youtube#searchResult",
                        "id": {"kind": "youtube#video", "videoId": vid},
                        "snippet": {
                            "publishedAt": published.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "channelId": cid,
                            "title": title,
                        },
                    }
                )
                daily = views / max(age, 1) * float(rng.uniform(0.5, 1.6))
                accel = float(rng.uniform(0.6, 1.8)) if heat > 0.6 else float(rng.uniform(0.4, 1.1))
                video = {
                    "kind": "youtube#video",
                    "id": vid,
                    "snippet": {
                        "publishedAt": published.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "channelId": cid,
                        "title": title,
                        "description": f"A look at {topic}. Sources in the description.",
                    },
                    "contentDetails": {"duration": f"PT{int(rng.integers(0, 20))}M{int(rng.integers(5, 59))}S"},
                    "statistics": {
                        "viewCount": str(views),
                        "likeCount": str(int(views * rng.uniform(0.01, 0.06))),
                        "commentCount": str(int(views * rng.uniform(0.0005, 0.004))),
                    },
                }
                videos.append(video)
                prev_views = max(0, int(views - daily * accel))
                prev.append({**video, "statistics": {**video["statistics"], "viewCount": str(prev_views)}})  # type: ignore[dict-item]
                if k >= 8:  # comments only for the top results (what the miner reads)
                    continue
                comments = []
                for _c in range(int(rng.integers(8, 30))):
                    if rng.random() < 0.45:
                        text = QUESTION_PATTERNS[int(rng.integers(len(QUESTION_PATTERNS)))].format(t=topic)
                    else:
                        text = PRAISE[int(rng.integers(len(PRAISE)))]
                    comments.append(
                        {
                            "snippet": {
                                "totalReplyCount": int(rng.integers(0, 4)),
                                "topLevelComment": {
                                    "snippet": {
                                        "textDisplay": text,
                                        "likeCount": int(rng.integers(0, 400)),
                                    }
                                },
                            }
                        }
                    )
                all_comments[vid] = {"items": comments}
            total = int(rng.integers(300, 4000) * (1.5 - heat))
            (OUT / "search" / f"{_slug(q)}.json").write_text(
                json.dumps(
                    {
                        "kind": "youtube#searchListResponse",
                        "pageInfo": {"totalResults": total, "resultsPerPage": 25},
                        "items": items,
                    },
                    indent=1,
                ),
                encoding="utf-8",
            )
    (OUT / "videos.json").write_text(json.dumps({"items": videos}), encoding="utf-8")
    (OUT / "videos_prev.json").write_text(
        json.dumps({"items": prev, "taken_at": (NOW - timedelta(hours=24)).isoformat()}),
        encoding="utf-8",
    )
    (OUT / "comments.json").write_text(json.dumps(all_comments), encoding="utf-8")
    (OUT / "channels.json").write_text(json.dumps({"items": list(channels.values())}), encoding="utf-8")
    (OUT / "meta.json").write_text(
        json.dumps(
            {
                "synthetic": True,
                "now": NOW.isoformat(),
                "note": "Invented data shaped like YouTube API v3 responses. Not real.",
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"fixtures: {len(videos)} videos, {len(channels)} channels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
