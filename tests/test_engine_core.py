"""Tests for easing, timeline, color math, canvas and the frame pipeline."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from engine.core import easing
from engine.core.color import contrast_ratio, mix, parse_color, to_argb_int
from engine.core.timeline import Keyframe, Track, lerp_value, stagger, tween

unit = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)


@pytest.mark.parametrize("name", easing.EASING_NAMES)
def test_easing_endpoints(name: str) -> None:
    f = easing.get_easing(name)
    assert f(0.0) == pytest.approx(0.0, abs=1e-9)
    assert f(1.0) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize("name", easing.EASING_NAMES)
@given(u=unit)
def test_easing_finite_and_bounded(name: str, u: float) -> None:
    v = easing.get_easing(name)(u)
    assert math.isfinite(v)
    assert -0.6 <= v <= 1.6  # overshoot allowed, but never wild


@given(u=st.floats(min_value=-5, max_value=5, allow_nan=False))
def test_easing_clamps_input(u: float) -> None:
    f = easing.get_easing("out_cubic")
    assert 0.0 <= f(u) <= 1.0


@pytest.mark.parametrize("name", ["out_cubic", "in_out_cubic", "out_expo", "in_sine", "smooth", "linear"])
@given(a=unit, b=unit)
def test_monotonic_easings(name: str, a: float, b: float) -> None:
    f = easing.get_easing(name)
    lo, hi = min(a, b), max(a, b)
    assert f(lo) <= f(hi) + 1e-9


@given(x1=unit, y1=st.floats(-1, 2), x2=unit, y2=st.floats(-1, 2), u=unit)
def test_cubic_bezier_endpoints_and_finite(x1: float, y1: float, x2: float, y2: float, u: float) -> None:
    f = easing.cubic_bezier(x1, y1, x2, y2)
    assert f(0.0) == 0.0 and f(1.0) == 1.0
    assert math.isfinite(f(u))


def test_cubic_bezier_matches_linear_when_control_points_on_diagonal() -> None:
    f = easing.cubic_bezier(0.25, 0.25, 0.75, 0.75)
    for u in np.linspace(0, 1, 21):
        assert f(float(u)) == pytest.approx(float(u), abs=1e-5)


@given(k=st.floats(10, 1000), d=st.floats(1, 100))
def test_spring_endpoints(k: float, d: float) -> None:
    f = easing.spring(k, d)
    assert f(0.0) == 0.0 and f(1.0) == 1.0
    assert abs(f(0.999) - 1.0) < 0.05


def test_underdamped_spring_overshoots() -> None:
    f = easing.get_easing("spring_bouncy")
    assert max(f(u / 200) for u in range(201)) > 1.01


@pytest.mark.parametrize("bad", ["nope", "spring(1)", "cubic_bezier(2,0,0,1)", "spring(a,b)", "spring(nan,1)"])
def test_bad_easing_names(bad: str) -> None:
    assert not easing.is_valid_easing(bad)


def test_parametric_easings_parse() -> None:
    assert easing.is_valid_easing("spring(240, 14)")
    assert easing.is_valid_easing("cubic_bezier(0.2,0.8,0.2,1)")
    assert easing.is_valid_easing("back_out(2.5)")


# --- timeline -------------------------------------------------------------------------------


@given(t=st.floats(-10, 10, allow_nan=False))
def test_track_holds_outside_range(t: float) -> None:
    tr = Track([Keyframe(1.0, 10.0), Keyframe(2.0, 20.0, "linear")])
    v = tr.scalar_at(t)
    if t <= 1.0:
        assert v == 10.0
    elif t >= 2.0:
        assert v == 20.0
    else:
        assert v == pytest.approx(10.0 + 10.0 * (t - 1.0))


@given(times=st.lists(st.floats(0, 100), min_size=1, max_size=12), t=st.floats(-1, 101))
def test_track_value_within_hull_for_monotone_easing(times: list[float], t: float) -> None:
    keys = [Keyframe(tt, float(i), "out_cubic") for i, tt in enumerate(times)]
    v = Track(keys).scalar_at(t)
    assert -1e-9 <= v <= len(times) - 1 + 1e-9


def test_tuple_tracks() -> None:
    tr = tween(0.0, 1.0, (0.0, 0.0), (10.0, 20.0), "linear")
    assert tr.value_at(0.5) == pytest.approx((5.0, 10.0))
    with pytest.raises(ValueError, match="equal length"):
        lerp_value((1.0,), (1.0, 2.0), 0.5)
    with pytest.raises(TypeError):
        lerp_value(1.0, (1.0,), 0.5)


def test_track_requires_keyframes() -> None:
    with pytest.raises(ValueError, match="at least one"):
        Track([])


@given(
    n=st.integers(0, 40),
    off=st.floats(0.03, 0.08),
    mode=st.sampled_from(["forward", "reverse", "center"]),
)
def test_stagger_properties(n: int, off: float, mode: str) -> None:
    times = stagger(n, off, start=1.0, mode=mode)
    assert len(times) == n
    if n:
        assert min(times) == pytest.approx(1.0)
        assert max(times) == pytest.approx(1.0 + off * (n - 1))
        assert len({round(x, 9) for x in times}) == n


def test_stagger_bad_mode() -> None:
    with pytest.raises(ValueError, match="unknown stagger"):
        stagger(3, 0.05, mode="sideways")


# --- color ----------------------------------------------------------------------------------


def test_color_parse_and_pack() -> None:
    assert parse_color("#fff") == (1.0, 1.0, 1.0, 1.0)
    assert parse_color("#00000080")[3] == pytest.approx(128 / 255)
    assert to_argb_int(parse_color("#ff0000")) == 0xFFFF0000
    with pytest.raises(ValueError, match="invalid color"):
        parse_color("red")
    assert mix((0, 0, 0, 1), (1, 1, 1, 1), 0.5) == (0.5, 0.5, 0.5, 1.0)


def test_wcag_contrast_known_values() -> None:
    assert contrast_ratio(parse_color("#000"), parse_color("#fff")) == pytest.approx(21.0, rel=1e-3)
    assert contrast_ratio(parse_color("#777"), parse_color("#fff")) == pytest.approx(4.48, abs=0.02)


@pytest.mark.parametrize("theme", ["atlas", "neon"])
def test_theme_text_contrast_meets_wcag_aa(theme: str) -> None:
    from engine.core.theme import load_theme

    th = load_theme(theme)
    for bg in ("bg", "bg2", "surface"):
        assert contrast_ratio(th.color("text"), th.color(bg)) >= 4.5
        assert contrast_ratio(th.color("text_muted"), th.color(bg)) >= 4.5


# --- canvas + frame pipeline ----------------------------------------------------------------


def test_canvas_draws_text_and_shapes() -> None:
    from engine.core.canvas import Fonts, FontSpec, PathBuilder, Surface

    surf = Surface(200, 100, Fonts())
    cv = surf.canvas
    cv.clear((0, 0, 0, 1))
    cv.rect(10, 10, 50, 50, fill=(1, 0, 0, 1), radius=8)
    w = cv.text("Hi", 100, 60, FontSpec("Inter", 40, 800), (1, 1, 1, 1), letter_spacing=2)
    assert w > 20
    p = PathBuilder().move_to(0, 90).line_to(200, 90)
    assert p.length() == pytest.approx(200)
    cv.path(p, stroke=(0, 1, 0, 1), stroke_width=4, trim=(0, 0.5))
    arr = surf.array
    assert arr[35, 35, 0] == 255  # red square
    assert arr[90, 50, 1] > 200 and arr[90, 150, 1] < 10  # trimmed path drawn halfway only


def test_frame_render_is_deterministic() -> None:
    from engine.core.scene import FrameRenderer
    from engine.demo_core import build

    a = FrameRenderer(*build("neon", 7, 0.25)).render(80).copy()
    b = FrameRenderer(*build("neon", 7, 0.25)).render(80).copy()
    assert np.array_equal(a, b)
    c = FrameRenderer(*build("neon", 8, 0.25)).render(80).copy()
    assert not np.array_equal(a, c)  # seed changes grain/glow phase


@pytest.mark.slow
def test_render_with_audio_and_still(tmp_path: Path) -> None:
    from scipy.io import wavfile

    from engine.core.render import EncodeSettings, render_still, render_video, write_manifest
    from engine.demo_core import build

    wav = tmp_path / "a.wav"
    wavfile.write(wav, 48000, (np.sin(np.arange(48000 * 2) * 0.05) * 0.2).astype(np.float32))
    out = tmp_path / "x.mp4"
    info = render_video(
        build,
        ("neon", 2, 0.25),
        out,
        audio_path=wav,
        workers=2,
        frame_range=(0, 30),
        settings=EncodeSettings(crf=28, preset="veryfast"),
    )
    assert info["frames"] == 30 and out.stat().st_size > 1000
    png = render_still(build, ("neon", 2, 0.25), 3.0, tmp_path / "s.png")
    assert png.read_bytes()[1:4] == b"PNG"
    assert write_manifest(tmp_path, "x", {"a": 1}).is_file()
    with pytest.raises(ValueError, match="crf"):
        EncodeSettings(crf=99)
    with pytest.raises(ValueError, match="preset"):
        EncodeSettings(preset="nope")


@pytest.mark.slow
def test_render_video_small(tmp_path: Path) -> None:
    from engine.core.render import render_video
    from engine.demo_core import build

    out = tmp_path / "x.mp4"
    info = render_video(build, ("atlas", 1, 0.25), out, workers=2, frame_range=(60, 100))
    assert out.stat().st_size > 1000
    assert info["frames"] == 40
