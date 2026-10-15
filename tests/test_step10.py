"""Tests for the distinctness guard, the human review queue, and crosspost exports."""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest

from engine.schema.spec import CaptionWord
from src import db as dbm
from src.compliance import distinctness as dist
from src.production.produce import DEMO_SCRIPT
from src.production.script import Script, load_script
from src.publish.crosspost_export import PLATFORMS, build_srt, caption_text, srt_timestamp
from src.review import queue


def _script(**changes: object) -> Script:
    d = load_script(DEMO_SCRIPT).model_dump()
    d.update(changes)
    return Script.model_validate(d)


def _other_topic() -> Script:
    d = load_script(DEMO_SCRIPT).model_dump()
    d["id"] = "planes"
    d["titles"] = [
        "Why Planes Fly So High",
        "The Altitude Trick Airlines Use",
        "Cruising at 35,000 Feet Explained",
    ]
    d["segments"] = [
        {
            "id": "a",
            "tag": "stat",
            "text": "Airliners cruise near thirty five thousand feet because thin air means less drag.",
            "visual": [{"type": "KineticTitle", "text": "Thin air, less drag"}],
        },
        {
            "id": "b",
            "tag": "diagram",
            "text": "Jet engines are also more efficient in the cold air up there.",
            "visual": [{"type": "KineticTitle", "text": "Cold air helps"}],
        },
    ]
    return Script.model_validate(d)


def test_identical_script_is_blocked(db: sqlite3.Connection) -> None:
    s = _script()
    dist.register(s, "v-original01", db)
    v = dist.check(s, db)
    assert not v.passed
    assert v.max_same_channel > 0.95


def test_unrelated_script_passes(db: sqlite3.Connection) -> None:
    dist.register(_script(), "v-original01", db)
    v = dist.check(_other_topic(), db)
    assert v.passed, v.reasons
    assert v.max_same_channel < 0.3


def test_cross_channel_uses_stricter_threshold(db: sqlite3.Connection) -> None:
    dist.register(_script(), "v-atlas00001", db)
    copy = _script(channel="neon", id="mercator-copy")
    v = dist.check(copy, db)
    assert not v.passed
    assert any("other-channel" in r for r in v.reasons)


def test_check_excludes_self(db: sqlite3.Connection) -> None:
    s = _script()
    dist.register(s, "v-self000001", db)
    assert dist.check(s, db, exclude_video="v-self000001").passed


# --- review queue ---------------------------------------------------------------------------


class FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


SCRIPTS = Path(__file__).resolve().parents[1] / "data" / "scripts"


def _scripted(db: sqlite3.Connection) -> str:
    vid = dbm.add_video(db, "atlas", "short", "Mercator")
    SCRIPTS.mkdir(parents=True, exist_ok=True)
    path = SCRIPTS / f"test-{vid}.yaml"
    path.write_text(DEMO_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    dbm.set_path(db, vid, "script_path", str(path.relative_to(SCRIPTS.parents[1])))
    dbm.transition(db, vid, "scripted")
    return vid


def test_approve_requires_tty(db: sqlite3.Connection) -> None:
    vid = _scripted(db)
    with pytest.raises(queue.ApprovalRefusedError, match="interactive"):
        queue.approve(db, vid, "Owner", stdin=io.StringIO(vid + "\n"), stdout=io.StringIO())


@pytest.mark.parametrize("name", ["agent", "Claude", "", "  "])
def test_approve_requires_human_name(db: sqlite3.Connection, name: str) -> None:
    vid = _scripted(db)
    with pytest.raises(queue.ApprovalRefusedError, match="human"):
        queue.approve(db, vid, name, stdin=FakeTTY(vid + "\n"), stdout=io.StringIO())


def test_approve_requires_matching_confirmation(db: sqlite3.Connection) -> None:
    vid = _scripted(db)
    with pytest.raises(queue.ApprovalRefusedError, match="confirmation"):
        queue.approve(db, vid, "Owner", stdin=FakeTTY("yes\n"), stdout=io.StringIO())
    v = dbm.get_video(db, vid)
    assert v is not None and v["state"] == "scripted"


def test_approve_happy_path(db: sqlite3.Connection) -> None:
    vid = _scripted(db)
    out = io.StringIO()
    v = queue.approve(db, vid, "Owner", stdin=FakeTTY(vid + "\n"), stdout=out)
    assert v["state"] == "reviewed" and v["approved_by"] == "Owner"
    assert "Type the video id" in out.getvalue()


def test_review_sheet_renders_script() -> None:
    sheet = queue.render_review(
        {"id": "v-x", "channel_id": "atlas", "format": "short", "state": "scripted"},
        load_script(DEMO_SCRIPT),
    )
    assert "TITLES:" in sheet and "SOURCES (6)" in sheet and "AI DISCLOSURE: no" in sheet


def test_review_sheet_shows_formula_and_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    monkeypatch.setattr(queue, "REPO_ROOT", tmp_path)
    (tmp_path / "data" / "scripts").mkdir(parents=True)
    script_file = tmp_path / "data" / "scripts" / "v-p.yaml"
    script_file.write_text("x", encoding="utf-8")
    preview = tmp_path / "output" / "dev" / "v-p" / "v-p.mp4"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(b"")
    video = {
        "id": "v-p",
        "channel_id": "atlas",
        "format": "short",
        "state": "scripted",
        "arm_id": "map-oddities|short|statement|s45|maps-heavy",
        "script_path": "data/scripts/v-p.yaml",
    }
    os.utime(script_file, (1, 1))
    sheet = queue.render_review(video, load_script(DEMO_SCRIPT))
    assert "FORMULA (bandit arm): map-oddities|short|statement|s45|maps-heavy" in sheet
    assert "PREVIEW: output/dev/v-p/v-p.mp4" in sheet and "re-render" not in sheet
    os.utime(script_file, None)
    os.utime(preview, (1, 1))
    assert "re-render before judging" in queue.render_review(video, load_script(DEMO_SCRIPT))


def test_queue_cli_list_and_show(tmp_path: Path) -> None:
    p = tmp_path / "q.sqlite"
    conn = dbm.connect(p)
    vid = _scripted(conn)
    conn.close()
    assert queue.main(["--db", str(p), "list"]) == 0
    assert queue.main(["--db", str(p), "show", vid]) == 0
    assert queue.main(["--db", str(p), "reject", vid, "--reason", "off-brand"]) == 0


# --- crosspost ------------------------------------------------------------------------------


@pytest.mark.parametrize(("t", "s"), [(0.0, "00:00:00,000"), (61.5, "00:01:01,500"), (3725.042, "01:02:05,042")])
def test_srt_timestamp(t: float, s: str) -> None:
    assert srt_timestamp(t) == s


def test_build_srt_cues() -> None:
    words = [
        CaptionWord(w=w, start=i * 0.4, end=i * 0.4 + 0.3)
        for i, w in enumerate("Maps lie. Greenland is not huge.".split())
    ]
    srt = build_srt(words)
    assert srt.startswith("1\n00:00:00,000 --> ")
    assert "Maps lie" in srt and "Greenland is not huge" in srt


def test_caption_has_single_disclosure() -> None:
    text = caption_text(load_script(DEMO_SCRIPT), PLATFORMS["reels"])
    assert text.lower().count("synthetic voice") == 1
    assert len(text) <= PLATFORMS["reels"].max_caption_chars
