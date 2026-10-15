"""Regression tests for gate bypasses reported by the independent security review."""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest

from src import db as dbm
from src.audio.tts import SyntheticBackend
from src.production import produce as prod
from src.production.produce import DEMO_SCRIPT
from src.production.script import load_script
from src.review import queue

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "data" / "scripts"


class FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def _approved(db: sqlite3.Connection, name: str) -> tuple[str, Path]:
    vid = dbm.add_video(db, "atlas", "short", "t")
    SCRIPTS.mkdir(parents=True, exist_ok=True)
    path = SCRIPTS / f"{name}-{vid}.yaml"
    path.write_text(DEMO_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    dbm.set_path(db, vid, "script_path", str(path.relative_to(ROOT)))
    dbm.transition(db, vid, "scripted")
    queue.approve(db, vid, "Owner", stdin=FakeTTY(vid + "\n"), stdout=io.StringIO())
    return vid, path


def test_production_refuses_script_edited_after_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "g.sqlite"
    conn = dbm.connect(db_path)
    vid, path = _approved(conn, "edited")
    conn.close()
    path.write_text(path.read_text(encoding="utf-8") + "\n# sneaky edit\n", encoding="utf-8")
    monkeypatch.setattr(dbm, "DEFAULT_DB", db_path)
    with pytest.raises(prod.GateError, match="changed since approval"):
        prod.produce_gated(vid, backend=SyntheticBackend())


def test_gated_production_uses_recorded_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "g2.sqlite"
    conn = dbm.connect(db_path)
    vid, _ = _approved(conn, "gated")
    conn.close()
    monkeypatch.setattr(dbm, "DEFAULT_DB", db_path)
    monkeypatch.setattr(prod, "render_video", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no render in tests")))
    with pytest.raises(RuntimeError, match="no render"):
        prod.produce_gated(vid, backend=SyntheticBackend())  # got past the gate with the approved script


def test_set_script_refused_after_approval(tmp_path: Path) -> None:
    db_path = tmp_path / "s.sqlite"
    conn = dbm.connect(db_path)
    vid, path = _approved(conn, "setscript")
    conn.close()
    assert dbm.main(["--db", str(db_path), "set-script", vid, str(path)]) == 2


def test_failed_cannot_skip_review(db: sqlite3.Connection) -> None:
    vid, _ = _approved(db, "failed")
    dbm.transition(db, vid, "failed")
    with pytest.raises(dbm.TransitionError):
        dbm.transition(db, vid, "voiced")
    v = dbm.transition(db, vid, "scripted")
    assert v["approved_by"] is None and v["script_sha256"] is None


@pytest.mark.parametrize("state", ["reviewed", "checked", "scheduled", "published", "voiced"])
def test_agent_db_cli_cannot_set_gated_states(tmp_path: Path, state: str) -> None:
    with pytest.raises(SystemExit):
        dbm.main(["--db", str(tmp_path / "c.sqlite"), "transition", "v-x", state])


def test_ungated_produce_cannot_write_upload_dir() -> None:
    with pytest.raises(prod.GateError, match="reserved"):
        prod.produce(
            load_script(DEMO_SCRIPT), prod.OUTPUT_DIR / "videos" / "v-evil", backend=SyntheticBackend(), render=False
        )


def test_mark_checked_requires_rendered_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.quality import run

    db_path = tmp_path / "m.sqlite"
    conn = dbm.connect(db_path)
    vid = dbm.add_video(conn, "atlas", "short", "t")
    conn.close()
    monkeypatch.setattr(dbm, "DEFAULT_DB", db_path)
    with pytest.raises(SystemExit, match="refused"):
        run.mark_checked(vid, ROOT / "output" / "x.mp4")
