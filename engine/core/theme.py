"""Channel themes: palette, typography, motion language, transitions, captions, background.

Themes are YAML files in ``config/themes/`` validated by :class:`Theme`. The same component
renders differently per theme, which is how channels get distinct visual identities.
"""

from __future__ import annotations

import functools
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from engine.core.canvas import FONT_FILES, FontSpec
from engine.core.color import RGBA, is_valid_color, parse_color
from engine.core.easing import is_valid_easing
from src.common.config import load_yaml
from src.common.paths import CONFIG_DIR, safe_join, validate_id

THEMES_DIR = CONFIG_DIR / "themes"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Palette(_M):
    """Named colors (hex strings)."""

    bg: str
    bg2: str
    surface: str
    text: str
    text_muted: str
    accent: str
    accent2: str
    highlight: str
    positive: str
    negative: str
    series: tuple[str, ...] = Field(min_length=3, max_length=10)

    @field_validator("*")
    @classmethod
    def _colors(cls, v: str | tuple[str, ...]) -> str | tuple[str, ...]:
        vals = v if isinstance(v, tuple) else (v,)
        for c in vals:
            if not is_valid_color(c):
                raise ValueError(f"invalid color {c!r}")
        return v

    def rgba(self, name: str) -> RGBA:
        """Return a palette entry by name as RGBA."""
        value = getattr(self, name)
        if not isinstance(value, str):
            raise KeyError(name)
        return parse_color(value)

    def series_rgba(self, i: int) -> RGBA:
        """i-th chart series color (cycles)."""
        return parse_color(self.series[i % len(self.series)])


class FontRole(_M):
    """A typographic role: family, weight, optional variable-font axes."""

    family: str
    weight: int = Field(ge=100, le=1000)
    axes: tuple[tuple[str, float], ...] = ()
    uppercase: bool = False
    tracking: float = 0.0  # letter spacing in em

    @field_validator("family")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in FONT_FILES:
            raise ValueError(f"unknown font family {v!r}")
        return v

    def spec(self, size: float) -> FontSpec:
        """FontSpec at ``size`` px."""
        return FontSpec(self.family, size, self.weight, self.axes)


class Fonts(_M):
    """Typographic roles."""

    display: FontRole
    body: FontRole
    caption: FontRole
    mono: FontRole
    thumb: FontRole | None = None  # thumbnail headline face (default: display)


class Motion(_M):
    """Motion language: one consistent set of curves and timings per channel."""

    enter: str = "out_expo"
    exit: str = "in_cubic"
    emphasis: str = "spring_bouncy"
    move: str = "in_out_cubic"
    enter_duration: float = Field(0.6, gt=0, le=3)
    stagger: float = Field(0.05, ge=0.03, le=0.08)  # 30-80 ms per the quality bar
    rise: float = Field(60.0, ge=0, le=400)  # px an element travels on entrance (at 1080 short side)
    camera_drift: float = Field(0.03, ge=0, le=0.2)  # slow Ken Burns scale per scene

    @field_validator("enter", "exit", "emphasis", "move")
    @classmethod
    def _easing(cls, v: str) -> str:
        if not is_valid_easing(v):
            raise ValueError(f"unknown easing {v!r}")
        return v


class TransitionStyle(_M):
    """Default scene transition."""

    kind: Literal["wipe", "zoom", "slide", "glitch", "fade", "iris"] = "wipe"
    duration: float = Field(0.45, gt=0, le=2)
    easing: str = "in_out_expo"


class CaptionStyle(_M):
    """Word caption look."""

    mode: Literal["pop", "karaoke", "box"] = "pop"
    size: float = Field(78.0, ge=30, le=200)  # px at 1080 short side
    y: float = Field(0.70, ge=0.3, le=0.85)  # vertical centre as fraction of height (shorts)
    y_long: float = Field(0.86, ge=0.5, le=0.95)
    max_words: int = Field(3, ge=1, le=7)
    max_chars: int = Field(18, ge=6, le=40)
    stroke: float = Field(10.0, ge=0, le=30)


class Background(_M):
    """Background treatment drawn under every scene."""

    kind: Literal["gradient_grain", "grid", "paper", "aurora"] = "gradient_grain"
    grain: float = Field(0.05, ge=0, le=0.3)
    vignette: float = Field(0.45, ge=0, le=1)
    glow: float = Field(0.35, ge=0, le=1)


class Theme(_M):
    """A complete channel look."""

    name: str
    description: str = ""
    palette: Palette
    fonts: Fonts
    motion: Motion = Motion()
    transition: TransitionStyle = TransitionStyle()
    captions: CaptionStyle = CaptionStyle()
    background: Background = Background()
    corner_radius: float = Field(28.0, ge=0, le=80)

    def color(self, name: str) -> RGBA:
        """Palette color by name."""
        return self.palette.rgba(name)


@functools.cache
def load_theme(name: str) -> Theme:
    """Load and validate ``config/themes/<name>.yaml``.

    Raises:
        ValueError / pydantic.ValidationError: invalid name or content.
    """
    validate_id(name, kind="theme name")
    path = safe_join(THEMES_DIR, f"{name}.yaml")
    return Theme.model_validate(load_yaml(path))
