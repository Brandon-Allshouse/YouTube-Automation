"""Scene spec: the contract between the ``visual-director`` agent and the renderer.

Agent output is untrusted (OWASP LLM02), so every spec is validated here before anything renders:
unknown fields are rejected, strings and lists are length-capped, numbers are bounded, easing and
color names are checked, timings must be consistent, and global limits from ``config/rules.yaml``
cap duration and resolution. ``python -m engine.schema.spec --export`` writes the JSON Schema
(``engine/schema/scene_spec.schema.json``) that the agent is given.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from engine.core.easing import is_valid_easing
from src.common.config import rules

SCHEMA_PATH = Path(__file__).with_name("scene_spec.schema.json")

Region = Literal["full", "center", "upper", "lower", "top", "left", "right"]
ShortText = Annotated[str, Field(min_length=1, max_length=80)]
Seconds = Annotated[float, Field(ge=0.0, le=1200.0)]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class _Element(_M):
    """Fields shared by every visual element. Times are seconds relative to the scene start."""

    start: Seconds = 0.0
    end: Seconds | None = None
    region: Region = "center"

    @model_validator(mode="after")
    def _times(self) -> _Element:
        if self.end is not None and self.end <= self.start:
            raise ValueError("element end must be after start")
        return self


class KineticTitleSpec(_Element):
    """Big animated headline (≤ 7 words on screen)."""

    type: Literal["KineticTitle"]
    text: Annotated[str, Field(min_length=1, max_length=70)]
    kicker: Annotated[str, Field(max_length=32)] = ""
    emphasis: list[Annotated[str, Field(max_length=30)]] = Field(default_factory=list, max_length=4)
    size: Literal["xl", "l", "m"] = "l"
    entrance: Literal["mask", "rise", "pop", "slide"] = "mask"
    align: Literal["center", "left"] = "center"
    underline: bool = False

    @field_validator("text")
    @classmethod
    def _word_limit(cls, v: str) -> str:
        if len(v.split()) > 7:
            raise ValueError("titles are limited to 7 words on screen")
        return v


class StatCounterSpec(_Element):
    """Number that counts up and lands with emphasis."""

    type: Literal["StatCounter"]
    value: Annotated[float, Field(ge=-1e15, le=1e15)]
    from_value: Annotated[float, Field(ge=-1e15, le=1e15)] = 0.0
    decimals: Annotated[int, Field(ge=0, le=3)] = 0
    prefix: Annotated[str, Field(max_length=4)] = ""
    suffix: Annotated[str, Field(max_length=12)] = ""
    label: Annotated[str, Field(max_length=48)] = ""
    count_duration: Annotated[float, Field(gt=0, le=6)] = 1.6
    count_at: Seconds | None = None  # scene-relative time counting begins (default: on entrance)
    ring: Annotated[float, Field(ge=0, le=1)] | None = None  # optional progress ring fraction
    compact: bool = False  # 1.2M style


class ListRevealSpec(_Element):
    """Up to 6 items revealed one by one; earlier items dim to keep focus."""

    type: Literal["ListReveal"]
    title: Annotated[str, Field(max_length=40)] = ""
    items: list[Annotated[str, Field(min_length=1, max_length=48)]] = Field(min_length=1, max_length=6)
    reveal_at: list[Seconds] | None = None  # per-item times; default: staggered
    interval: Annotated[float, Field(gt=0, le=10)] = 0.9
    numbered: bool = True

    @model_validator(mode="after")
    def _reveal(self) -> ListRevealSpec:
        if self.reveal_at is not None and len(self.reveal_at) != len(self.items):
            raise ValueError("reveal_at must have one time per item")
        return self


class QuoteCardSpec(_Element):
    """Quotation on a card with attribution."""

    type: Literal["QuoteCard"]
    quote: Annotated[str, Field(min_length=1, max_length=180)]
    attribution: Annotated[str, Field(max_length=60)] = ""
    highlight: list[Annotated[str, Field(max_length=30)]] = Field(default_factory=list, max_length=4)


class SeriesSpec(_M):
    """One data series."""

    name: Annotated[str, Field(max_length=24)] = ""
    values: list[Annotated[float, Field(ge=-1e12, le=1e12)]] = Field(min_length=2, max_length=60)


class ComparisonSide(_M):
    """One side of a comparison."""

    label: Annotated[str, Field(min_length=1, max_length=24)]
    value: Annotated[float, Field(ge=0, le=1e15)]


class ComparisonSplitSpec(_Element):
    """Two quantities drawn to honest scale (area-true squares or bars)."""

    type: Literal["ComparisonSplit"]
    left: ComparisonSide
    right: ComparisonSide
    unit: Annotated[str, Field(max_length=8)] = ""
    mode: Literal["area", "bar"] = "area"
    decimals: Annotated[int, Field(ge=0, le=2)] = 1
    compact: bool = True
    show_ratio: bool = True
    stagger: Annotated[float, Field(ge=0, le=3)] = 0.35


class LineChartRevealSpec(_Element):
    """Line chart drawing left to right with an end-point label."""

    type: Literal["LineChartReveal"]
    title: Annotated[str, Field(max_length=48)] = ""
    x_labels: list[Annotated[str, Field(max_length=10)]] = Field(min_length=2, max_length=60)
    series: list[SeriesSpec] = Field(min_length=1, max_length=3)
    y_suffix: Annotated[str, Field(max_length=6)] = ""
    draw_duration: Annotated[float, Field(gt=0, le=10)] = 2.5


class BarRaceSpec(_Element):
    """Animated ranking bars over time."""

    type: Literal["BarRace"]
    title: Annotated[str, Field(max_length=48)] = ""
    labels: list[Annotated[str, Field(max_length=18)]] = Field(min_length=2, max_length=12)
    frames: list[list[Annotated[float, Field(ge=0, le=1e15)]]] = Field(min_length=1, max_length=40)
    frame_labels: list[Annotated[str, Field(max_length=10)]] = Field(default_factory=list, max_length=40)
    seconds_per_frame: Annotated[float, Field(gt=0.2, le=10)] = 1.2
    top_n: Annotated[int, Field(ge=2, le=10)] = 6
    value_suffix: Annotated[str, Field(max_length=6)] = ""

    @model_validator(mode="after")
    def _shape(self) -> BarRaceSpec:
        for row in self.frames:
            if len(row) != len(self.labels):
                raise ValueError("each frame needs one value per label")
        if self.frame_labels and len(self.frame_labels) != len(self.frames):
            raise ValueError("frame_labels must match frames")
        return self


class TimelineEventSpec(_M):
    """A dated event on a timeline."""

    date: Annotated[str, Field(min_length=1, max_length=14)]
    label: Annotated[str, Field(min_length=1, max_length=40)]


class TimelineSpec(_Element):
    """Horizontal/vertical timeline whose events pop in along a drawing line."""

    type: Literal["Timeline"]
    events: list[TimelineEventSpec] = Field(min_length=2, max_length=7)
    interval: Annotated[float, Field(gt=0, le=10)] = 0.8
    highlight: Annotated[int, Field(ge=-1, le=6)] = -1


LonLat = tuple[Annotated[float, Field(ge=-180, le=180)], Annotated[float, Field(ge=-90, le=90)]]
Continent = Literal["Africa", "Europe", "Asia", "North America", "South America", "Oceania", "Antarctica"]
Projection = Literal["mercator", "equal_earth", "equirect"]
CountryCode = Annotated[str, Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")]


class MapPathSpec(_M):
    """Route drawn on a map: great circle (shortest path) or rhumb line (constant compass bearing)."""

    points: list[LonLat] = Field(min_length=2, max_length=50)
    kind: Literal["great_circle", "rhumb"] = "great_circle"
    start: Seconds = 0.5
    duration: Annotated[float, Field(gt=0, le=15)] = 2.0


class MapMarkerSpec(_M):
    """Labelled, pulsing point on a map."""

    lon: Annotated[float, Field(ge=-180, le=180)]
    lat: Annotated[float, Field(ge=-90, le=90)]
    label: Annotated[str, Field(max_length=24)] = ""
    at: Seconds = 0.0


class MapMoveSpec(_M):
    """Slide a country across the globe by rigid rotation (keeps true size); re-projected live."""

    country: CountryCode
    to: LonLat
    start: Seconds = 0.5
    duration: Annotated[float, Field(gt=0, le=10)] = 2.5
    label: Annotated[str, Field(max_length=24)] = ""


class AnimatedMapSpec(_Element):
    """Natural Earth map: camera, projection morph, highlights, true-size moves, routes, markers."""

    type: Literal["AnimatedMap"]
    region: Region = "full"
    projection: Projection = "mercator"
    morph_to: Projection | None = None
    morph_start: Seconds = 0.5
    morph_duration: Annotated[float, Field(gt=0, le=10)] = 2.5
    center_from: tuple[Annotated[float, Field(ge=-180, le=180)], Annotated[float, Field(ge=-85, le=85)]] = (0.0, 20.0)
    center_to: tuple[Annotated[float, Field(ge=-180, le=180)], Annotated[float, Field(ge=-85, le=85)]] | None = None
    zoom_from: Annotated[float, Field(ge=0.5, le=60)] = 1.0
    zoom_to: Annotated[float, Field(ge=0.5, le=60)] | None = None
    move_start: Seconds = 0.0
    move_duration: Annotated[float, Field(gt=0, le=15)] = 2.5
    highlight: list[CountryCode] = Field(default_factory=list, max_length=20)
    highlight_continents: list[Continent] = Field(default_factory=list, max_length=7)
    highlight_at: Seconds = 0.6
    moves: list[MapMoveSpec] = Field(default_factory=list, max_length=4)
    paths: list[MapPathSpec] = Field(default_factory=list, max_length=5)
    markers: list[MapMarkerSpec] = Field(default_factory=list, max_length=10)
    graticule: bool = True
    resolution: Literal["50m", "110m"] = "50m"
    title: Annotated[str, Field(max_length=40)] = ""


class DiagramNodeSpec(_M):
    """Diagram node in a 0-1 layout grid."""

    id: Annotated[str, Field(pattern=r"^[a-z0-9_]{1,20}$")]
    label: Annotated[str, Field(min_length=1, max_length=28)]
    x: Annotated[float, Field(ge=0, le=1)]
    y: Annotated[float, Field(ge=0, le=1)]
    at: Seconds = 0.0
    accent: bool = False


class DiagramEdgeSpec(_M):
    """Arrow between two diagram nodes."""

    src: Annotated[str, Field(pattern=r"^[a-z0-9_]{1,20}$")]
    dst: Annotated[str, Field(pattern=r"^[a-z0-9_]{1,20}$")]
    label: Annotated[str, Field(max_length=20)] = ""
    at: Seconds = 0.0


class DiagramBuildSpec(_Element):
    """Nodes and arrows building step by step."""

    type: Literal["DiagramBuild"]
    nodes: list[DiagramNodeSpec] = Field(min_length=1, max_length=10)
    edges: list[DiagramEdgeSpec] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def _refs(self) -> DiagramBuildSpec:
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("duplicate diagram node id")
        for e in self.edges:
            if e.src not in ids or e.dst not in ids:
                raise ValueError("edge references unknown node")
        return self


class IconSceneSpec(_Element):
    """One of our code-drawn vector icons with an optional label."""

    type: Literal["IconScene"]
    icon: Literal[
        "globe",
        "clock",
        "bolt",
        "coin",
        "ship",
        "mountain",
        "atom",
        "chart",
        "warning",
        "crown",
        "book",
        "flame",
    ]
    label: Annotated[str, Field(max_length=32)] = ""
    accent: bool = True


class ParticleBackgroundSpec(_Element):
    """Ambient particle field layered over the theme background."""

    type: Literal["ParticleBackground"]
    count: Annotated[int, Field(ge=10, le=400)] = 120
    style: Literal["dust", "embers", "stars", "snow"] = "dust"
    region: Region = "full"


ElementSpec = Annotated[
    KineticTitleSpec
    | StatCounterSpec
    | ListRevealSpec
    | QuoteCardSpec
    | ComparisonSplitSpec
    | LineChartRevealSpec
    | BarRaceSpec
    | TimelineSpec
    | AnimatedMapSpec
    | DiagramBuildSpec
    | IconSceneSpec
    | ParticleBackgroundSpec,
    Field(discriminator="type"),
]


class TransitionSpecModel(_M):
    """Transition into a scene (defaults to the theme's)."""

    kind: Literal["wipe", "zoom", "slide", "glitch", "fade", "iris", "cut"] = "wipe"
    duration: Annotated[float, Field(gt=0, le=2)] = 0.45
    easing: str = "in_out_expo"

    @field_validator("easing")
    @classmethod
    def _easing(cls, v: str) -> str:
        if not is_valid_easing(v):
            raise ValueError("unknown easing")
        return v


class SceneSpec(_M):
    """One shot."""

    id: Annotated[str, Field(pattern=r"^[a-z0-9_-]{1,32}$")]
    start: Seconds
    transition: TransitionSpecModel | Literal["theme"] | None = "theme"
    elements: list[ElementSpec] = Field(min_length=1, max_length=8)
    camera_drift: Annotated[float, Field(ge=0, le=0.2)] | None = None
    note: Annotated[str, Field(max_length=200)] = ""  # segment-plan reference, not rendered


class CaptionWord(_M):
    """A spoken word with timing (seconds, global)."""

    w: Annotated[str, Field(min_length=1, max_length=40)]
    start: Seconds
    end: Seconds
    emph: bool = False

    @model_validator(mode="after")
    def _t(self) -> CaptionWord:
        if self.end < self.start:
            raise ValueError("caption word ends before it starts")
        return self


class CaptionsSpec(_M):
    """Word-level captions overlay."""

    enabled: bool = True
    words: list[CaptionWord] = Field(default_factory=list, max_length=6000)
    mode: Literal["pop", "karaoke", "box"] | None = None  # default: theme


class SceneSpecDocument(_M):
    """A complete video spec (spec + theme + seed fully determine the frames)."""

    version: Literal[1] = 1
    id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
    format: Literal["short", "long"]
    theme: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
    seed: Annotated[int, Field(ge=0, le=2**31 - 1)] = 0
    fps: Literal[24, 25, 30, 60] = 30
    duration: Annotated[float, Field(gt=0)]
    scenes: list[SceneSpec] = Field(min_length=1, max_length=200)
    captions: CaptionsSpec = CaptionsSpec()
    progress_bar: bool = False
    motion_blur: Annotated[int, Field(ge=1, le=4)] = 1

    @model_validator(mode="after")
    def _consistency(self) -> SceneSpecDocument:
        if self.duration > rules().render.max_duration_s:
            raise ValueError("duration exceeds render limit")
        if self.format == "short" and self.duration > 180:
            raise ValueError("shorts must be ≤ 180 s")
        starts = [s.start for s in self.scenes]
        if starts != sorted(starts) or len(set(starts)) != len(starts):
            raise ValueError("scene starts must be strictly increasing")
        if starts[0] != 0.0:
            raise ValueError("first scene must start at 0")
        if starts[-1] >= self.duration:
            raise ValueError("scene starts after the video ends")
        ids = [s.id for s in self.scenes]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate scene id")
        for w in self.captions.words:
            if w.end > self.duration + 0.5:
                raise ValueError("caption word beyond video duration")
        return self


def validate_spec(data: Any) -> SceneSpecDocument:
    """Validate untrusted spec data (dict or JSON string) and return the typed document."""
    if isinstance(data, str | bytes):
        return SceneSpecDocument.model_validate_json(data)
    return SceneSpecDocument.model_validate(data)


def export_schema(path: Path = SCHEMA_PATH) -> Path:
    """Write the JSON Schema for agents and external validators."""
    schema = SceneSpecDocument.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Scene spec (video-portfolio renderer)"
    path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    """CLI: ``--export`` the schema, or ``--validate FILE``."""
    p = argparse.ArgumentParser(description="Scene spec schema tools")
    p.add_argument("--export", action="store_true")
    p.add_argument("--validate", type=Path)
    a = p.parse_args(argv)
    if a.export:
        print(export_schema())
    if a.validate:
        from src.common.paths import REPO_ROOT, ensure_within

        doc = validate_spec(ensure_within(a.validate, REPO_ROOT).read_text(encoding="utf-8"))
        print(json.dumps({"ok": True, "id": doc.id, "scenes": len(doc.scenes), "duration": doc.duration}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
