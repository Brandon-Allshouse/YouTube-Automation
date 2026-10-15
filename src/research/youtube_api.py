"""Read-only YouTube Data API v3 client with quota tracking, caching and fixture mode.

* **Fixture mode** (default): responses come from ``tests/fixtures/youtube/`` (recorded/synthetic
  responses shaped exactly like the API), so every module runs offline and deterministically.
* **Live mode** (``live=True``): HTTPS GET only, API key from the environment (never logged), hard
  daily cap from ``config/rules.yaml`` (session_cap_units) tracked in SQLite *before* each call,
  responses cached for ``cache_ttl_hours``.

All response text (titles, descriptions, comments) is untrusted: it is cleaned and length-capped
on parse, and validated with pydantic models that keep only the fields we use.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src import db as dbm
from src.common.config import rules
from src.common.log import get_logger, log_event
from src.common.paths import FIXTURES_DIR
from src.common.secrets import get_secret
from src.common.untrusted import clean_text

log = get_logger("src.research.api")

API_BASE = "https://www.googleapis.com/youtube/v3/"
FIXTURE_DIR = FIXTURES_DIR / "youtube"
COST = {"search": 100, "videos": 1, "channels": 1, "commentThreads": 1}
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


class QuotaExceededError(RuntimeError):
    """Raised when a call would exceed the configured daily cap (or the API reports exhaustion)."""


class ApiError(RuntimeError):
    """Raised for non-retryable API errors (message never contains the key or full URL)."""


class _Item(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class SearchResult(_Item):
    """A search hit (video)."""

    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{6,20}$")
    channel_id: str = Field(pattern=r"^[A-Za-z0-9_-]{6,40}$")
    title: str
    published_at: datetime

    @field_validator("title")
    @classmethod
    def _clean(cls, v: str) -> str:
        return clean_text(v, 200)


class Video(_Item):
    """Video statistics."""

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{6,20}$")
    channel_id: str
    title: str
    description: str = ""
    published_at: datetime
    duration_s: float = 0.0
    views: int = Field(ge=0)
    likes: int = Field(0, ge=0)
    comments: int = Field(0, ge=0)

    @field_validator("title", "description")
    @classmethod
    def _clean(cls, v: str) -> str:
        return clean_text(v, 500)


class Channel(_Item):
    """Channel statistics."""

    id: str
    title: str
    subscribers: int = Field(ge=0)
    video_count: int = Field(0, ge=0)
    hidden_subscribers: bool = False

    @field_validator("title")
    @classmethod
    def _clean(cls, v: str) -> str:
        return clean_text(v, 200)


class Comment(_Item):
    """Top-level comment."""

    text: str
    likes: int = Field(0, ge=0)
    replies: int = Field(0, ge=0)

    @field_validator("text")
    @classmethod
    def _clean(cls, v: str) -> str:
        return clean_text(v, 600)


def _iso_duration_s(d: str) -> float:
    import re

    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0.0
    days, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return float(days * 86400 + h * 3600 + mi * 60 + s)


def _slug(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "q"


class YouTubeClient:
    """Read-only client. Construct with ``live=False`` for fixtures (default)."""

    def __init__(
        self,
        *,
        live: bool = False,
        conn: sqlite3.Connection | None = None,
        fixture_dir: Path = FIXTURE_DIR,
    ) -> None:
        self.live = live
        self.conn = conn or dbm.connect()
        self.fixture_dir = fixture_dir
        self.calls = 0
        self.units = 0
        if live and not get_secret("YOUTUBE_API_KEY"):
            raise ApiError("YOUTUBE_API_KEY is not set; use fixture mode")

    # --- transport -------------------------------------------------------------------------
    def _cache_key(self, endpoint: str, params: dict[str, Any]) -> str:
        blob = json.dumps({"e": endpoint, "p": params}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def _get(self, endpoint: str, params: dict[str, Any], max_age_hours: float | None = None) -> dict[str, Any]:
        if endpoint not in COST:
            raise ApiError("endpoint not allowed")
        if not self.live:
            return self._fixture(endpoint, params)
        key = self._cache_key(endpoint, params)
        fetched_after = ""
        if max_age_hours is not None:
            fetched_after = (datetime.now(UTC) - timedelta(hours=max_age_hours)).isoformat(timespec="seconds")
        cached = dbm.cache_get(self.conn, key, fetched_after=fetched_after)
        if isinstance(cached, dict):
            return cached
        cost = COST[endpoint]
        cap = rules().quota.session_cap_units
        used = dbm.quota_used_today(self.conn)
        if used + cost > cap:
            raise QuotaExceededError(f"daily cap reached ({used}/{cap} units)")
        api_key = get_secret("YOUTUBE_API_KEY")
        query = urllib.parse.urlencode({**params, "key": api_key})
        url = f"{API_BASE}{endpoint}?{query}"
        data = self._fetch(url, endpoint)
        dbm.record_quota(self.conn, endpoint, cost)
        self.calls += 1
        self.units += cost
        expires = (datetime.now(UTC) + timedelta(hours=rules().quota.cache_ttl_hours)).isoformat(timespec="seconds")
        dbm.cache_put(self.conn, key, endpoint, data, expires)
        log_event(log, "api call", endpoint=endpoint, units=cost, used_today=used + cost)
        return data

    def _fetch(self, url: str, endpoint: str) -> dict[str, Any]:
        headers = {"Accept": "application/json", "User-Agent": "video-portfolio-research/1"}
        req = urllib.request.Request(url, headers=headers)  # noqa: S310 - fixed https API_BASE
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310  # nosec B310
                    raw = resp.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ApiError("response too large")
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise ApiError("unexpected response shape")
                return parsed
            except urllib.error.HTTPError as exc:
                body = exc.read(4000).decode(errors="replace")
                if exc.code == 403 and "quota" in body.lower():
                    raise QuotaExceededError("API reports quota exhausted") from None
                if exc.code >= 500 and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise ApiError(f"{endpoint}: HTTP {exc.code}") from None
            except urllib.error.URLError as exc:
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise ApiError(f"{endpoint}: network error {type(exc.reason).__name__}") from None
        raise ApiError(f"{endpoint}: retries exhausted")

    def _fixture(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        if endpoint == "search":
            path = self.fixture_dir / "search" / f"{_slug(str(params.get('q', '')))}.json"
            return (
                json.loads(path.read_text(encoding="utf-8"))
                if path.is_file()
                else {"items": [], "pageInfo": {"totalResults": 0}}
            )
        if endpoint == "commentThreads":
            store_c = json.loads((self.fixture_dir / "comments.json").read_text(encoding="utf-8"))
            found = store_c.get(str(params.get("videoId", "")))
            return found if isinstance(found, dict) else {"items": []}
        store = json.loads((self.fixture_dir / f"{endpoint}.json").read_text(encoding="utf-8"))
        wanted = set(str(params.get("id", "")).split(","))
        return {"items": [it for it in store.get("items", []) if it.get("id") in wanted]}

    # --- endpoints ---------------------------------------------------------------------------
    def search(
        self, q: str, *, published_after: datetime, max_results: int = 25, order: str = "viewCount"
    ) -> tuple[list[SearchResult], int]:
        """Search videos (100 units). Returns (results, approximate total results)."""
        params = {
            "part": "snippet",
            "type": "video",
            "q": clean_text(q, 100),
            "order": order if order in ("viewCount", "date", "relevance") else "viewCount",
            "maxResults": max(1, min(int(max_results), 50)),
            "publishedAfter": published_after.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "relevanceLanguage": "en",
        }
        data = self._get("search", params)
        out = []
        for it in data.get("items", []):
            try:
                out.append(
                    SearchResult(
                        video_id=it["id"]["videoId"],
                        channel_id=it["snippet"]["channelId"],
                        title=it["snippet"]["title"],
                        published_at=it["snippet"]["publishedAt"],
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue  # skip malformed items rather than trusting them
        return out, int(data.get("pageInfo", {}).get("totalResults", 0) or 0)

    def videos(self, ids: list[str], max_age_hours: float | None = None) -> list[Video]:
        """Video stats in batches of 50 (1 unit per batch).

        Args:
            ids: Video ids.
            max_age_hours: Ignore cached responses older than this (view counts for velocity
                snapshots must be fresh). None uses the normal cache lifetime.
        """
        out: list[Video] = []
        for i in range(0, len(ids), 50):
            data = self._get(
                "videos",
                {
                    "part": "snippet,statistics,contentDetails",
                    "id": ",".join(ids[i : i + 50]),
                    "maxResults": 50,
                },
                max_age_hours,
            )
            for it in data.get("items", []):
                try:
                    st, sn = it.get("statistics", {}), it["snippet"]
                    out.append(
                        Video(
                            id=it["id"],
                            channel_id=sn["channelId"],
                            title=sn.get("title", ""),
                            description=sn.get("description", ""),
                            published_at=sn["publishedAt"],
                            duration_s=_iso_duration_s(it.get("contentDetails", {}).get("duration", "")),
                            views=int(st.get("viewCount", 0)),
                            likes=int(st.get("likeCount", 0)),
                            comments=int(st.get("commentCount", 0)),
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    continue
        return out

    def channels(self, ids: list[str]) -> list[Channel]:
        """Channel stats in batches of 50 (1 unit per batch)."""
        out: list[Channel] = []
        uniq = sorted(set(ids))
        for i in range(0, len(uniq), 50):
            data = self._get(
                "channels",
                {"part": "snippet,statistics", "id": ",".join(uniq[i : i + 50]), "maxResults": 50},
            )
            for it in data.get("items", []):
                try:
                    st = it.get("statistics", {})
                    out.append(
                        Channel(
                            id=it["id"],
                            title=it["snippet"].get("title", ""),
                            subscribers=int(st.get("subscriberCount", 0)),
                            video_count=int(st.get("videoCount", 0)),
                            hidden_subscribers=bool(st.get("hiddenSubscriberCount", False)),
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    continue
        return out

    def comments(self, video_id: str, max_results: int = 50) -> list[Comment]:
        """Top-level comments by relevance (1 unit)."""
        try:
            data = self._get(
                "commentThreads",
                {
                    "part": "snippet",
                    "videoId": video_id,
                    "maxResults": max(1, min(max_results, 100)),
                    "order": "relevance",
                    "textFormat": "plainText",
                },
            )
        except ApiError:
            return []  # comments disabled etc.
        out = []
        for it in data.get("items", []):
            try:
                top = it["snippet"]["topLevelComment"]["snippet"]
                out.append(
                    Comment(
                        text=top.get("textDisplay", ""),
                        likes=int(top.get("likeCount", 0)),
                        replies=int(it["snippet"].get("totalReplyCount", 0)),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out


def fixture_now() -> datetime:
    """Reference 'now' for fixture data, so fixture runs are deterministic."""
    meta = FIXTURE_DIR / "meta.json"
    if meta.is_file():
        return datetime.fromisoformat(json.loads(meta.read_text(encoding="utf-8"))["now"])
    return datetime(2026, 10, 14, tzinfo=UTC)
