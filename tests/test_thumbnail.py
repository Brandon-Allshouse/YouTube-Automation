"""Tests for the thumbnail engine and the phone-size legibility checker."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from engine.core import geo
from engine.core.canvas import Fonts, Picture, Surface
from src.production.thumbnail import DEMO_VARIANTS, Focal, ThumbSpec, build_variant
from src.quality.legibility import TextBox, check_legibility


def test_thumb_spec_word_limit() -> None:
    with pytest.raises(ValidationError, match="4 words"):
        ThumbSpec(
            id="x",
            theme="atlas",
            text="one two three four five",
            focal=Focal(kind="stat", value="1"),
        )


@pytest.mark.parametrize("spec", DEMO_VARIANTS, ids=lambda s: s.id)
def test_demo_variants_pass_legibility(spec: ThumbSpec, tmp_path: Path) -> None:
    out = Path(__file__).resolve().parents[1] / "output" / "test-thumbs"
    rep = build_variant(spec, out)
    assert rep.passed, rep.as_dict()
    assert (out / f"{spec.id}.phone.png").is_file()


@pytest.mark.parametrize("kind", ["stat", "icon"])
def test_other_focals_render(kind: str) -> None:
    out = Path(__file__).resolve().parents[1] / "output" / "test-thumbs"
    spec = ThumbSpec(
        id=f"t-{kind}",
        theme="neon",
        text="Tiny atoms",
        highlight="atoms",
        focal=Focal(kind=kind, value="10¹⁵", icon="atom"),
    )  # type: ignore[arg-type]
    assert build_variant(spec, out).checks["max_4_words"]


def _plate(tmp_path: Path, rgb: tuple[int, int, int]) -> Path:
    arr = np.zeros((720, 1280, 4), dtype=np.uint8)
    arr[..., :3] = rgb
    arr[..., 3] = 255
    p = tmp_path / "plate.png"
    p.write_bytes(Picture.from_array(arr).to_png())
    return p


def test_checker_rejects_low_contrast_and_tiny_text(tmp_path: Path) -> None:
    plate = _plate(tmp_path, (230, 230, 230))
    boxes = [TextBox(60, 300, 200, 30, (1, 1, 1, 1))]  # white on near-white, 30 px caps
    rep = check_legibility(plate, boxes, word_count=5)
    assert not rep.passed
    assert not rep.checks["max_4_words"]
    assert not rep.checks["cap_height_small"]
    assert not rep.checks["contrast_median_4_5"]


def test_checker_flags_duration_badge_zone(tmp_path: Path) -> None:
    plate = _plate(tmp_path, (10, 10, 20))
    rep = check_legibility(plate, [TextBox(1100, 640, 150, 70, (1, 1, 1, 1))], word_count=1)
    assert not rep.checks["clear_of_duration_badge"]


def test_checker_passes_good_text(tmp_path: Path) -> None:
    plate = _plate(tmp_path, (10, 10, 20))
    rep = check_legibility(plate, [TextBox(60, 200, 500, 140, (1, 1, 1, 1))], word_count=2)
    assert rep.passed


def test_slide_keeps_area_and_north_up() -> None:
    rings = geo.load_countries("110m")["GRL"].rings
    moved = geo.slide_to(rings, (0.0, 0.0))
    before = sum(geo.ring_area_km2(r) for r in rings)
    after = sum(geo.ring_area_km2(r) for r in moved)
    assert after == pytest.approx(before, rel=0.02)
    # north-up: Greenland is taller than wide in true shape; after the slide it stays that way
    big = max(moved, key=len)
    assert np.ptp(big[:, 1]) > np.ptp(big[:, 0]) * 0.6
    lon, lat = geo.centroid(moved)
    assert lon == pytest.approx(0.0, abs=0.5) and lat == pytest.approx(0.0, abs=0.5)


def test_picture_resize_roundtrip() -> None:
    s = Surface(64, 32, Fonts())
    s.canvas.clear((1, 0, 0, 1))
    small = s.snapshot().resized(16, 8)
    assert small.width == 16 and small.to_array()[4, 8, 0] > 240
