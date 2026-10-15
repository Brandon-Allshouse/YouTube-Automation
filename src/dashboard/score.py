"""Success scoring: how well each video, channel and the whole portfolio works the algorithm.

Every number here is relative, robust and comparable (ADR-033):

* **Video Performance Index (VPI, 0-100, 50 = channel-typical).** Each video is judged at the same
  age (``scoring.snapshot_hours``, 48 h) against the *other* videos of its channel and format
  (leave-one-out median), or configured priors until the channel has ``min_peers`` videos. For
  each indicator the recommender rewards (views, CTR, average view %, retention at 30 s,
  engagement) we take ``log2(value / baseline)`` clipped to ±3, combine them with per-format
  weights (renormalized over the indicators we actually have), shrink toward 50 while the video
  has few impressions, and map through a logistic curve. 2x the baseline on everything ≈ 80;
  half of it ≈ 20.
* **Channel score (0-100, 50 = holding steady):** reach growth (are newer uploads getting more 48 h
  views than older ones?), recency-weighted form (mean VPI, half-life ``form_half_life_days``) and
  trend (Theil-Sen slope of VPI per upload). VPI is relative to the channel itself, so reach growth
  is the part that shows the algorithm distributing a channel more.
* **Portfolio score:** channel scores weighted by how many scored videos back them.
* **Levers:** arm ids (``niche|format|hook|length|visual``) are split into dimensions and each
  value gets an empirical-Bayes shrunk mean VPI, so a lucky single video can't top the table.

Metrics are platform-neutral names, so the same scoring can rank other platforms' videos later.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy.special import betaincinv

from src.common.config import MetricPriors, MetricWeights, ScoringRules

METRICS = ("views", "ctr", "avg_view_pct", "retained_30s", "engagement")
ARM_DIMENSIONS = ("niche", "format", "hook", "length", "visual")
CLIP = 3.0


@dataclass(frozen=True)
class VideoMetrics:
    """Indicators of one video at the scoring snapshot. Missing values are None."""

    video_id: str
    channel_id: str
    fmt: str
    views: float | None = None
    impressions: float | None = None
    ctr: float | None = None
    avg_view_pct: float | None = None
    retained_30s: float | None = None
    likes: float | None = None
    comments: float | None = None

    @property
    def engagement(self) -> float | None:
        """(likes + comments) per view."""
        if not self.views or self.likes is None:
            return None
        return (self.likes + (self.comments or 0.0)) / self.views

    def value(self, metric: str) -> float | None:
        """The value of one of ``METRICS``."""
        if metric == "engagement":
            return self.engagement
        v = getattr(self, metric)
        return None if v is None else float(v)


@dataclass(frozen=True)
class VideoScore:
    """A scored video."""

    video_id: str
    vpi: float
    components: dict[str, float]  # metric -> clipped log2 ratio vs baseline
    baseline: dict[str, float]
    baseline_source: str  # "peers" or "priors"
    confidence: float
    breakout: bool


def log_ratio(value: float, baseline: float) -> float:
    """``log2(value / baseline)`` clipped to ±CLIP; 0 when either side is non-positive."""
    if value <= 0 or baseline <= 0:
        return -CLIP if value <= 0 < baseline else 0.0
    return max(-CLIP, min(CLIP, math.log2(value / baseline)))


def logistic(x: float) -> float:
    """Numerically safe logistic function."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def baseline_for(
    video: VideoMetrics, peers: Sequence[VideoMetrics], rules: ScoringRules
) -> tuple[dict[str, float], str]:
    """Per-metric baseline for ``video``: median of its channel×format peers (itself excluded) or priors.

    Returns:
        (metric -> baseline, "peers" | "priors").
    """
    others = [
        p for p in peers if p.video_id != video.video_id and p.channel_id == video.channel_id and p.fmt == video.fmt
    ]
    if len(others) >= rules.min_peers:
        out: dict[str, float] = {}
        for m in METRICS:
            vals = [x for x in (p.value(m) for p in others) if x is not None and x > 0]
            if vals:
                out[m] = float(statistics.median(vals))
        return out, "peers"
    priors: MetricPriors | None = next((v for k, v in rules.priors.items() if k == video.fmt), None)
    if priors is None:
        return {}, "priors"
    return {m: float(v) for m in METRICS if (v := getattr(priors, m)) is not None}, "priors"


def score_video(video: VideoMetrics, peers: Sequence[VideoMetrics], rules: ScoringRules) -> VideoScore:
    """Video Performance Index for one video (see module docstring).

    Args:
        video: The video's snapshot indicators.
        peers: All snapshot indicators known (any channel/format; filtered here).
        rules: ``config/rules.yaml: scoring``.
    """
    baseline, source = baseline_for(video, peers, rules)
    weights: MetricWeights | None = next((v for k, v in rules.weights.items() if k == video.fmt), None)
    comps: dict[str, float] = {}
    num = den = 0.0
    for m in METRICS:
        w = float(getattr(weights, m)) if weights is not None else 0.0
        x = video.value(m)
        if w <= 0 or x is None or m not in baseline:
            continue
        r = log_ratio(x, baseline[m])
        comps[m] = round(r, 4)
        num += w * r
        den += w
    s = num / den if den else 0.0
    exposure = video.impressions if video.impressions else (video.views or 0.0)
    conf = exposure / (exposure + rules.confidence_impressions) if exposure > 0 else 0.0
    vpi = 100.0 * logistic(rules.steepness * s * conf)
    breakout = bool(
        source == "peers"
        and video.views is not None
        and "views" in baseline
        and video.views >= rules.breakout_multiple * baseline["views"]
    )
    return VideoScore(video.video_id, round(vpi, 1), comps, baseline, source, round(conf, 3), breakout)


def score_all(videos: Sequence[VideoMetrics], rules: ScoringRules) -> dict[str, VideoScore]:
    """Score every video against the others."""
    return {v.video_id: score_video(v, videos, rules) for v in videos}


def theil_sen(ys: Sequence[float]) -> float:
    """Robust slope of ``ys`` against 0..n-1 (median of pairwise slopes); 0 for fewer than 2 points."""
    n = len(ys)
    slopes = [(ys[j] - ys[i]) / (j - i) for i in range(n) for j in range(i + 1, n)]
    return float(statistics.median(slopes)) if slopes else 0.0


@dataclass(frozen=True)
class ChannelScore:
    """Channel-level success."""

    channel_id: str
    score: float | None  # None until the channel has scored videos
    form: float | None  # recency-weighted VPI (quality of recent uploads vs the channel's norm)
    hit_rate: float | None
    trend_per_video: float  # Theil-Sen slope of VPI per upload
    reach_growth: float | None  # newest half / older half of 48 h views (x-fold), None if < 4 videos
    scored_videos: int
    breakouts: int


def reach_growth_ratio(views_oldest_first: Sequence[float]) -> float | None:
    """Median 48 h views of the newer half divided by the older half (None below 4 videos or no views)."""
    n = len(views_oldest_first)
    if n < 4:
        return None
    half = n // 2
    old = statistics.median(views_oldest_first[:half])
    new = statistics.median(views_oldest_first[n - half :])
    if old <= 0:
        return None
    return new / old


def channel_score(
    channel_id: str,
    scored: Sequence[tuple[float, float, bool, float | None]],
    rules: ScoringRules,
) -> ChannelScore:
    """Combine a channel's recent videos into one 0-100 score (50 = holding steady).

    Args:
        channel_id: Channel.
        scored: (age_days, vpi, breakout, views_at_snapshot) per scored video, any order.
        rules: Scoring rules.

    Returns:
        Weighted mean of the available parts: 50 % reach growth (50 + 25·log2 of the newer/older
        48 h-views ratio, so 2x more reach per upload = 75), 30 % form (recency-weighted VPI) and
        20 % trend (50 + 10·VPI slope per upload). Each part is clipped to 0-100. VPI alone is
        relative to the channel's own median, so reach growth is what shows the algorithm giving a
        channel more distribution over time.
    """
    recent = sorted(scored, key=lambda t: t[0])[: rules.form_window]  # youngest first
    if not recent:
        return ChannelScore(channel_id, None, None, None, 0.0, None, 0, 0)
    ws = [0.5 ** (max(t[0], 0.0) / rules.form_half_life_days) for t in recent]
    form = sum(w * t[1] for w, t in zip(ws, recent, strict=True)) / sum(ws)
    hit = sum(1 for t in recent if t[1] >= rules.hit_threshold) / len(recent)
    chronological = sorted(recent, key=lambda t: -t[0])  # oldest → newest
    slope = theil_sen([t[1] for t in chronological])
    trend = max(0.0, min(100.0, 50.0 + 10.0 * slope))
    window = sorted(scored, key=lambda t: t[0])[: 2 * rules.form_window]
    growth = reach_growth_ratio([t[3] for t in sorted(window, key=lambda t: -t[0]) if t[3] is not None])
    parts = [(0.3, form), (0.2, trend)]
    if growth is not None:
        parts.append((0.5, max(0.0, min(100.0, 50.0 + 25.0 * math.log2(max(growth, 1e-6))))))
    score = sum(w * v for w, v in parts) / sum(w for w, _ in parts)
    return ChannelScore(
        channel_id,
        round(score, 1),
        round(form, 1),
        round(hit, 3),
        round(slope, 2),
        None if growth is None else round(growth, 3),
        len(recent),
        sum(1 for t in recent if t[2]),
    )


def portfolio_score(channels: Sequence[ChannelScore]) -> float | None:
    """Channel scores weighted by how many scored videos back them (None without any)."""
    pairs = [(c.score, c.scored_videos) for c in channels if c.score is not None and c.scored_videos > 0]
    total = sum(n for _, n in pairs)
    if not total:
        return None
    return round(sum(s * n for s, n in pairs) / total, 1)


def parse_arm(arm_id: str) -> dict[str, str]:
    """Split ``niche|format|hook|length|visual`` into named dimensions (missing parts are skipped)."""
    parts = arm_id.split("|")
    return {d: p for d, p in zip(ARM_DIMENSIONS, parts, strict=False) if p}


@dataclass(frozen=True)
class Lever:
    """How one value of one arm dimension performs."""

    dimension: str
    value: str
    n: int
    mean_vpi: float
    shrunk_vpi: float  # empirical-Bayes: pulled toward the overall mean when n is small
    lift: float  # shrunk_vpi - overall mean
    low_confidence: bool


def levers(rows: Sequence[tuple[str, float]], *, prior_strength: float = 3.0, min_n: int = 5) -> list[Lever]:
    """What works: shrunk mean VPI per arm-dimension value, best first within each dimension.

    Args:
        rows: (arm_id, vpi) per scored video with an arm.
        prior_strength: Pseudo-videos at the overall mean added to every group.
        min_n: Groups smaller than this are flagged low-confidence.
    """
    if not rows:
        return []
    overall = statistics.fmean(v for _, v in rows)
    groups: dict[tuple[str, str], list[float]] = {}
    for arm, vpi in rows:
        for dim, val in parse_arm(arm).items():
            groups.setdefault((dim, val), []).append(vpi)
    out = []
    for (dim, val), xs in groups.items():
        n = len(xs)
        mean = statistics.fmean(xs)
        shrunk = (n * mean + prior_strength * overall) / (n + prior_strength)
        out.append(Lever(dim, val, n, round(mean, 1), round(shrunk, 1), round(shrunk - overall, 1), n < min_n))
    order = {d: i for i, d in enumerate(ARM_DIMENSIONS)}
    return sorted(out, key=lambda lv: (order.get(lv.dimension, 99), -lv.shrunk_vpi))


@dataclass(frozen=True)
class ArmPosterior:
    """Bandit arm with credible interval and probability of being the best arm."""

    arm_id: str
    mean: float
    low: float  # 5th percentile
    high: float  # 95th percentile
    p_best: float
    pulls: int


def arm_posteriors(
    arms: Sequence[tuple[str, float, float, int]], *, draws: int = 4000, seed: int = 0
) -> list[ArmPosterior]:
    """Beta posteriors → 90 % credible intervals and P(best) by seeded Monte Carlo (deterministic).

    Args:
        arms: (arm_id, alpha, beta, pulls).
    """
    if not arms:
        return []
    rng = np.random.default_rng(seed)
    alphas = np.array([a for _, a, _, _ in arms], dtype=np.float64)
    betas = np.array([b for _, _, b, _ in arms], dtype=np.float64)
    samples = rng.beta(alphas[None, :], betas[None, :], size=(draws, len(arms)))
    wins = np.bincount(np.argmax(samples, axis=1), minlength=len(arms)) / draws
    out = [
        ArmPosterior(
            arm,
            round(a / (a + b), 4),
            round(float(betaincinv(a, b, 0.05)), 4),
            round(float(betaincinv(a, b, 0.95)), 4),
            round(float(wins[i]), 4),
            pulls,
        )
        for i, (arm, a, b, pulls) in enumerate(arms)
    ]
    return sorted(out, key=lambda p: -p.p_best)


@dataclass
class Cadence:
    """Upload cadence over the last 7 days vs the channel's target."""

    shorts: int = 0
    long: int = 0
    shorts_target: int = 0
    long_target: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def adherence(self) -> float | None:
        """Uploads / target, capped at 1 (over-posting is flagged in notes, not rewarded)."""
        target = self.shorts_target + self.long_target
        if not target:
            return None
        return round(min(self.shorts, self.shorts_target) / target + min(self.long, self.long_target) / target, 3)


def cadence(uploads_7d: Mapping[str, int], shorts_per_day: int, long_per_week: int) -> Cadence:
    """Compare the last 7 days of uploads (by format) with the channel's cadence."""
    c = Cadence(
        shorts=int(uploads_7d.get("short", 0)),
        long=int(uploads_7d.get("long", 0)),
        shorts_target=7 * shorts_per_day,
        long_target=long_per_week,
    )
    if c.shorts > c.shorts_target or c.long > c.long_target:
        c.notes.append("above the cadence limit (compliance risk)")
    return c
