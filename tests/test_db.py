"""Tests for the SQLite state machine and the human review gate."""

from __future__ import annotations

import sqlite3

import pytest

from src import db as dbm


def _advance(conn: sqlite3.Connection, vid: str, *states: str) -> None:
    for s in states:
        if s == "reviewed":
            dbm.transition(conn, vid, s, actor="owner", approved_by="owner")
        else:
            dbm.transition(conn, vid, s)


def test_full_happy_path(db: sqlite3.Connection) -> None:
    vid = dbm.add_video(db, "atlas", "short", "Test", seed=7)
    _advance(db, vid, *dbm.STATES[1:])
    v = dbm.get_video(db, vid)
    assert v is not None and v["state"] == "published" and v["approved_by"] == "owner"
    events = db.execute("SELECT COUNT(*) FROM video_events WHERE video_id = ?", (vid,)).fetchone()[0]
    assert events == len(dbm.STATES)


def test_cannot_skip_states(db: sqlite3.Connection) -> None:
    vid = dbm.add_video(db, "atlas", "short", "Test")
    with pytest.raises(dbm.TransitionError):
        dbm.transition(db, vid, "rendered")


@pytest.mark.parametrize("approver", [None, "", "agent", "Claude", "cron"])
def test_review_gate_requires_human(db: sqlite3.Connection, approver: str | None) -> None:
    vid = dbm.add_video(db, "atlas", "short", "Test")
    dbm.transition(db, vid, "scripted")
    with pytest.raises(dbm.TransitionError, match="review gate"):
        dbm.transition(db, vid, "reviewed", approved_by=approver)


def test_agent_actor_cannot_approve(db: sqlite3.Connection) -> None:
    vid = dbm.add_video(db, "atlas", "short", "Test")
    dbm.transition(db, vid, "scripted")
    with pytest.raises(dbm.TransitionError):
        dbm.transition(db, vid, "reviewed", actor="agent", approved_by="owner")


def test_revision_clears_approval(db: sqlite3.Connection) -> None:
    vid = dbm.add_video(db, "atlas", "short", "Test")
    _advance(db, vid, "scripted", "reviewed")
    dbm.transition(db, vid, "scripted", note="changes requested")
    v = dbm.get_video(db, vid)
    assert v is not None and v["approved_by"] is None


def test_idempotent_transition(db: sqlite3.Connection) -> None:
    vid = dbm.add_video(db, "atlas", "short", "Test")
    dbm.transition(db, vid, "scripted")
    assert dbm.transition(db, vid, "scripted")["state"] == "scripted"


def test_sql_injection_is_inert(db: sqlite3.Connection) -> None:
    evil = "x'); DROP TABLE videos; --"
    vid = dbm.add_video(db, "atlas", "short", evil)
    v = dbm.get_video(db, vid)
    assert v is not None and v["title"] == evil
    assert dbm.list_videos(db, channel_id=evil) == []


def test_set_path_allowlist(db: sqlite3.Connection) -> None:
    vid = dbm.add_video(db, "atlas", "short", "Test")
    dbm.set_path(db, vid, "spec_path", "output/x.json")
    with pytest.raises(ValueError, match="unknown path column"):
        dbm.set_path(db, vid, "title; DROP TABLE videos", "x")


def test_quota_and_cache(db: sqlite3.Connection) -> None:
    dbm.record_quota(db, "search.list", 100)
    dbm.record_quota(db, "videos.list", 1)
    assert dbm.quota_used_today(db) == 101
    dbm.cache_put(db, "k", "videos.list", {"a": 1}, "2999-01-01T00:00:00+00:00")
    assert dbm.cache_get(db, "k") == {"a": 1}
    dbm.cache_put(db, "old", "videos.list", {"a": 1}, "2000-01-01T00:00:00+00:00")
    assert dbm.cache_get(db, "old") is None


def test_cli(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "t.sqlite"
    assert (
        dbm.main(
            [
                "--db",
                str(path),
                "add-video",
                "--channel",
                "atlas",
                "--format",
                "short",
                "--title",
                "T",
            ]
        )
        == 0
    )
    assert dbm.main(["--db", str(path), "list"]) == 0
