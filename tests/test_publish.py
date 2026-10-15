"""Tests for the timing optimizer, the publish gates (dry run only) and OAuth safety checks."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src import db as dbm
from src.common.paths import REPO_ROOT
from src.production.produce import DEMO_SCRIPT
from src.production.script import load_script
from src.publish import oauth, timing, youtube

NOW = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)


def _video(db: sqlite3.Connection, ch: str = "atlas", fmt: str = "short", approve: bool = True) -> str:
    vid = dbm.add_video(db, ch, fmt, "t")
    for st in ("scripted", "reviewed", "voiced", "specd", "rendered", "checked"):
        if st == "reviewed":
            if not approve:
                return vid
            dbm.transition(db, vid, st, actor="owner", approved_by="owner")
        else:
            dbm.transition(db, vid, st)
    return vid


def _schedule(db: sqlite3.Connection, vid: str, slot: datetime) -> None:
    db.execute("UPDATE videos SET scheduled_for = ? WHERE id = ?", (slot.isoformat(), vid))
    db.commit()
    dbm.transition(db, vid, "scheduled")


def test_slot_respects_lead_time_and_jitter(db: sqlite3.Connection) -> None:
    vid = _video(db)
    slot = timing.propose_slot(db, "atlas", "short", vid, now=NOW)
    assert slot >= NOW + timedelta(hours=2)
    assert slot.minute != 0 or slot.second != 0
    local = slot.astimezone(ZoneInfo("America/New_York"))
    assert any(abs((local.hour * 60 + local.minute) - h * 60) <= 47 for h in (11, 15, 19))


def test_one_short_per_day_per_channel(db: sqlite3.Connection) -> None:
    a, b = _video(db), _video(db)
    s1 = timing.propose_slot(db, "atlas", "short", a, now=NOW)
    _schedule(db, a, s1)
    s2 = timing.propose_slot(db, "atlas", "short", b, now=NOW)
    tz = ZoneInfo("America/New_York")
    assert s2.astimezone(tz).date() != s1.astimezone(tz).date()


def test_portfolio_gap_between_channels(db: sqlite3.Connection) -> None:
    a, b = _video(db, "atlas"), _video(db, "neon")
    s1 = timing.propose_slot(db, "atlas", "short", a, now=NOW)
    _schedule(db, a, s1)
    s2 = timing.propose_slot(db, "neon", "short", b, now=NOW)
    assert abs(s2 - s1) >= timing.MIN_GAP_PORTFOLIO


def test_long_form_weekly_cap(db: sqlite3.Connection) -> None:
    slots = []
    for _ in range(4):
        v = _video(db, "atlas", "long")
        s = timing.propose_slot(db, "atlas", "long", v, now=NOW)
        _schedule(db, v, s)
        slots.append(s)
    slots.sort()
    tz = ZoneInfo("America/New_York")
    assert len({s.astimezone(tz).date() for s in slots}) == len(slots)  # never two long-form on one day
    for s in slots:
        rolling = [x for x in slots if s <= x < s + timedelta(days=7)]
        assert len(rolling) <= 2  # atlas long_per_week = 2 in any rolling 7-day window
    assert slots[-1] - slots[0] > timedelta(days=3)


def test_slots_deterministic(db: sqlite3.Connection) -> None:
    vid = _video(db)
    assert timing.propose_slot(db, "atlas", "short", vid, now=NOW) == timing.propose_slot(
        db, "atlas", "short", vid, now=NOW
    )


def test_dst_handled() -> None:
    tz = ZoneInfo("America/New_York")
    assert datetime(2026, 7, 1, 12, tzinfo=tz).utcoffset() != datetime(2026, 1, 1, 12, tzinfo=tz).utcoffset()


# --- publish gates --------------------------------------------------------------------------


def test_gate_refuses_unapproved_and_unscheduled(db: sqlite3.Connection) -> None:
    with pytest.raises(youtube.PublishGateError, match="unknown"):
        youtube.check_gate(None)
    vid = _video(db, approve=False)
    with pytest.raises(youtube.PublishGateError, match="human"):
        youtube.check_gate(dbm.get_video(db, vid))
    vid2 = _video(db)
    with pytest.raises(youtube.PublishGateError, match="expected scheduled"):
        youtube.check_gate(dbm.get_video(db, vid2))


def test_metadata_defaults_private_with_publish_at() -> None:
    script = load_script(DEMO_SCRIPT)
    meta = youtube.build_metadata(script, {"scheduled_for": "2026-10-16T15:07:31+00:00"})
    st = meta["status"]
    assert st["privacyStatus"] == "private"
    assert st["publishAt"] == "2026-10-16T15:07:31.000Z"
    assert st["selfDeclaredMadeForKids"] is False
    assert st["containsSyntheticMedia"] is False
    assert "Sources:" in meta["snippet"]["description"]
    assert len(meta["snippet"]["title"]) <= 100


def test_dry_run_and_public_confirmation(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    vid = _video(db)
    _schedule(db, vid, datetime.now(UTC) + timedelta(days=2))  # publishAt must be in the future
    dbm.set_path(db, vid, "script_path", "config/demo/mercator_short.yaml")
    mp4 = REPO_ROOT / "output" / "videos" / "test-publish" / "x.mp4"
    mp4.parent.mkdir(parents=True, exist_ok=True)
    mp4.write_bytes(b"not really a video")
    dbm.set_path(db, vid, "output_path", str(mp4))
    db.execute(
        "UPDATE videos SET script_sha256 = ?, output_sha256 = ? WHERE id = ?",
        (dbm.file_sha256(REPO_ROOT / "config/demo/mercator_short.yaml"), dbm.file_sha256(mp4), vid),
    )
    db.commit()
    res = youtube.publish(db, vid, live=False)
    assert res["dry_run"] is True and res["metadata"]["status"]["privacyStatus"] == "private"
    with pytest.raises(youtube.PublishGateError, match="confirm-public"):
        youtube.publish(db, vid, live=False, privacy="public")

    def boom(*a: object, **k: object) -> str:
        raise AssertionError("network must not be touched in tests")

    monkeypatch.setattr(youtube, "_upload", boom)
    youtube.publish(db, vid, live=False)  # dry run never calls _upload


# --- oauth ----------------------------------------------------------------------------------


def test_secrets_dir_must_be_outside_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VP_SECRETS_DIR", str(REPO_ROOT / "data"))
    with pytest.raises(oauth.OAuthError, match="outside the repository"):
        oauth.secrets_dir()


def test_authorize_rejects_state_mismatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = tmp_path / "client.json"
    client.write_text(json.dumps({"installed": {"client_id": "id", "client_secret": "s"}}), encoding="utf-8")
    monkeypatch.setenv("YOUTUBE_OAUTH_CLIENT_SECRETS", str(client))
    monkeypatch.setenv("VP_SECRETS_DIR", str(tmp_path / "secrets"))
    with pytest.raises(oauth.OAuthError, match="state mismatch"):
        oauth.authorize("atlas", input_fn=lambda _: "http://127.0.0.1:8765/?code=abc&state=forged")


def test_access_token_requires_authorization(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VP_SECRETS_DIR", str(tmp_path / "secrets"))
    with pytest.raises(oauth.OAuthError, match="not authorized"):
        oauth.access_token("atlas")


def test_scopes_are_least_privilege() -> None:
    assert "https://www.googleapis.com/auth/youtube" not in oauth.SCOPES
    assert all(s.endswith(("upload", "readonly")) for s in oauth.SCOPES)


# --- gate hardening from the independent security review ---------------------------------------


def test_publish_refuses_swapped_video(db: sqlite3.Connection) -> None:
    vid = _video(db)
    _schedule(db, vid, NOW + timedelta(days=1))
    dbm.set_path(db, vid, "script_path", "config/demo/mercator_short.yaml")
    mp4 = REPO_ROOT / "output" / "videos" / "test-swap" / "x.mp4"
    mp4.parent.mkdir(parents=True, exist_ok=True)
    mp4.write_bytes(b"approved render")
    dbm.set_path(db, vid, "output_path", str(mp4))
    db.execute("UPDATE videos SET script_sha256 = ?, output_sha256 = ? WHERE id = ?",
               (dbm.file_sha256(REPO_ROOT / "config/demo/mercator_short.yaml"), dbm.file_sha256(mp4), vid))  # fmt: skip
    db.commit()
    mp4.write_bytes(b"swapped after quality checks")
    with pytest.raises(youtube.PublishGateError, match="changed since it passed"):
        youtube.publish(db, vid, live=False)


def test_publish_requires_fingerprints(db: sqlite3.Connection) -> None:
    vid = _video(db)
    _schedule(db, vid, NOW + timedelta(days=1))
    dbm.set_path(db, vid, "output_path", str(REPO_ROOT / "output" / "videos" / "x.mp4"))
    with pytest.raises(youtube.PublishGateError, match="fingerprint"):
        youtube.check_gate(dbm.get_video(db, vid))


# --- operational fixes (end-to-end run, 2026-10-15) --------------------------------------------


def test_publish_refuses_past_slot_with_guidance() -> None:
    video = {"id": "v-1", "scheduled_for": "2026-10-15T08:00:00+00:00"}
    with pytest.raises(youtube.PublishGateError, match="timing --video-id v-1 --schedule"):
        youtube.check_slot(video, "private", now=datetime(2026, 10, 15, 7, 50, tzinfo=UTC))
    youtube.check_slot(video, "private", now=datetime(2026, 10, 15, 7, 0, tzinfo=UTC))
    youtube.check_slot(video, "unlisted", now=datetime(2026, 10, 16, tzinfo=UTC))  # no publishAt involved


def test_pick_thumbnail_takes_first_legible_variant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(youtube, "OUTPUT_DIR", tmp_path)
    vdir = tmp_path / "videos" / "v-1"
    tdir = vdir / "thumbnails"
    tdir.mkdir(parents=True)
    assert youtube.pick_thumbnail(vdir) is None  # nothing rendered
    for name in ("thumb-a", "thumb-b"):
        (tdir / f"{name}.png").write_bytes(b"png")
    (tdir / "thumbnails.json").write_text(
        json.dumps({"thumb-a": {"passed": False}, "thumb-b": {"passed": True}}), encoding="utf-8"
    )
    picked = youtube.pick_thumbnail(vdir)
    assert picked is not None and picked.name == "thumb-b.png"


def test_publish_refuses_script_for_another_channel(db: sqlite3.Connection) -> None:
    vid = _video(db, ch="neon")  # the demo script belongs to atlas
    _schedule(db, vid, datetime.now(UTC) + timedelta(days=2))
    dbm.set_path(db, vid, "script_path", "config/demo/mercator_short.yaml")
    mp4 = REPO_ROOT / "output" / "videos" / "test-channel" / "x.mp4"
    mp4.parent.mkdir(parents=True, exist_ok=True)
    mp4.write_bytes(b"render")
    dbm.set_path(db, vid, "output_path", mp4.relative_to(REPO_ROOT).as_posix())
    db.execute("UPDATE videos SET script_sha256 = ?, output_sha256 = ? WHERE id = ?",
               (dbm.file_sha256(REPO_ROOT / "config/demo/mercator_short.yaml"), dbm.file_sha256(mp4), vid))  # fmt: skip
    db.commit()
    with pytest.raises(youtube.PublishGateError, match="channel"):
        youtube.publish(db, vid, live=False)


def test_rescheduling_ignores_the_videos_own_old_slot(db: sqlite3.Connection) -> None:
    vid = _video(db)
    _schedule(db, vid, NOW + timedelta(hours=5))
    assert [t for _, _, t in timing._scheduled(db)] == [NOW + timedelta(hours=5)]
    assert timing._scheduled(db, exclude=vid) == []
    # with its own slot ignored, the proposal is what an empty schedule would give
    empty = dbm.connect(":memory:")  # type: ignore[arg-type]
    try:
        assert timing.propose_slot(db, "atlas", "short", vid, now=NOW) == timing.propose_slot(
            empty, "atlas", "short", vid, now=NOW
        )
    finally:
        empty.close()


def test_pick_thumbnail_ignores_untrusted_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(youtube, "OUTPUT_DIR", tmp_path)
    vdir = tmp_path / "videos" / "v-1"
    (vdir / "thumbnails").mkdir(parents=True)
    (tmp_path / "videos" / "evil.png").write_bytes(b"png")
    report = {"../evil": {"passed": True}, "thumb-a": "not a dict", "thumb-b": {"passed": "yes"}}
    (vdir / "thumbnails" / "thumbnails.json").write_text(json.dumps(report), encoding="utf-8")
    assert youtube.pick_thumbnail(vdir) is None
    (vdir / "thumbnails" / "thumbnails.json").write_text("[1, 2]", encoding="utf-8")
    assert youtube.pick_thumbnail(vdir) is None


def test_youtube_id_format() -> None:
    assert youtube._YT_ID.fullmatch("dQw4w9WgXcQ")
    assert not youtube._YT_ID.fullmatch("../../etc/x")
    assert not youtube._YT_ID.fullmatch("dQw4w9WgXcQ&x=1")
