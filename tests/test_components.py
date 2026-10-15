"""Tests for the scene spec validator, text layout, caption paging and component rendering."""

from __future__ import annotations

import copy
import itertools
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from engine.components.captions import display_word, paginate
from engine.components.stat_counter import format_number
from engine.core.text import balanced_breaks
from engine.schema.spec import SCHEMA_PATH, CaptionWord, SceneSpecDocument, validate_spec

FIXTURE = Path(__file__).parent / "fixtures" / "specs" / "components_demo.json"


def _spec() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_spec_valid() -> None:
    doc = validate_spec(_spec())
    assert doc.id == "components-demo"
    assert len(doc.scenes) == 4


def _mutate(path: list[Any], value: Any) -> dict[str, Any]:
    d = copy.deepcopy(_spec())
    cur: Any = d
    for k in path[:-1]:
        cur = cur[k]
    cur[path[-1]] = value
    return d


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (
            ["scenes", 0, "elements", 0, "text"],
            "one two three four five six seven eight",
        ),  # >7 words
        (["scenes", 0, "elements", 0, "evil"], "x"),  # unknown field
        (["scenes", 0, "elements", 0, "type"], "Exec"),  # unknown component
        (["scenes", 1, "start"], 0.0),  # non-increasing
        (["scenes", 0, "start"], 1.0),  # first scene not at 0
        (["duration"], 99999.0),  # beyond render limit
        (["id"], "../../etc"),  # path-like id
        (["theme"], "../secrets"),
        (["fps"], 1000),
        (["scenes", 3, "transition"], {"kind": "wipe", "easing": "__import__('os')"}),
        (["scenes", 1, "elements", 0, "value"], float("1e300")),
        (["scenes", 2, "elements", 0, "items"], ["a"] * 7),
        (["scenes", 0, "elements"], []),
    ],
)
def test_spec_rejects_hostile_or_invalid(path: list[Any], value: Any) -> None:
    with pytest.raises(ValidationError):
        validate_spec(_mutate(path, value))


def test_shorts_duration_cap() -> None:
    d = _spec()
    d["duration"] = 200.0
    with pytest.raises(ValidationError, match="180"):
        validate_spec(d)


def test_caption_word_order_enforced() -> None:
    with pytest.raises(ValidationError):
        CaptionWord(w="x", start=2.0, end=1.0)


def test_diagram_edges_must_reference_nodes() -> None:
    d = _spec()
    d["scenes"][0]["elements"] = [
        {
            "type": "DiagramBuild",
            "nodes": [{"id": "a", "label": "A", "x": 0.2, "y": 0.5}],
            "edges": [{"src": "a", "dst": "zz"}],
        }
    ]
    with pytest.raises(ValidationError, match="unknown node"):
        validate_spec(d)


def test_exported_schema_in_sync() -> None:
    on_disk = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    generated = SceneSpecDocument.model_json_schema()
    assert on_disk["$defs"].keys() == generated["$defs"].keys()
    assert on_disk["properties"] == generated["properties"]


# --- text layout ----------------------------------------------------------------------------


@given(widths=st.lists(st.floats(1, 300), min_size=0, max_size=30), max_w=st.floats(50, 1000))
def test_balanced_breaks_cover_all_words_in_order(widths: list[float], max_w: float) -> None:
    lines = balanced_breaks(widths, 10.0, max_w)
    flat = [i for ln in lines for i in ln]
    assert flat == list(range(len(widths)))
    for ln in lines:
        w = sum(widths[i] for i in ln) + 10.0 * (len(ln) - 1)
        assert w <= max_w + 1e-6 or len(ln) == 1


def test_balanced_breaks_prefers_even_lines() -> None:
    lines = balanced_breaks([100.0] * 5, 10.0, 400.0)
    assert [len(x) for x in lines] in ([3, 2], [2, 3])


# --- captions -------------------------------------------------------------------------------


def _words(text: str, gap_after: dict[int, float] | None = None) -> list[CaptionWord]:
    out, t = [], 0.0
    for i, w in enumerate(text.split()):
        out.append(CaptionWord(w=w, start=t, end=t + 0.3))
        t += 0.35 + (gap_after or {}).get(i, 0.0)
    return out


def test_paginate_breaks_on_sentence_and_limits() -> None:
    pages = paginate(_words("One two three four. Five six"), max_words=3, max_chars=40)
    texts = [" ".join(w.w for w in p.words) for p in pages]
    assert texts == ["One two three", "four.", "Five six"]


def test_paginate_breaks_on_pause() -> None:
    pages = paginate(_words("alpha beta gamma", {0: 0.6}), max_words=5, max_chars=40)
    assert [len(p.words) for p in pages] == [1, 2]


def test_pages_never_overlap() -> None:
    pages = paginate(_words("a b c d e f g h i j k"), 2, 10)
    for a, b in itertools.pairwise(pages):
        assert a.end <= b.start + 1e-9


def test_display_word() -> None:
    assert display_word("world,") == "world"
    assert display_word("why?") == "why?"
    assert display_word("...") == "..."


@pytest.mark.parametrize(
    ("v", "d", "c", "out"),
    [
        (1234567, 0, False, "1,234,567"),
        (1234567, 1, True, "1.2M"),
        (3.14159, 2, False, "3.14"),
        (950, 0, True, "950"),
        (2_500_000_000, 1, True, "2.5B"),
    ],
)
def test_format_number(v: float, d: int, c: bool, out: str) -> None:
    assert format_number(v, d, c) == out


# --- rendering ------------------------------------------------------------------------------


@pytest.mark.parametrize("theme", ["atlas", "neon"])
@pytest.mark.parametrize("fmt", ["short", "long"])
def test_every_scene_renders(theme: str, fmt: str) -> None:
    from engine.build import build_from_spec
    from engine.core.scene import FrameRenderer

    d = _spec()
    d["theme"], d["format"] = theme, fmt
    comp, ctx = build_from_spec(json.dumps(d), 0.25)
    fr = FrameRenderer(comp, ctx)
    for sc in validate_spec(d).scenes:
        frame = fr.render(int((sc.start + 1.8) * comp.fps))
        assert frame.shape == (ctx.height, ctx.width, 4)
        assert float(np.std(frame[..., :3])) > 3.0  # not a blank frame
