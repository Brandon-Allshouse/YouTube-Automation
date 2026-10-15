"""Typed, validated configuration loading.

YAML is parsed with ``yaml.safe_load`` only and validated against pydantic models, so a malformed
or hostile config fails at load time instead of deep inside a render.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from src.common.paths import CONFIG_DIR, ensure_within


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class QuotaRules(_Strict):
    daily_limit_units: int = Field(gt=0)
    session_cap_units: int = Field(gt=0)
    cache_ttl_hours: float = Field(gt=0)


class CadenceRules(_Strict):
    max_shorts_per_day_per_channel: int = Field(ge=0, le=1)
    min_long_per_week_per_channel: int = Field(ge=0)
    max_long_per_week_per_channel: int = Field(ge=0, le=4)
    publish_jitter_minutes: int = Field(ge=0, le=180)


class KillScaleThresholds(_Strict):
    median_views_vs_niche_median_below: float | None = None
    median_views_vs_niche_median_above: float | None = None
    ctr_below: float | None = None
    avg_view_pct_below: float | None = None
    retained_30s_above: float | None = None


class KillScaleRules(_Strict):
    review_period_days: int = Field(gt=0)
    min_videos_before_decision: int = Field(gt=0)
    leading_indicators_hours: int = Field(gt=0)
    kill_if: KillScaleThresholds
    scale_if: KillScaleThresholds


class OutlierRules(_Strict):
    min_views_to_subs_ratio: float = Field(gt=0)
    window_days_min: int = Field(gt=0)
    window_days_max: int = Field(gt=0)


class DistinctnessRules(_Strict):
    max_similarity_same_channel: float = Field(gt=0, le=1)
    max_similarity_cross_channel: float = Field(gt=0, le=1)
    compare_last_n_videos: int = Field(gt=0)


class SafeZone(_Strict):
    top: float = Field(ge=0, lt=0.5)
    bottom: float = Field(ge=0, lt=0.5)
    right: float = Field(ge=0, lt=0.5)
    left: float = Field(ge=0, lt=0.5)


class QualityRules(_Strict):
    max_static_seconds: float = Field(gt=0)
    target_lufs: float
    lufs_tolerance: float = Field(gt=0)
    max_true_peak_dbtp: float
    max_silence_gap_s: float = Field(gt=0)
    min_contrast_ratio: float = Field(ge=1)
    max_words_on_screen: int = Field(gt=0)
    safe_zone_short: SafeZone
    safe_zone_long: SafeZone


class RenderLimits(_Strict):
    max_duration_s: float = Field(gt=0)
    max_width: int = Field(gt=0)
    max_height: int = Field(gt=0)
    max_fps: int = Field(gt=0)
    render_timeout_s: float = Field(gt=0)
    max_output_mb: int = Field(gt=0)


class MetricWeights(_Strict):
    """Weight of each 48 h indicator in the Video Performance Index (renormalized over available ones)."""

    views: float = Field(ge=0)
    ctr: float = Field(ge=0)
    avg_view_pct: float = Field(ge=0)
    retained_30s: float = Field(ge=0)
    engagement: float = Field(ge=0)


class MetricPriors(_Strict):
    """Baseline used before a channel has enough of its own videos (None = no prior, metric skipped)."""

    views: float | None = Field(default=None, gt=0)
    ctr: float | None = Field(default=None, gt=0)
    avg_view_pct: float | None = Field(default=None, gt=0)
    retained_30s: float | None = Field(default=None, gt=0)
    engagement: float | None = Field(default=None, gt=0)


class ScoringRules(_Strict):
    snapshot_hours: int = Field(gt=0)
    min_peers: int = Field(ge=1)
    confidence_impressions: float = Field(gt=0)
    steepness: float = Field(gt=0)
    hit_threshold: float = Field(gt=0, lt=100)
    breakout_multiple: float = Field(gt=1)
    form_half_life_days: float = Field(gt=0)
    form_window: int = Field(ge=1)
    weights: dict[Literal["short", "long"], MetricWeights]
    priors: dict[Literal["short", "long"], MetricPriors]


class Rules(_Strict):
    """Top-level schema of ``config/rules.yaml``."""

    quota: QuotaRules
    cadence: CadenceRules
    kill_scale: KillScaleRules
    outliers: OutlierRules
    distinctness: DistinctnessRules
    quality: QualityRules
    render: RenderLimits
    scoring: ScoringRules


def load_yaml(path: Path, *, root: Path = CONFIG_DIR) -> Any:
    """Safely parse a YAML file that must live under ``root``."""
    safe = ensure_within(path, root)
    with safe.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@functools.cache
def rules() -> Rules:
    """Load and validate ``config/rules.yaml`` (cached)."""
    return Rules.model_validate(load_yaml(CONFIG_DIR / "rules.yaml"))
