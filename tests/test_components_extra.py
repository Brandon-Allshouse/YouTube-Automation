"""Tests for step-6 components: geodesy, charts helpers, and render smoke tests."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from engine.components.charts import nice_ticks
from engine.core import geo
from engine.schema.spec import validate_spec

SPECS = Path(__file__).parent / "fixtures" / "specs"


def test_country_areas_match_reference() -> None:
    c = geo.load_countries("50m")
    assert c["GRL"].area_km2_hint / 1e6 == pytest.approx(2.17, rel=0.03)
    assert c["BRA"].area_km2_hint / 1e6 == pytest.approx(8.52, rel=0.03)
    africa = sum(f.area_km2_hint for f in c.values() if f.continent == "Africa")
    assert africa / 1e6 == pytest.approx(30.3, rel=0.03)


@given(lon=st.floats(-170, 170), lat=st.floats(-60, 60))
def test_rotation_preserves_area(lon: float, lat: float) -> None:
    rings = geo.load_countries("110m")["GRL"].rings
    before = sum(geo.ring_area_km2(r) for r in rings)
    after = sum(geo.ring_area_km2(r) for r in geo.rotate_to(rings, (lon, lat)))
    assert after == pytest.approx(before, rel=0.02)


def test_rotation_moves_centroid() -> None:
    rings = geo.load_countries("110m")["GRL"].rings
    moved = geo.rotate_to(rings, (20.0, 0.0))
    lon, lat = geo.centroid(moved)
    assert lon == pytest.approx(20.0, abs=0.5) and lat == pytest.approx(0.0, abs=0.5)


def test_mercator_reference_values() -> None:
    p = geo.project(np.array([[0.0, 0.0], [180.0, 0.0], [0.0, 60.0]]), "mercator")
    assert p[0] == pytest.approx([0, 0])
    assert p[1][0] == pytest.approx(math.pi)
    assert p[2][1] == pytest.approx(math.log(math.tan(math.pi / 4 + math.radians(30))))


def test_equal_earth_is_equal_area() -> None:
    # Equal-area: a 1x1 degree cell has the same projected area at any latitude, relative to cos(lat)
    def cell(lat: float) -> float:
        pts = np.array([[0, lat], [1, lat], [1, lat + 1], [0, lat + 1]], dtype=float)
        p = geo.project(pts, "equal_earth")
        x, y = p[:, 0], p[:, 1]
        return 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))

    ratio = (cell(60.0) / math.cos(math.radians(60.5))) / (cell(0.0) / math.cos(math.radians(0.5)))
    assert ratio == pytest.approx(1.0, rel=0.01)


def test_morph_endpoints() -> None:
    pts = np.array([[10.0, 50.0], [-40.0, -20.0]])
    a = geo.project(pts, "mercator")
    b = geo.project(pts, "equal_earth")
    assert np.allclose(geo.project(pts, morph=("mercator", "equal_earth", 0.0)), a)
    assert np.allclose(geo.project(pts, morph=("mercator", "equal_earth", 1.0)), b)


def test_densify_follows_great_circle() -> None:
    line = geo.densify(np.array([[-9.1, 38.7], [-74.0, 40.7]]), 2.0)
    assert len(line) > 20
    assert line[:, 1].max() > 43  # great circle Lisbon→NYC arcs well north of both endpoints


@given(lo=st.floats(-1e6, 1e6), span=st.floats(1e-3, 1e7))
def test_nice_ticks_cover_range(lo: float, span: float) -> None:
    hi = lo + span
    ticks = nice_ticks(lo, hi)
    assert ticks[0] <= lo + 1e-6 * max(1.0, abs(lo)) and ticks[-1] >= hi - 1e-6 * max(1.0, abs(hi))
    assert 2 <= len(ticks) <= 12


def test_unknown_country_rejected_at_build() -> None:
    from engine.build import build_from_spec

    d = json.loads((SPECS / "map_demo.json").read_text(encoding="utf-8"))
    d["scenes"][0]["elements"][0]["highlight"] = ["ZZZ"]
    with pytest.raises(ValueError, match="unknown country"):
        build_from_spec(json.dumps(d), 0.25)


def test_bar_race_shape_validation() -> None:
    d = json.loads((SPECS / "showcase.json").read_text(encoding="utf-8"))
    d["scenes"][1]["elements"][0]["frames"][0] = [1, 2]
    with pytest.raises(ValidationError, match="one value per label"):
        validate_spec(d)


@pytest.mark.parametrize("fixture", ["showcase.json", "map_demo.json"])
@pytest.mark.parametrize(("theme", "fmt"), [("neon", "short"), ("atlas", "long")])
def test_extra_components_render(fixture: str, theme: str, fmt: str) -> None:
    from engine.build import build_from_spec
    from engine.core.scene import FrameRenderer

    d = json.loads((SPECS / fixture).read_text(encoding="utf-8"))
    d["theme"], d["format"] = theme, fmt
    comp, ctx = build_from_spec(json.dumps(d), 0.25)
    fr = FrameRenderer(comp, ctx)
    for sc in validate_spec(d).scenes:
        frame = fr.render(int((sc.start + 2.0) * comp.fps))
        assert float(np.std(frame[..., :3])) > 3.0
