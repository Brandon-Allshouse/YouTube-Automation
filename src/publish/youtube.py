"""YouTube upload (owner-run, ``--live`` only, private by default).

Gates enforced in code before any network call:
1. ``--live`` must be passed; otherwise this prints the exact metadata it *would* send (dry run).
2. The video must be in state ``scheduled`` with a human ``approved_by`` (``src.db`` enforces that
   ``scheduled`` itself requires approval).
3. Default privacy is ``private`` with ``publishAt`` = the scheduled slot, so YouTube publishes at
   the jittered time; ``public`` requires ``--privacy public`` *and* ``--confirm-public``.
4. Metadata: honest title (variant chosen by the bandit or the first), description with sources,
   ``selfDeclaredMadeForKids = false``, and ``containsSyntheticMedia`` from the script's AI
   disclosure flag.
5. The upload account is the video's recorded channel (the script must match it), and a private
   ``publishAt`` slot that has already passed is refused with instructions to reschedule.
6. Long-form uploads also set the first legible thumbnail rendered by production (Shorts
   thumbnails are chosen in the YouTube app). A thumbnail failure never hides the finished upload.

Uses the resumable upload protocol (stdlib only). Costs 1,600 quota units per upload (+50 for a
custom thumbnail). Never run by agents; never run during the overnight build.

``python -m src.publish.youtube --video-id v-123 [--live] [--privacy private|unlisted]``
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src import db as dbm
from src.common.paths import OUTPUT_DIR, REPO_ROOT, ensure_within
from src.production.script import Script, load_script
from src.publish import oauth

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
THUMB_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={vid}"
CHUNK = 8 * 1024 * 1024
_YT_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_THUMB_ID = re.compile(r"thumb-[a-z]")
CATEGORY = {"atlas": "27", "neon": "28"}  # Education, Science & Technology


class PublishGateError(RuntimeError):
    """Raised when a video is not eligible for upload."""


def build_metadata(
    script: Script, video: dict[str, Any], *, privacy: str = "private", title_index: int = 0
) -> dict[str, Any]:
    """Request body for videos.insert."""
    if privacy not in ("private", "unlisted", "public"):
        raise ValueError("invalid privacy")
    title = script.titles[min(title_index, len(script.titles) - 1)][:100]
    sources = "\n".join(f"- {s.claim}: {s.url}" for s in script.sources)
    desc = f"{script.description.strip()}\n\nSources:\n{sources}" if sources else script.description.strip()
    status: dict[str, Any] = {
        "privacyStatus": privacy,
        "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": bool(script.ai_disclosure),
    }
    if privacy == "private" and video.get("scheduled_for"):
        status["publishAt"] = datetime.fromisoformat(video["scheduled_for"]).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {
        "snippet": {
            "title": title,
            "description": desc[:4900],
            "categoryId": CATEGORY.get(script.channel, "27"),
            "defaultLanguage": "en",
        },
        "status": status,
    }


def check_gate(video: dict[str, Any] | None) -> dict[str, Any]:
    """Raise unless the video is approved and scheduled."""
    if video is None:
        raise PublishGateError("unknown video")
    if not video.get("approved_by") or str(video["approved_by"]).lower() in dbm.AGENT_ACTORS:
        raise PublishGateError("not approved by a human")
    if video.get("state") != "scheduled":
        raise PublishGateError(f"state is {video.get('state')}, expected scheduled")
    if not video.get("output_path"):
        raise PublishGateError("no rendered output recorded")
    if not video.get("script_sha256") or not video.get("output_sha256"):
        raise PublishGateError("approval or quality-check fingerprint missing")
    return video


def verify_content(video: dict[str, Any], script_file: Path, mp4: Path) -> None:
    """The uploaded script/video must be byte-identical to what was approved and quality-checked."""
    if dbm.file_sha256(script_file) != video["script_sha256"]:
        raise PublishGateError("script changed since human approval")
    if dbm.file_sha256(mp4) != video["output_sha256"]:
        raise PublishGateError("video file changed since it passed quality checks")


def _upload(path: Path, meta: dict[str, Any], token: str) -> str:
    size = path.stat().st_size
    init = urllib.request.Request(
        UPLOAD_URL, data=json.dumps(meta).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
    )  # fmt: skip
    with urllib.request.urlopen(init, timeout=60) as resp:  # noqa: S310  # nosec B310
        location = resp.headers.get("Location")
    if not location or not location.startswith("https://www.googleapis.com/"):
        raise RuntimeError("upload session not created")
    with path.open("rb") as fh:
        offset = 0
        while offset < size:
            chunk = fh.read(CHUNK)
            end = offset + len(chunk) - 1
            req = urllib.request.Request(  # noqa: S310 - session URL validated as googleapis https above
                location,
                data=chunk,
                method="PUT",
                headers={"Content-Range": f"bytes {offset}-{end}/{size}", "Authorization": f"Bearer {token}"},
            )
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310  # nosec B310
                    body = json.loads(resp.read())
                    yt_id = body.get("id") if isinstance(body, dict) else None
                    # the id is stored and used in a URL: accept only YouTube's 11-char id format
                    if not isinstance(yt_id, str) or not _YT_ID.fullmatch(yt_id):
                        raise RuntimeError("upload response had no valid video id")
                    return yt_id
            except urllib.error.HTTPError as exc:
                if exc.code != 308:  # 308 Resume Incomplete = chunk accepted
                    raise RuntimeError(f"upload failed: HTTP {exc.code}") from None
            offset = end + 1
    raise RuntimeError("upload ended without a video id")


def publish(
    conn: sqlite3.Connection, video_id: str, *, live: bool, privacy: str = "private", confirm_public: bool = False
) -> dict[str, Any]:
    """Upload an approved, scheduled video (or dry-run)."""
    video = check_gate(dbm.get_video(conn, video_id))
    if privacy == "public" and not confirm_public:
        raise PublishGateError("public uploads need --confirm-public")
    script_file = ensure_within(REPO_ROOT / str(video["script_path"]), REPO_ROOT)
    mp4 = ensure_within(REPO_ROOT / str(video["output_path"]), OUTPUT_DIR / "videos")
    verify_content(video, script_file, mp4)
    script = load_script(script_file)
    if script.channel != video["channel_id"]:
        raise PublishGateError("script channel does not match the video's channel")
    check_slot(video, privacy)
    meta = build_metadata(script, video, privacy=privacy)
    thumb = pick_thumbnail(mp4.parent) if video["format"] == "long" else None
    if not live:
        return {"dry_run": True, "would_upload": mp4.name, "thumbnail": thumb.name if thumb else None, "metadata": meta}
    # the token is chosen by the video's channel record, never by text inside the script
    token = oauth.access_token(str(video["channel_id"]))
    yt_id = _upload(mp4, meta, token)
    with dbm.transaction(conn):
        conn.execute("UPDATE videos SET youtube_id = ? WHERE id = ?", (yt_id, video_id))
    dbm.transition(conn, video_id, "published", actor="owner", note=f"uploaded {privacy} as {yt_id}")
    thumb_note = None
    if thumb is not None:
        try:
            _set_thumbnail(yt_id, thumb, token)
            thumb_note = thumb.name
        except (RuntimeError, urllib.error.URLError) as exc:
            # the video is up; a thumbnail failure (e.g. channel not verified yet) must not hide that
            thumb_note = f"failed: {exc}"
    return {
        "uploaded": yt_id,
        "privacy": privacy,
        "publishAt": meta["status"].get("publishAt"),
        "thumbnail": thumb_note,
    }


def check_slot(video: dict[str, Any], privacy: str, *, now: datetime | None = None) -> None:
    """A private upload scheduled with ``publishAt`` must still be in the future (YouTube rejects past times).

    Raises:
        PublishGateError: The slot has passed (or is under 15 minutes away).
    """
    if privacy != "private" or not video.get("scheduled_for"):
        return
    slot = datetime.fromisoformat(str(video["scheduled_for"]))
    if slot - (now or datetime.now(UTC)) < timedelta(minutes=15):
        raise PublishGateError(
            f"scheduled slot {slot.isoformat()} has passed or is too close; pick a new one with "
            f"`bash scripts/task.sh timing --video-id {video['id']} --schedule`"
        )


def pick_thumbnail(video_dir: Path) -> Path | None:
    """The first thumbnail variant that passed the legibility check (see ``produce``), if any."""
    tdir = video_dir / "thumbnails"
    report = tdir / "thumbnails.json"
    if not report.is_file():
        return None
    try:
        results = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(results, dict):
        return None
    for name, rep in results.items():
        # names become filenames: only the ids production generates (thumb-a, thumb-b, ...)
        if not isinstance(name, str) or not _THUMB_ID.fullmatch(name) or not isinstance(rep, dict):
            continue
        png = ensure_within(tdir / f"{name}.png", OUTPUT_DIR / "videos")
        if rep.get("passed") is True and png.is_file() and png.stat().st_size <= 2 * 1024 * 1024:
            return png
    return None


def _set_thumbnail(yt_id: str, png: Path, token: str) -> None:
    req = urllib.request.Request(  # noqa: S310 - fixed https googleapis URL
        THUMB_URL.format(vid=urllib.parse.quote(yt_id, safe="")),
        data=png.read_bytes(),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "image/png"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310  # nosec B310
            resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"thumbnail upload failed: HTTP {exc.code}") from None


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (owner only)."""
    p = argparse.ArgumentParser(description="Upload an approved video to YouTube (owner only)")
    p.add_argument("--db", type=Path, default=None, help=argparse.SUPPRESS)
    p.add_argument("--video-id", required=True)
    p.add_argument("--live", action="store_true", help="actually upload (otherwise dry run)")
    p.add_argument("--privacy", choices=("private", "unlisted", "public"), default="private")
    p.add_argument("--confirm-public", action="store_true")
    a = p.parse_args(argv)
    conn = dbm.connect(a.db)
    try:
        print(
            json.dumps(
                publish(conn, a.video_id, live=a.live, privacy=a.privacy, confirm_public=a.confirm_public), indent=2
            )
        )
    except (PublishGateError, oauth.OAuthError) as exc:
        print(json.dumps({"refused": str(exc)}))
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
