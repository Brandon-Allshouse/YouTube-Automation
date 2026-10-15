"""Human review queue (CLI).

The owner reviews every script before production (CLAUDE.md constraint 1). Approval is human-only
**in code**: ``approve`` requires an interactive terminal, the reviewer's name, and typing the
video id back to confirm; headless/agent sessions cannot satisfy that, and ``src.db.transition``
independently refuses agent actors. Agents may only ``list`` and ``show``.

Commands::

    python -m src.review.queue list
    python -m src.review.queue show <video_id>
    python -m src.review.queue approve <video_id> --as <your name>
    python -m src.review.queue request-changes <video_id> --note "..."
    python -m src.review.queue reject <video_id> --reason "..."
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from src import db as dbm
from src.common.paths import DATA_DIR, REPO_ROOT, ensure_within
from src.learning.arms import estimate_duration, spoken_words
from src.production.script import Script, load_script


class ApprovalRefusedError(RuntimeError):
    """Raised when approval conditions (interactive human confirmation) are not met."""


def _script_for(video: dict[str, Any]) -> Script | None:
    path = video.get("script_path")
    if not path:
        return None
    return load_script(ensure_within(REPO_ROOT / path, DATA_DIR / "scripts"))


def render_review(video: dict[str, Any], script: Script | None) -> str:
    """Plain-text review sheet: titles, thumbnails, narration by segment, sources, checks."""
    lines = [
        f"VIDEO {video['id']}  channel={video['channel_id']}  format={video['format']}  state={video['state']}",
        "",
    ]
    if script is None:
        lines.append("(no script attached; attach with: python -m src.db set-script <video_id> <path>)")
        return "\n".join(lines)
    lines += ["TITLES:"] + [f"  {i + 1}. {t}" for i, t in enumerate(script.titles)]
    lines += ["THUMBNAILS:"] + [f"  {i + 1}. [{t.text}] {t.visual}" for i, t in enumerate(script.thumbnails)]
    lines += ["", "SCRIPT:"]
    for seg in script.segments:
        lines.append(f"  [{seg.id} | {seg.tag}] {seg.text}")
    lines += ["", f"SOURCES ({len(script.sources)}):"] + [f"  - {s.claim}  <{s.url}>" for s in script.sources]
    lines.append(f"AI DISCLOSURE: {'yes' if script.ai_disclosure else 'no'}")
    fc = DATA_DIR / "factchecks" / f"{script.id}.md"
    lines.append(f"FACT-CHECK REPORT: {fc.relative_to(REPO_ROOT) if fc.is_file() else 'missing'}")
    words = sum(spoken_words(seg.text) for seg in script.segments)
    lines.append(f"LENGTH: {words} spoken words ≈ {estimate_duration(script):.0f} s narrated")
    if video.get("arm_id"):
        lines.append(f"FORMULA (bandit arm): {video['arm_id']}")
    preview = REPO_ROOT / "output" / "dev" / str(video["id"]) / f"{video['id']}.mp4"
    if preview.is_file():
        script_file = REPO_ROOT / str(video["script_path"])
        stale = script_file.is_file() and script_file.stat().st_mtime > preview.stat().st_mtime
        note = "  (older than the script: re-render before judging)" if stale else ""
        lines.append(f"PREVIEW: {preview.relative_to(REPO_ROOT).as_posix()}{note}")
    return "\n".join(lines)


def approve(
    conn: Any,
    video_id: str,
    reviewer: str,
    *,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
) -> dict[str, Any]:
    """Approve after interactive confirmation.

    Raises:
        ApprovalRefusedError: not a TTY, agent-like reviewer name, or confirmation mismatch.
    """
    if not stdin.isatty():
        raise ApprovalRefusedError("approval requires an interactive terminal (human reviewer)")
    name = reviewer.strip()
    if not name or name.lower() in dbm.AGENT_ACTORS:
        raise ApprovalRefusedError("a named human reviewer is required")
    video = dbm.get_video(conn, video_id)
    if video is None:
        raise ApprovalRefusedError("unknown video")
    stdout.write(render_review(video, _script_for(video)) + "\n\n")
    if not video.get("script_path"):
        raise ApprovalRefusedError("no script attached to this video")
    script_file = ensure_within(REPO_ROOT / str(video["script_path"]), DATA_DIR / "scripts")
    digest = dbm.file_sha256(script_file)
    stdout.write(f"Type the video id ({video_id}) to approve it for production: ")
    stdout.flush()
    typed = stdin.readline().strip()
    if typed != video_id:
        raise ApprovalRefusedError("confirmation did not match; not approved")
    if dbm.file_sha256(script_file) != digest:
        raise ApprovalRefusedError("script changed while you were reviewing it; not approved")
    approved = dbm.transition(
        conn, video_id, "reviewed", actor=name, approved_by=name, note=f"approved script sha256 {digest[:12]}"
    )
    with dbm.transaction(conn):
        conn.execute("UPDATE videos SET script_sha256 = ? WHERE id = ?", (digest, video_id))
    approved["script_sha256"] = digest
    return approved


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    p = argparse.ArgumentParser(description="Human review queue")
    p.add_argument("--db", type=Path, default=None, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sh = sub.add_parser("show")
    sh.add_argument("video_id")
    ap = sub.add_parser("approve")
    ap.add_argument("video_id")
    ap.add_argument("--as", dest="reviewer", required=True)
    rc = sub.add_parser("request-changes")
    rc.add_argument("video_id")
    rc.add_argument("--note", required=True)
    rj = sub.add_parser("reject")
    rj.add_argument("video_id")
    rj.add_argument("--reason", required=True)
    a = p.parse_args(argv)
    conn = dbm.connect(a.db)
    try:
        if a.cmd == "list":
            rows = dbm.list_videos(conn, state="scripted")
            print(
                json.dumps(
                    [
                        {
                            k: r[k]
                            for k in (
                                "id",
                                "channel_id",
                                "format",
                                "title",
                                "script_path",
                                "updated_at",
                            )
                        }
                        for r in rows
                    ],
                    indent=2,
                )
            )
        elif a.cmd == "show":
            video = dbm.get_video(conn, a.video_id)
            if video is None:
                print("unknown video", file=sys.stderr)
                return 2
            print(render_review(video, _script_for(video)))
        elif a.cmd == "approve":
            v = approve(conn, a.video_id, a.reviewer)
            print(f"\nApproved by {v['approved_by']} at {v['approved_at']}. Next: /produce {a.video_id}")
        elif a.cmd == "request-changes":
            cur = dbm.get_video(conn, a.video_id)
            if cur is None:
                return 2
            if cur["state"] == "reviewed":
                dbm.transition(conn, a.video_id, "scripted", actor="owner", note=a.note[:500])
            else:
                with dbm.transaction(conn):
                    conn.execute(
                        "INSERT INTO video_events(video_id, from_state, to_state, actor, note, at) "
                        "VALUES (?, ?, ?, 'owner', ?, ?)",
                        (
                            a.video_id,
                            cur["state"],
                            cur["state"],
                            "changes requested: " + a.note[:480],
                            dbm.utcnow(),
                        ),
                    )
            print("changes requested")
        elif a.cmd == "reject":
            dbm.transition(conn, a.video_id, "rejected", actor="owner", note=a.reason[:500])
            print("rejected")
    except (ApprovalRefusedError, dbm.TransitionError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
