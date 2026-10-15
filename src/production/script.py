"""Script documents: the scriptwriter agent's output and the input to production.

A script is YAML/JSON validated here (agent output is untrusted). Each segment carries narration
text (with ``*emphasis*`` and ``{display|spoken}`` markup) plus the visual-director's elements
for that segment. Elements may use ``at_word`` (start when a word is spoken) and, for lists,
``reveal_words`` (reveal each item on a spoken word); production resolves these to times.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.common.config import load_yaml
from src.common.paths import REPO_ROOT, ensure_within


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class Source(_M):
    """A citation backing a factual claim."""

    claim: Annotated[str, Field(min_length=3, max_length=300)]
    url: Annotated[str, Field(pattern=r"^https://[^\s]{4,500}$")]


class Segment(_M):
    """One beat of the video: narration + visuals."""

    id: Annotated[str, Field(pattern=r"^[a-z0-9_-]{1,32}$")]
    text: Annotated[str, Field(min_length=1, max_length=1200)]
    visual: list[dict[str, Any]] = Field(min_length=1, max_length=8)
    transition: dict[str, Any] | Literal["theme"] | None = "theme"
    tag: Annotated[str, Field(max_length=40)] = ""  # visual type tag from the segment plan


class Focal(_M):
    """The single focal visual of a thumbnail (rendered by ``src.production.thumbnail``)."""

    kind: Literal["silhouettes", "map_ghost", "stat", "icon"]
    countries: list[Annotated[str, Field(pattern=r"^[A-Z]{3}$")]] = Field(default_factory=list, max_length=3)
    labels: list[Annotated[str, Field(max_length=16)]] = Field(default_factory=list, max_length=3)
    continent: str | None = None  # draw a whole continent as one silhouette (first slot)
    value: Annotated[str, Field(max_length=8)] = ""
    icon: str = "globe"
    strike: bool = False


class ThumbnailConcept(_M):
    """A thumbnail idea (≤ 4 words of text).

    ``visual`` describes it for the human reviewer; ``focal`` (+ ``highlight``) makes it renderable,
    so the thumbnails are part of the approved script and are rendered during production.
    """

    text: Annotated[str, Field(min_length=1, max_length=32)]
    visual: Annotated[str, Field(max_length=200)]
    highlight: Annotated[str, Field(max_length=16)] = ""
    focal: Focal | None = None

    @field_validator("text")
    @classmethod
    def _words(cls, v: str) -> str:
        if len(v.split()) > 4:
            raise ValueError("thumbnail text is limited to 4 words")
        return v


class Script(_M):
    """A complete, reviewable script."""

    id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
    channel: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
    format: Literal["short", "long"]
    seed: Annotated[int, Field(ge=0, le=2**31 - 1)] = 0
    titles: list[Annotated[str, Field(min_length=5, max_length=100)]] = Field(min_length=1, max_length=3)
    thumbnails: list[ThumbnailConcept] = Field(default_factory=list, max_length=3)
    description: Annotated[str, Field(max_length=4000)] = ""
    segments: list[Segment] = Field(min_length=1, max_length=120)
    sources: list[Source] = Field(default_factory=list, max_length=60)
    ai_disclosure: bool = False  # true if realistic synthetic people/places/events appear
    # Learning-loop labels (src.learning.arms): a seed niche of this channel and the hook type.
    niche: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")] | None = None
    hook: Literal["question", "statement", "number", "stakes", "story"] | None = None

    @property
    def narration(self) -> str:
        """All narration text in order."""
        return " ".join(s.text for s in self.segments)


def load_script(path: Path) -> Script:
    """Load and validate a script file inside the repository."""
    safe = ensure_within(path, REPO_ROOT)
    return Script.model_validate(load_yaml(safe, root=REPO_ROOT))
