"""Run every automated quality check on a produced video and write a report.

``python -m src.quality.run --video output/demo/mercator-short.mp4 --spec output/demo/mercator-short.spec.json
   --script config/demo/mercator_short.yaml [--audio output/demo/mercator-short.wav]``

Writes ``<stem>_review/quality.json`` and ``quality.md`` next to the video; exit code 1 if any
check fails (so ``/produce`` can loop on fixes).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from engine.schema.spec import validate_spec
from src.audio.analyze import analyze, ffmpeg_loudness
from src.audio.mixer import read_wav
from src.common.config import rules
from src.common.paths import OUTPUT_DIR, REPO_ROOT, ensure_dir, ensure_within
from src.production.script import load_script
from src.quality import visual, writing


def audio_checks(wav: Path | None, video: Path) -> dict[str, Any]:
    """Loudness, true peak, clipping and silence gaps vs config/rules.yaml."""
    q = rules().quality
    out: dict[str, Any] = {"checks": {}, "metrics": {}}
    if wav is not None and wav.is_file():
        sr, audio = read_wav(wav)
        m = analyze(audio, sr)
        out["metrics"].update(m)
        out["checks"]["loudness_target"] = abs(m["integrated_lufs"] - q.target_lufs) <= q.lufs_tolerance
        out["checks"]["true_peak"] = m["true_peak_dbtp"] <= q.max_true_peak_dbtp
        out["checks"]["no_clipping"] = m["clipped_samples"] == 0
        out["checks"]["no_long_silence"] = not m["silence_gaps_gt_1_5s"]
    ff = ffmpeg_loudness(video)
    out["metrics"]["ffmpeg_ebur128"] = ff
    if "I" in ff:
        out["checks"]["delivered_loudness"] = abs(ff["I"] - q.target_lufs) <= q.lufs_tolerance
    if "TP" in ff:
        out["checks"]["delivered_true_peak"] = ff["TP"] <= q.max_true_peak_dbtp
    out["passed"] = all(out["checks"].values())
    return out


def run(video: Path, spec_path: Path, script_path: Path, wav: Path | None) -> dict[str, Any]:
    """All checks → report dict."""
    q = rules().quality
    spec_json = validate_spec(ensure_within(spec_path, REPO_ROOT).read_text(encoding="utf-8")).model_dump_json()
    duration = validate_spec(spec_json).duration
    vr = visual.scan_text(spec_json)
    st = visual.static_runs(video, duration)
    anim: dict[str, Any] = {
        "checks": {
            "text_in_safe_zone": not vr.safe_zone_violations,
            "no_offcanvas_text": not vr.offcanvas,
            "max_words_on_screen": not vr.word_violations,
            "contrast": not vr.contrast_failures,
            "no_static_over_4s": st["longest_static_s"] <= q.max_static_seconds,
        },
        "metrics": {
            "max_words_transient": vr.max_words_on_screen, "max_words_persistent": vr.max_words_persistent,
            "min_contrast_transient": round(vr.min_contrast, 2), "samples": vr.samples, **st,
        },
        "violations": {
            "safe_zone": vr.safe_zone_violations[:10], "offcanvas": vr.offcanvas[:10],
            "words": vr.word_violations[:10], "contrast": vr.contrast_failures[:10],
        },
    }  # fmt: skip
    anim["passed"] = all(bool(v) for v in anim["checks"].values())
    wr = writing.as_dict(writing.check_script(load_script(script_path)))
    au = audio_checks(wav, video)
    return {
        "video": video.name,
        "passed": anim["passed"] and wr["passed"] and au["passed"],
        "animation": anim,
        "audio": au,
        "writing": wr,
    }


def to_markdown(rep: dict[str, Any]) -> str:
    """Human-readable summary."""
    lines = [f"# Quality report: {rep['video']}", "", f"**Overall: {'PASS' if rep['passed'] else 'FAIL'}**", ""]
    for section in ("animation", "audio", "writing"):
        s = rep[section]
        lines += [f"## {section.title()}: {'PASS' if s['passed'] else 'FAIL'}", ""]
        lines += [f"- {'✅' if ok else '❌'} {name}" for name, ok in s["checks"].items()]
        lines += ["", "```json", json.dumps(s.get("metrics", {}), indent=1, default=str), "```", ""]
        if s.get("violations"):
            v = {k: x for k, x in s["violations"].items() if x}
            if v:
                lines += ["Violations (first 10 each):", "```json", json.dumps(v, indent=1), "```", ""]
        if s.get("notes"):
            lines += [f"- note: {n}" for n in s["notes"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    p = argparse.ArgumentParser(description="Automated quality checks")
    p.add_argument("--video", type=Path)
    p.add_argument("--spec", type=Path)
    p.add_argument("--script", type=Path)
    p.add_argument("--audio", type=Path)
    p.add_argument(
        "--video-id",
        help="check this video's recorded render, spec, audio and approved script (no other paths allowed); "
        "on PASS mark it 'checked' and record the render's sha256",
    )
    a = p.parse_args(argv)
    if a.video_id:
        if a.video or a.spec or a.script or a.audio:
            p.error("--video-id takes its files from the database; don't pass --video/--spec/--script/--audio")
        video, spec, script, wav = recorded_inputs(a.video_id)
    else:
        if not (a.video and a.spec and a.script):
            p.error("--video, --spec and --script are required without --video-id")
        video, spec, script = ensure_within(a.video, OUTPUT_DIR), a.spec, a.script
        wav = ensure_within(a.audio, OUTPUT_DIR) if a.audio else None
    from src.db import file_sha256

    digest = file_sha256(video)  # the exact bytes being judged
    rep = run(video, spec, script, wav)
    rdir = ensure_dir(video.parent / f"{video.stem}_review")
    (rdir / "quality.json").write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    (rdir / "quality.md").write_text(to_markdown(rep), encoding="utf-8")
    print(
        json.dumps(
            {
                "passed": rep["passed"],
                **{
                    k: {"passed": rep[k]["passed"], "checks": rep[k]["checks"]}
                    for k in ("animation", "audio", "writing")
                },
            },
            indent=1,
        )
    )
    if a.video_id and rep["passed"]:
        mark_checked(a.video_id, video, digest)
    return 0 if rep["passed"] else 1


def recorded_inputs(video_id: str) -> tuple[Path, Path, Path, Path | None]:
    """(video, spec, approved script, audio) for a rendered video, straight from the database.

    The gate must judge exactly what will be uploaded and the script the human approved, so none
    of these can come from the caller.

    Raises:
        SystemExit: Unknown/unrendered video, or the approved script changed since approval.
    """
    from src import db as dbm

    conn = dbm.connect()
    try:
        v = dbm.get_video(conn, video_id)
    finally:
        conn.close()
    if v is None or v["state"] != "rendered" or not v["output_path"] or not v["script_path"]:
        raise SystemExit("refused: video must be 'rendered' with a recorded output and approved script")
    video = ensure_within(REPO_ROOT / str(v["output_path"]), OUTPUT_DIR / "videos")
    script = ensure_within(REPO_ROOT / str(v["script_path"]), REPO_ROOT / "data" / "scripts")
    if not v["script_sha256"] or dbm.file_sha256(script) != v["script_sha256"]:
        raise SystemExit("refused: the approved script changed since approval")
    wav = video.with_suffix(".wav")
    return video, video.with_suffix(".spec.json"), script, wav if wav.is_file() else None


def mark_checked(video_id: str, video: Path, digest: str | None = None) -> None:
    """Only a passing quality run can move rendered → checked; it pins the exact file that passed.

    ``digest`` is the render's sha256 taken before the checks ran; a file changed meanwhile is refused.
    """
    from src import db as dbm

    conn = dbm.connect()
    try:
        v = dbm.get_video(conn, video_id)
        if v is None or v["state"] != "rendered" or (REPO_ROOT / str(v["output_path"])).resolve() != video.resolve():
            raise SystemExit("refused: video must be 'rendered' and the checked file must be its recorded output")
        now = dbm.file_sha256(video)
        if digest is not None and now != digest:
            raise SystemExit("refused: the render changed while it was being checked")
        dbm.transition(conn, video_id, "checked", actor="pipeline", note="automated quality checks passed")
        with dbm.transaction(conn):
            conn.execute("UPDATE videos SET output_sha256 = ? WHERE id = ?", (now, video_id))
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
