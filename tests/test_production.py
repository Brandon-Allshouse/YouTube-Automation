"""Tests for the script model and the production orchestrator (synthetic TTS, no render)."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from engine.schema.spec import validate_spec
from src import db as dbm
from src.audio.captions import WordTiming
from src.audio.tts import SyntheticBackend
from src.production import produce as prod
from src.production.script import Script, load_script

DEMO = prod.DEMO_SCRIPT


def test_demo_script_valid() -> None:
    s = load_script(DEMO)
    assert s.channel == "atlas" and s.format == "short"
    assert len(s.titles) == 3 and len(s.thumbnails) == 3
    assert all(src.url.startswith("https://") for src in s.sources)


def test_script_rejects_bad_input() -> None:
    base = load_script(DEMO).model_dump()
    for mutate in (
        lambda d: d.update(id="../x"),
        lambda d: d["thumbnails"].append({"text": "five words is too many", "visual": "x"}),
        lambda d: d["sources"].append({"claim": "abc", "url": "http://insecure.example"}),
        lambda d: d.update(segments=[]),
        lambda d: d.update(extra_field=1),
    ):
        d = json.loads(json.dumps(base))
        mutate(d)
        with pytest.raises(ValidationError):
            Script.model_validate(d)


def _w(text: str, t: float) -> WordTiming:
    return WordTiming(text, t, t + 0.2)


def test_word_anchors_resolve() -> None:
    words = [
        _w("It", 10.0),
        _w("was", 10.3),
        _w("meant", 10.6),
        _w("to", 11.0),
        _w("meant", 12.0),
        _w("four.", 13.0),
    ]
    els = prod.resolve_elements(
        [
            {"type": "KineticTitle", "text": "A", "until_word": "meant#2"},
            {"type": "KineticTitle", "text": "B", "at_word": "to"},
            {"type": "StatCounter", "value": 4, "count_at_word": "four"},
            {"type": "ListReveal", "items": ["a", "b"], "reveal_words": ["was", "meant"]},
        ],
        words,
        scene_start=9.9,
    )
    assert els[0]["end"] == pytest.approx(12.0 - 9.9 + 0.05)
    assert els[1]["start"] == pytest.approx(11.0 - 9.9 - 0.05)
    assert els[2]["count_at"] == pytest.approx(13.0 - 9.9 - 0.15)
    assert els[3]["reveal_at"] == pytest.approx([0.36, 0.66])
    assert all("_word" not in k for e in els for k in e)


def test_produce_without_render() -> None:
    out = Path(prod.OUTPUT_DIR) / "test-produce"
    res = prod.produce(load_script(DEMO), out, backend=SyntheticBackend(), render=False)
    spec = validate_spec((out / res["outputs"]["spec"]).read_text(encoding="utf-8"))
    assert spec.duration == pytest.approx(res["duration_s"])
    starts = [s.start for s in spec.scenes]
    assert starts[0] == 0.0 and starts == sorted(starts)
    assert res["mix"]["lufs"] == pytest.approx(-14.0, abs=0.6)
    assert res["mix"]["true_peak_dbtp"] <= -1.0
    # captions show digits for {1569|fifteen sixty-nine}
    assert any(w.w.startswith("1569") for w in spec.captions.words)
    assert (out / f"{spec.id}.manifest.json").is_file()


def test_production_gate_refuses_unapproved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "p.sqlite"
    conn = dbm.connect(db_path)
    vid = dbm.add_video(conn, "atlas", "short", "t")
    dbm.transition(conn, vid, "scripted")
    conn.close()
    monkeypatch.setattr(dbm, "DEFAULT_DB", db_path)
    with pytest.raises(prod.GateError, match="human review gate"):
        prod.produce_gated(vid)
    with pytest.raises(prod.GateError, match="unknown"):
        prod.produce_gated("v-missing")


def test_scene_starts_respect_minimum_length() -> None:
    from src.audio.mixer import Narration

    nar = Narration(
        track=None,
        sentences=[("a", 0.25, 1.0), ("b", 1.3, 2.0), ("c", 2.1, 3.0)],
        words=[],
        duration=4.0,
    )  # type: ignore[arg-type]
    starts = prod.scene_starts(nar, [1, 1, 1])
    assert starts[0] == 0.0
    assert all(b - a >= prod.MIN_SCENE_S - 1e-9 for a, b in itertools.pairwise(starts))
