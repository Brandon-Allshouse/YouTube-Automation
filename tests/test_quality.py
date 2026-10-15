"""Tests for automated quality checks (writing, visual instrumentation, persistence filter, static detection)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.production.produce import DEMO_SCRIPT
from src.production.script import Script, load_script
from src.quality import visual, writing

SPECS = Path(__file__).parent / "fixtures" / "specs"


def _script_with(first: str, extra: str = "") -> Script:
    d = load_script(DEMO_SCRIPT).model_dump()
    d["segments"][0]["text"] = first
    if extra:
        d["segments"][1]["text"] = extra
    return Script.model_validate(d)


def test_demo_script_passes_writing() -> None:
    rep = writing.check_script(load_script(DEMO_SCRIPT))
    assert rep.passed, (rep.checks, rep.metrics)


def test_flat_opening_fails_hook() -> None:
    rep = writing.check_script(_script_with("This video is about maps and also about some other things today."))
    assert not rep.checks["hook_first_sentence"]


def test_banned_phrase_detected() -> None:
    rep = writing.check_script(
        _script_with("Why is Greenland so big?", "In today's video we look at maps. Let's dive in.")
    )
    assert not rep.checks["no_banned_phrases"]
    assert "let's dive in" in rep.notes[0]


def test_flesch_is_sane() -> None:
    assert writing.flesch(["The cat sat on the mat."]) > 90
    assert writing.flesch(["Incomprehensibilities characteristically internationalize."]) < 0


def test_text_recorder_tags_captions_and_content() -> None:
    from engine.build import build_from_spec
    from engine.core.canvas import TextRecord
    from engine.core.scene import FrameRenderer

    spec = json.loads((SPECS / "components_demo.json").read_text(encoding="utf-8"))
    comp, ctx = build_from_spec(json.dumps(spec), 0.25)
    fr = FrameRenderer(comp, ctx)
    recs: list[TextRecord] = []
    fr.surface.canvas.recorder = recs
    fr.render(60)  # 2 s: title + captions visible
    roles = {r.role for r in recs}
    assert roles == {"caption", "content"}
    assert all(r.box[2] > r.box[0] and r.box[3] > r.box[1] for r in recs)


def test_suppress_text_draws_no_text() -> None:
    from engine.core.canvas import Fonts, FontSpec, Surface

    s = Surface(200, 80, Fonts())
    s.canvas.clear((0, 0, 0, 1))
    s.canvas.suppress_text = True
    s.canvas.text("HELLO", 10, 60, FontSpec("Inter", 48, 800), (1, 1, 1, 1))
    assert int(s.array[..., :3].max()) == 0


def test_scan_components_demo_is_clean() -> None:
    spec = (SPECS / "components_demo.json").read_text(encoding="utf-8")
    rep = visual.scan_text(spec, sample_fps=1.0, scale=0.25)
    assert not rep.safe_zone_violations and not rep.offcanvas
    assert rep.samples == 16


def test_persistence_filter_drops_transients() -> None:
    rep = visual.VisualReport(
        word_violations=[{"t": 2.0, "words": 9}, {"t": 5.0, "words": 9}, {"t": 5.5, "words": 8}],
        contrast_failures=[
            {"t": 1.0, "text": "a", "ratio": 2, "need": 3},
            {"t": 1.5, "text": "a", "ratio": 2, "need": 3},
        ],
        max_words_on_screen=9,
    )
    out = visual._persistent(rep, 0.5)
    assert [v["t"] for v in out.word_violations] == [5.0, 5.5]
    assert len(out.contrast_failures) == 2
    assert out.max_words_on_screen == 9  # raw peak is still reported honestly


@pytest.mark.slow
def test_static_detection_on_still_video(tmp_path: Path) -> None:
    from src.common import proc
    from src.common.ffmpeg import ffmpeg_exe

    out = Path(__file__).resolve().parents[1] / "output" / "test-static.mp4"
    proc.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=108x192:d=6:r=30",
              "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], timeout=120)  # fmt: skip
    res = visual.static_runs(out, 6.0)
    assert res["longest_static_s"] >= 5.5
