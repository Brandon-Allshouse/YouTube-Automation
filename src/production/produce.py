"""Production orchestrator: script → narration → scene spec → music + mix → render → review assets.

Usage::

    python -m src.production.produce --demo                 # demo Short → output/demo/
    python -m src.production.produce --script PATH --out output/dev
    python -m src.production.produce --video-id v-123 --script PATH   # production (gated)

Production mode enforces the human review gate in code: the video must already be in state
``reviewed`` (approved by a named human in the review queue) before any audio is generated, and
every stage advances the SQLite state machine so crashed runs resume safely.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from engine.build import build_from_spec
from engine.components.base import norm_word
from engine.core.render import render_video, write_manifest
from engine.schema.spec import validate_spec
from src import db as dbm
from src.audio import dsp
from src.audio.analyze import analyze, ffmpeg_loudness, review_image
from src.audio.captions import WordTiming
from src.audio.mixer import Narration, layout_narration, master, write_wav
from src.audio.music import generate
from src.audio.tts import TTS, Backend, split_sentences
from src.common.channels import load_channel
from src.common.log import get_logger, log_event, new_job_id
from src.common.paths import (
    CONFIG_DIR,
    DATA_DIR,
    OUTPUT_DIR,
    REPO_ROOT,
    ensure_dir,
    ensure_within,
    safe_join,
    validate_id,
)
from src.production.script import Script, load_script
from src.production.thumbnail import build_for_script
from src.quality.frames import contact_sheet, frame_at

log = get_logger("src.production")

DEMO_SCRIPT = CONFIG_DIR / "demo" / "mercator_short.yaml"
SCENE_LEAD_S = 0.12  # cut slightly before the first word of a segment
MIN_SCENE_S = 1.2


class GateError(RuntimeError):
    """Raised when production is attempted without human approval."""


def _segment_sentences(script: Script) -> list[list[str]]:
    return [split_sentences(seg.text) for seg in script.segments]


def scene_starts(nar: Narration, counts: list[int]) -> list[float]:
    """Global start time of each segment's scene, aligned to its first spoken sentence."""
    starts: list[float] = []
    idx = 0
    for i, n in enumerate(counts):
        first = nar.sentences[idx][1]
        s = 0.0 if i == 0 else max(first - SCENE_LEAD_S, starts[-1] + MIN_SCENE_S)
        starts.append(round(s, 3))
        idx += n
    return starts


def _words_in(words: list[WordTiming], t0: float, t1: float) -> list[WordTiming]:
    return [w for w in words if t0 - 0.2 <= w.start < t1]


def _find_word(words: list[WordTiming], target: str, after: float = -1.0) -> WordTiming | None:
    """First (or ``word#n``: n-th) spoken occurrence of ``target`` at or after ``after``."""
    word, _, nth = target.partition("#")
    want = int(nth) if nth.isdigit() and int(nth) > 0 else 1
    key = norm_word(word)
    seen = 0
    for w in words:
        if w.start >= after and norm_word(w.text) == key:
            seen += 1
            if seen == want:
                return w
    return None


def resolve_elements(
    elements: list[dict[str, Any]], words: list[WordTiming], scene_start: float
) -> list[dict[str, Any]]:
    """Turn word anchors (``at_word``, ``reveal_words``) into scene-relative times."""
    out: list[dict[str, Any]] = []
    for el in elements:
        e = dict(el)
        at = e.pop("at_word", None)
        if isinstance(at, str):
            w = _find_word(words, at)
            if w is not None:
                e["start"] = round(max(0.0, w.start - scene_start - 0.05), 3)
        until = e.pop("until_word", None)
        if isinstance(until, str):
            w = _find_word(words, until)
            if w is not None:
                e["end"] = round(max(w.start - scene_start + 0.05, e.get("start", 0.0) + 0.5), 3)
        count_word = e.pop("count_at_word", None)
        if isinstance(count_word, str):
            w = _find_word(words, count_word)
            if w is not None:
                e["count_at"] = round(max(0.0, w.start - scene_start - 0.15), 3)
        reveal = e.pop("reveal_words", None)
        if isinstance(reveal, list):
            times: list[float] = []
            after = scene_start
            for target in reveal:
                w = _find_word(words, str(target), after)
                t = (w.start - scene_start - 0.04) if w else (times[-1] + 0.8 if times else 0.3)
                times.append(round(max(t, times[-1] + 0.25 if times else 0.0), 3))
                if w:
                    after = w.start
            e["reveal_at"] = times
        out.append(e)
    return out


def build_spec(script: Script, nar: Narration, theme: str) -> dict[str, Any]:
    """Assemble the scene spec dict (validated by the caller)."""
    counts = [len(s) for s in _segment_sentences(script)]
    starts = scene_starts(nar, counts)
    scenes = []
    for i, seg in enumerate(script.segments):
        end = starts[i + 1] if i + 1 < len(starts) else nar.duration
        seg_words = _words_in(nar.words, starts[i], end)
        scenes.append(
            {
                "id": seg.id,
                "start": starts[i],
                "transition": seg.transition,
                "elements": resolve_elements(seg.visual, seg_words, starts[i]),
                "note": seg.tag,
            }
        )
    return {
        "version": 1,
        "id": script.id,
        "format": script.format,
        "theme": theme,
        "seed": script.seed,
        "fps": 30,
        "duration": nar.duration,
        "scenes": scenes,
        "captions": {
            "enabled": True,
            "words": [{"w": w.text, "start": w.start, "end": w.end, "emph": w.emph} for w in nar.words],
        },
    }


def produce(
    script: Script,
    out_dir: Path,
    *,
    backend: Backend | None = None,
    render: bool = True,
    scale: float = 1.0,
    workers: int | None = None,
    review_assets: bool = True,
    _gated: bool = False,
) -> dict[str, Any]:
    """Run the full pipeline for one script and write all outputs into ``out_dir``."""
    job = new_job_id("produce")
    t0 = time.perf_counter()
    channel = load_channel(script.channel)
    out_dir = ensure_within(out_dir, OUTPUT_DIR)
    # output/videos/ (what gets uploaded) is reserved for produce_gated
    if not _gated and (out_dir == OUTPUT_DIR / "videos" or (OUTPUT_DIR / "videos") in out_dir.parents):
        raise GateError("output/videos/ is reserved for approved, gated production")
    out_dir = ensure_dir(out_dir)
    stem = validate_id(script.id, kind="script id")

    sentences = TTS(backend).narrate(_segment_sentences(script), channel.voice, seed=script.seed)
    nar = layout_narration(sentences)
    t_tts = time.perf_counter()

    spec_dict = build_spec(script, nar, channel.theme)
    spec = validate_spec(spec_dict)
    spec_json = spec.model_dump_json()
    spec_path = safe_join(out_dir, f"{stem}.spec.json")
    spec_path.write_text(json.dumps(json.loads(spec_json), indent=1), encoding="utf-8")

    cues = tuple(s.start for s in spec.scenes[1:])
    music, arrangement = generate(channel.music.style, nar.duration, channel.music.seed + script.seed, cues=cues)
    mix, report = master(nar.track, music, music_rel_db=channel.music.music_rel_db)
    wav = write_wav(safe_join(out_dir, f"{stem}.wav"), mix)
    t_audio = time.perf_counter()

    result: dict[str, Any] = {
        "job_id": job,
        "script_id": script.id,
        "channel": channel.id,
        "theme": channel.theme,
        "seed": script.seed,
        "duration_s": nar.duration,
        "scenes": len(spec.scenes),
        "words": len(nar.words),
        "mix": asdict(report),
        "music": asdict(arrangement),
        "timings_s": {"tts": round(t_tts - t0, 2), "audio": round(t_audio - t_tts, 2)},
        "outputs": {"spec": spec_path.name, "audio": wav.name},
    }
    if render:
        mp4 = safe_join(out_dir, f"{stem}.mp4")
        info = render_video(build_from_spec, (spec_json, scale), mp4, audio_path=wav, workers=workers)
        result["render"] = info
        # thumbnails come from the approved script's concepts, so they're rendered with the video
        thumbs = build_for_script(script, channel.theme, out_dir / "thumbnails")
        result["thumbnails"] = {k: bool(v["passed"]) for k, v in thumbs.items()}
        if thumbs:
            result["outputs"]["thumbnails"] = "thumbnails/"
        result["outputs"]["video"] = mp4.name
        result["ffmpeg_loudness"] = ffmpeg_loudness(mp4)
        if review_assets:
            rdir = ensure_dir(out_dir / f"{stem}_review")
            contact_sheet(mp4, rdir / "contact.png", duration=nar.duration, every_s=1.0, cols=10, tile_w=216)
            for name, t in [("first", 0.0), ("hook_1s", 1.0)] + [(s.id, s.start + 0.9) for s in spec.scenes[1:]]:
                frame_at(mp4, min(t, nar.duration - 0.1), rdir / f"key_{name}.png", width=540)
            review_image(mix, dsp.SR, rdir / "audio.png", title=f"{stem}: master", marks=list(cues))
            result["audio_analysis"] = analyze(mix, dsp.SR)
    result["timings_s"]["total"] = round(time.perf_counter() - t0, 2)
    write_manifest(
        out_dir,
        stem,
        {"result": result, "spec": json.loads(spec_json), "script": script.model_dump()},
    )
    log_event(
        log,
        "produce complete",
        **{k: v for k, v in result.items() if k in ("script_id", "duration_s", "timings_s")},
    )
    return result


def produce_gated(video_id: str, *, scale: float = 1.0, backend: Backend | None = None) -> dict[str, Any]:
    """Production entry: renders exactly the human-approved script and advances the state machine.

    The script comes from the video's recorded ``script_path`` (never a caller-supplied path) and
    its SHA-256 must match the hash stored at approval, so a script edited after approval is refused.
    """
    conn = dbm.connect()
    try:
        video = dbm.get_video(conn, video_id)
        if video is None:
            raise GateError("unknown video id")
        if video["state"] not in ("reviewed", "voiced", "specd", "rendered") or not video["approved_by"]:
            raise GateError("human review gate: script must be approved before production")
        if not video.get("script_path") or not video.get("script_sha256"):
            raise GateError("approved script is not recorded; re-approve via the review queue")
        script_file = ensure_within(REPO_ROOT / str(video["script_path"]), DATA_DIR / "scripts")
        if dbm.file_sha256(script_file) != video["script_sha256"]:
            raise GateError("script changed since approval; it must be re-approved")
        try:
            dbm.check_script_matches(video, script_file)
        except ValueError as exc:
            raise GateError(str(exc)) from None
        script = load_script(script_file)
        out_dir = OUTPUT_DIR / "videos" / validate_id(video_id, kind="video id")
        result = produce(script, out_dir, scale=scale, backend=backend, _gated=True)
        # walk the state machine back to "voiced" (re-runs) and forward to "rendered"
        cur = video["state"]
        for back in (("rendered", "specd"), ("specd", "voiced")):
            if cur == back[0]:
                dbm.transition(conn, video_id, back[1], actor="pipeline", note="re-run")
                cur = back[1]
        for state in ("voiced", "specd", "rendered"):
            if cur != state:
                dbm.transition(conn, video_id, state, actor="pipeline", note=result["job_id"])
            cur = state
        # repo-relative, like script_path, so the record is valid inside and outside containers
        mp4 = out_dir / result["outputs"].get("video", "")
        dbm.set_path(conn, video_id, "output_path", mp4.relative_to(REPO_ROOT).as_posix())
        with dbm.transaction(conn):
            conn.execute(
                "UPDATE videos SET output_sha256 = NULL WHERE id = ?", (video_id,)
            )  # set only by quality checks
        return result
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    p = argparse.ArgumentParser(description="Produce a video from a script")
    p.add_argument("--demo", action="store_true", help="render the demo Short to output/demo/")
    p.add_argument("--script", type=Path)
    p.add_argument("--out", type=Path, default=OUTPUT_DIR / "dev")
    p.add_argument("--video-id")
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--no-render", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="validate only: synthetic TTS, no render")
    p.add_argument("--workers", type=int)
    a = p.parse_args(argv)
    if a.demo:
        script = load_script(a.script or DEMO_SCRIPT)
        res = produce(script, OUTPUT_DIR / "demo", render=not a.no_render, scale=a.scale, workers=a.workers)
    elif a.video_id:
        if a.script:
            p.error("--video-id renders the approved script recorded in the DB; do not pass --script")
        res = produce_gated(a.video_id, scale=a.scale)
    elif a.script and a.dry_run:
        from src.audio.tts import SyntheticBackend

        res = produce(load_script(a.script), OUTPUT_DIR / "dry-run", backend=SyntheticBackend(), render=False)
    elif a.script:
        out = ensure_within(a.out, OUTPUT_DIR / "dev")  # ungated renders never touch output/videos/
        res = produce(load_script(a.script), out, render=not a.no_render, scale=a.scale, workers=a.workers)
    else:
        p.error("one of --demo, --script or --video-id is required")
    print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
