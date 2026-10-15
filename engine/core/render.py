"""Parallel frame rendering → FFmpeg.

Strategy: split the frame range into contiguous chunks; each worker process builds the
composition once, renders its chunk into a reused RGBA buffer and pipes raw frames straight into
its own FFmpeg encoder, producing an H.264 segment. Segments are then joined losslessly with the
concat demuxer and the audio is muxed in. This avoids shipping ~8 MB frames between processes and
scales with cores. Frames are deterministic for a given (spec, theme, seed).

Colour: frames are sRGB; we convert with an explicit BT.709 matrix and tag the stream BT.709
(FFmpeg's default RGB→YUV matrix is BT.601, which would shift hues on YouTube).
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess  # nosec B404
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.core.scene import Composition, FrameRenderer, RenderContext
from src.common import proc
from src.common.config import rules
from src.common.ffmpeg import ffmpeg_exe
from src.common.log import get_logger, log_event

log = get_logger("engine.render")

# A builder returns (composition, context). It must be a module-level callable (picklable by
# reference) so each worker can rebuild the composition from plain arguments.
Builder = Callable[..., tuple[Composition, RenderContext]]


@dataclass(frozen=True)
class EncodeSettings:
    """H.264 settings for YouTube-ready masters."""

    crf: int = 17
    preset: str = "medium"
    audio_bitrate: str = "320k"

    def __post_init__(self) -> None:
        if not 0 <= self.crf <= 51:
            raise ValueError("crf out of range")
        if self.preset not in {
            "ultrafast",
            "superfast",
            "veryfast",
            "faster",
            "fast",
            "medium",
            "slow",
            "slower",
        }:
            raise ValueError("invalid preset")
        if not self.audio_bitrate.endswith("k") or not self.audio_bitrate[:-1].isdigit():
            raise ValueError("invalid audio bitrate")


def _check_limits(width: int, height: int, fps: int, duration: float) -> None:
    lim = rules().render
    if not (16 <= width <= lim.max_width and 16 <= height <= lim.max_height):
        raise ValueError("resolution outside render limits")
    if not 1 <= fps <= lim.max_fps:
        raise ValueError("fps outside render limits")
    if not 0 < duration <= lim.max_duration_s:
        raise ValueError("duration outside render limits")
    if width % 2 or height % 2:
        raise ValueError("width/height must be even for yuv420p")


def _encoder_args(width: int, height: int, fps: int, out: Path, settings: EncodeSettings, threads: int) -> list[str]:
    return [
        ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "pipe:0",
        "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
        "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
        "-profile:v", "high", "-threads", str(threads),
        "-g", str(fps * 2), "-keyint_min", str(fps), "-bf", "2",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-color_range", "tv",
        "-an", str(out),
    ]  # fmt: skip


_WORKER: dict[str, Any] = {}


def _render_chunk(
    builder: Builder,
    args: tuple[Any, ...],
    start: int,
    stop: int,
    seg_path: str,
    settings: EncodeSettings,
    threads: int,
) -> tuple[int, float]:
    """Worker: render frames [start, stop) into one encoded segment. Returns (frames, seconds)."""
    key = repr((builder.__module__, builder.__qualname__, args))
    if _WORKER.get("key") != key:
        comp, ctx = builder(*args)
        _WORKER.update(key=key, fr=FrameRenderer(comp, ctx))
    fr: FrameRenderer = _WORKER["fr"]
    comp = fr.comp
    t0 = time.perf_counter()
    enc = proc.popen(
        _encoder_args(comp.width, comp.height, comp.fps, Path(seg_path), settings, threads),
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if enc.stdin is None:  # cannot happen with stdin=PIPE; explicit check survives python -O
        raise RuntimeError("encoder stdin unavailable")
    try:
        for f in range(start, stop):
            enc.stdin.write(fr.render(f).data)
        enc.stdin.close()
        rc = enc.wait(timeout=rules().render.render_timeout_s)
    except BaseException:
        enc.kill()
        raise
    if rc != 0:
        err = enc.stderr.read().decode(errors="replace")[-800:] if enc.stderr else ""
        raise RuntimeError(f"ffmpeg segment encode failed ({rc}): {err}")
    return stop - start, time.perf_counter() - t0


def render_video(
    builder: Builder,
    args: tuple[Any, ...],
    out_path: Path,
    *,
    audio_path: Path | None = None,
    workers: int | None = None,
    settings: EncodeSettings | None = None,
    frame_range: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Render a composition to an MP4 (optionally muxing ``audio_path``).

    Args:
        builder: Module-level callable returning ``(Composition, RenderContext)`` from ``args``.
        args: Plain, picklable arguments for ``builder``.
        out_path: Destination ``.mp4`` (parent must exist and be writable).
        audio_path: Optional WAV/AAC to mux (trimmed to video length).
        workers: Worker processes (default: CPU count, capped at 12).
        settings: Encoder settings.
        frame_range: Optional (start, stop) subset of frames (for previews).

    Returns:
        Benchmark/info dict (frames, seconds, fps achieved, realtime factor, workers).
    """
    settings = settings or EncodeSettings()
    comp, _ = builder(*args)
    _check_limits(comp.width, comp.height, comp.fps, comp.duration)
    total = comp.frame_count
    f0, f1 = frame_range or (0, total)
    f1 = min(f1, total)
    n_frames = f1 - f0
    if n_frames <= 0:
        raise ValueError("empty frame range")
    cpu = os.cpu_count() or 2
    workers = max(1, min(workers or min(cpu, 12), math.ceil(n_frames / 15)))
    threads = max(1, cpu // workers)
    # More chunks than workers smooths load imbalance between busy and calm scenes.
    n_chunks = min(n_frames, workers * 3)
    bounds = [f0 + round(i * n_frames / n_chunks) for i in range(n_chunks + 1)]

    t_start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="render-") as tmp:
        tmpdir = Path(tmp)
        segs = [tmpdir / f"seg_{i:04d}.mp4" for i in range(n_chunks)]
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(
                    _render_chunk,
                    builder,
                    args,
                    bounds[i],
                    bounds[i + 1],
                    str(segs[i]),
                    settings,
                    threads,
                )
                for i in range(n_chunks)
            ]
            for fut in futures:
                fut.result()
        listing = tmpdir / "segments.txt"
        listing.write_text("".join(f"file '{s.name}'\n" for s in segs), encoding="utf-8")
        video_only = tmpdir / "video.mp4"
        proc.run(
            [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
             "-i", str(listing), "-c", "copy", str(video_only)],
            timeout=600,
        )  # fmt: skip
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if audio_path is not None:
            proc.run(
                [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(video_only),
                 "-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                 "-c:a", "aac", "-b:a", settings.audio_bitrate, "-ar", "48000",
                 "-t", f"{n_frames / comp.fps:.3f}", "-movflags", "+faststart", str(out_path)],
                timeout=600,
            )  # fmt: skip
        else:
            proc.run(
                [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(video_only),
                 "-c", "copy", "-movflags", "+faststart", str(out_path)],
                timeout=600,
            )  # fmt: skip
    elapsed = time.perf_counter() - t_start
    size_mb = out_path.stat().st_size / 1e6
    if size_mb > rules().render.max_output_mb:
        out_path.unlink(missing_ok=True)
        raise ValueError("output exceeded max_output_mb")
    info = {
        "frames": n_frames,
        "seconds": round(elapsed, 2),
        "render_fps": round(n_frames / elapsed, 2),
        "realtime_factor": round((n_frames / comp.fps) / elapsed, 3),
        "workers": workers,
        "chunks": n_chunks,
        "resolution": f"{comp.width}x{comp.height}@{comp.fps}",
        "motion_blur": comp.motion_blur,
        "size_mb": round(size_mb, 2),
        "output": out_path.name,
    }
    log_event(log, "render complete", **info)
    return info


def render_still(builder: Builder, args: tuple[Any, ...], t: float, out_png: Path) -> Path:
    """Render a single frame at time ``t`` to PNG (thumbnails, key frames)."""
    comp, ctx = builder(*args)
    fr = FrameRenderer(comp, ctx)
    frame = min(round(t * comp.fps), max(comp.frame_count - 1, 0))
    fr.render(frame)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out_png.write_bytes(fr.surface.snapshot().to_png())
    return out_png


def write_manifest(out_dir: Path, name: str, payload: dict[str, Any]) -> Path:
    """Store spec/theme/seed (and benchmark) next to an output for reproducibility."""
    path = out_dir / f"{name}.manifest.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def copy_if_exists(src: Path, dst: Path) -> None:
    """Copy a file if present (helper for packaging outputs)."""
    if src.is_file():
        shutil.copy2(src, dst)
