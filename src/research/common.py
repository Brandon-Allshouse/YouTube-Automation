"""Shared research plumbing: niche config, client setup, shared fetches, output files.

Every module fetches niche videos through :func:`niche_videos` with identical parameters, so in
live mode the second module to ask is served from the SQLite cache (no extra quota).
"""

from __future__ import annotations

import argparse
import functools
import json
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src import db as dbm
from src.common.config import load_yaml, rules
from src.common.paths import CONFIG_DIR, DATA_DIR, ensure_dir, safe_join
from src.research.youtube_api import Channel, Video, YouTubeClient, fixture_now

RESEARCH_DIR = DATA_DIR / "research"


class Niche(BaseModel):
    """A seed niche (``config/seed_niches.yaml``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    name: str = Field(max_length=80)
    category: str = Field(max_length=40)
    rpm_usd: tuple[float, float]
    visual_fit: float = Field(ge=0, le=1)
    evergreen: float = Field(ge=0, le=1)
    channel: Literal["atlas", "neon"]
    queries: tuple[str, ...] = Field(min_length=1, max_length=4)


@functools.cache
def load_niches() -> tuple[Niche, ...]:
    """All seed niches, validated."""
    data = load_yaml(CONFIG_DIR / "seed_niches.yaml")
    return tuple(Niche.model_validate(n) for n in data["niches"])


@dataclass
class Context:
    """Run context shared by modules."""

    client: YouTubeClient
    live: bool
    now: datetime
    niches: tuple[Niche, ...]


def add_common_args(p: argparse.ArgumentParser) -> None:
    """``--live`` and ``--niches`` flags."""
    p.add_argument("--live", action="store_true", help="call the real API (read-only, quota-capped)")
    p.add_argument("--niches", default="", help="comma-separated niche ids (default: all)")


def make_context(args: argparse.Namespace, *, db_path: Path | None = None) -> Context:
    """Create the client and resolve niches from parsed args."""
    conn = dbm.connect(db_path)
    client = YouTubeClient(live=bool(args.live), conn=conn)
    niches = load_niches()
    if args.niches:
        wanted = set(args.niches.split(","))
        niches = tuple(n for n in niches if n.id in wanted)
    now = datetime.now(UTC) if args.live else fixture_now()
    return Context(client, bool(args.live), now, niches)


@dataclass
class NicheData:
    """Videos and their channels for one niche."""

    niche: Niche
    videos: list[Video]
    channels: dict[str, Channel]
    supply: int  # approx. total results in the window across queries


def niche_videos(ctx: Context, niche: Niche) -> NicheData:
    """Search each query (last ``window_days_max`` days, by views), then fetch stats (cached)."""
    window = rules().outliers.window_days_max
    after = (ctx.now - timedelta(days=window)).replace(hour=0, minute=0, second=0, microsecond=0)
    ids: list[str] = []
    supply = 0
    for q in niche.queries:
        results, total = ctx.client.search(q, published_after=after, max_results=25)
        supply += total
        ids += [r.video_id for r in results if r.video_id not in ids]
    vids = ctx.client.videos(ids)
    chans = {c.id: c for c in ctx.client.channels([v.channel_id for v in vids])}
    return NicheData(niche, vids, chans, supply)


def age_days(v: Video, now: datetime) -> float:
    """Video age in days (≥ 0.1)."""
    return max((now - v.published_at).total_seconds() / 86400, 0.1)


def median(xs: list[float]) -> float:
    """Median (0 for empty)."""
    return float(statistics.median(xs)) if xs else 0.0


def write_output(module: str, payload: dict[str, Any], ctx: Context) -> Path:
    """Write ``data/research/<module>-<date>[-fixture].json`` and return the path."""
    ensure_dir(RESEARCH_DIR)
    suffix = "" if ctx.live else "-fixture"
    path = safe_join(RESEARCH_DIR, f"{module}-{ctx.now.date().isoformat()}{suffix}.json")
    payload = {
        "module": module,
        "mode": "live" if ctx.live else "fixture (SYNTHETIC data, not real)",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "reference_now": ctx.now.isoformat(timespec="seconds"),
        "api_units_this_run": ctx.client.units,
        **payload,
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def latest_output(module: str, live: bool) -> dict[str, Any] | None:
    """Most recent output of a module for the given mode."""
    if not RESEARCH_DIR.is_dir():
        return None
    files = sorted(p for p in RESEARCH_DIR.glob(f"{module}-*.json") if p.stem.endswith("-fixture") != live)
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None
