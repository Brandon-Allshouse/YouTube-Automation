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

Uses the resumable upload protocol (stdlib only). Costs 1,600 quota units per upload (+50 for a
custom thumbnail). Never run by agents; never run during the overnight build.

``python -m src.publish.youtube --video-id v-123 [--live] [--privacy private|unlisted]``
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from src import db as dbm
from src.common.paths import OUTPUT_DIR, REPO_ROOT, ensure_within
from src.production.script import Script, load_script
from src.publish import oauth

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
THUMB_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={vid}"
CHUNK = 8 * 1024 * 1024
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
                    return str(body["id"])
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
    mp4 = ensure_within(Path(video["output_path"]), OUTPUT_DIR / "videos")
    verify_content(video, script_file, mp4)
    script = load_script(script_file)
    meta = build_metadata(script, video, privacy=privacy)
    if not live:
        return {"dry_run": True, "would_upload": mp4.name, "metadata": meta}
    yt_id = _upload(mp4, meta, oauth.access_token(script.channel))
    with dbm.transaction(conn):
        conn.execute("UPDATE videos SET youtube_id = ? WHERE id = ?", (yt_id, video_id))
    dbm.transition(conn, video_id, "published", actor="owner", note=f"uploaded {privacy} as {yt_id}")
    return {"uploaded": yt_id, "privacy": privacy, "publishAt": meta["status"].get("publishAt")}


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
