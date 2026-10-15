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


def test_number_hook_counts_when_spoken_as_words() -> None:
    # regression (2026-10-15): "{73|seventy-three}" put no digit in the spoken text, so number
    # hooks failed; the number is on screen, so the display side counts
    rep = writing.check_script(_script_with("In {73|seventy-three} years, the Mongols went from tribes to an empire."))
    assert rep.checks["hook_first_sentence"]


@pytest.mark.parametrize("theme", ["atlas", "neon"])
def test_long_caption_lines_stay_in_safe_zone(theme: str) -> None:
    # regression (2026-10-15): captions were centred on the frame, but the Shorts safe zone is offset
    # left of the right-edge buttons, so a full-width line crossed into them
    spec = json.loads((SPECS / "components_demo.json").read_text(encoding="utf-8"))
    spec["theme"] = theme
    words = "united the Mongolian confederations unexpectedly internationally".split()
    spec["captions"]["words"] = [{"w": w, "start": 0.3 + 0.5 * i, "end": 0.75 + 0.5 * i} for i, w in enumerate(words)]
    rep = visual.scan_text(json.dumps(spec), sample_fps=2.0, scale=0.25)
    assert not [v for v in rep.safe_zone_violations if v["role"] == "caption"], rep.safe_zone_violations[:3]


def test_box_caption_active_word_is_not_a_contrast_failure() -> None:
    # regression (2026-10-15): neon's box captions draw the active word in the bg color on an accent
    # box; the checker compared it with the dark outline and reported 1.03:1
    spec = json.loads((SPECS / "components_demo.json").read_text(encoding="utf-8"))
    spec["theme"] = "neon"
    rep = visual.scan_text(json.dumps(spec), sample_fps=2.0, scale=0.25)
    caption_words = {w["w"].strip(".,:").upper() for w in spec["captions"]["words"]}
    assert not [f for f in rep.contrast_failures if f["text"].strip(".,:").upper() in caption_words]


def test_recorder_flags_characters_the_font_cannot_draw() -> None:
    # regression (2026-10-15): "Tahiti → Wallis" in Fraunces rendered the arrow as an empty box
    from engine.core.canvas import Fonts, FontSpec, Surface, TextRecord

    s = Surface(400, 100, Fonts())
    recs: list[TextRecord] = []
    s.canvas.recorder = recs
    s.canvas.text("Tahiti → Wallis", 10, 60, FontSpec("Fraunces", 40, 800), (1, 1, 1, 1))
    s.canvas.text("Tahiti to Wallis", 10, 60, FontSpec("Fraunces", 40, 800), (1, 1, 1, 1))
    assert recs[0].missing_glyphs == "→" and recs[1].missing_glyphs == ""
