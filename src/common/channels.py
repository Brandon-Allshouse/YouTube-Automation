"""Channel config (``config/channels/<id>.yaml``): identity, voice, music, writing, cadence."""

from __future__ import annotations

import functools
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.audio.tts import VoiceSpec
from src.common.config import load_yaml
from src.common.paths import CONFIG_DIR, safe_join, validate_id

CHANNELS_DIR = CONFIG_DIR / "channels"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MusicConfig(_M):
    """Channel music identity."""

    style: Literal["cinematic", "synthwave", "lofi", "ambient"]
    seed: int = Field(ge=0, le=2**31 - 1)
    music_rel_db: float = Field(-12.0, ge=-30, le=-6)


class WritingConfig(_M):
    """Channel writing voice."""

    persona: str = Field(max_length=400)
    banned_phrases: tuple[str, ...] = ()
    shorts_length_s: tuple[int, int] = (35, 55)
    long_length_min: tuple[int, int] = (8, 14)


class CadenceConfig(_M):
    """Publishing cadence (bounded by config/rules.yaml)."""

    shorts_per_day: int = Field(1, ge=0, le=1)
    long_per_week: int = Field(2, ge=0, le=4)
    preferred_hours_local: tuple[int, ...] = (12, 18)
    timezone: str = Field("America/New_York", pattern=r"^[A-Za-z_]+(/[A-Za-z_+-]+){0,2}$")


class Channel(_M):
    """A channel's full identity."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    name: str = Field(max_length=100)
    niche: str = Field(max_length=64)
    theme: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    voice: VoiceSpec
    music: MusicConfig
    writing: WritingConfig
    cadence: CadenceConfig = CadenceConfig()


@functools.cache
def load_channel(channel_id: str) -> Channel:
    """Load and validate ``config/channels/<id>.yaml``."""
    validate_id(channel_id, kind="channel id")
    return Channel.model_validate(load_yaml(safe_join(CHANNELS_DIR, f"{channel_id}.yaml")))


def all_channels() -> list[Channel]:
    """Every configured channel."""
    return [load_channel(p.stem) for p in sorted(CHANNELS_DIR.glob("*.yaml"))]
