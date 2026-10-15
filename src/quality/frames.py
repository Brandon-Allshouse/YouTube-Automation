"""Inspection artifacts: contact sheets and key frames extracted from rendered videos.

The build agent cannot watch video, so it reviews these images instead (see CLAUDE.md).
"""

from __future__ import annotations

import math
from pathlib import Path

from src.common import proc
from src.common.ffmpeg import ffmpeg_exe


def contact_sheet(
    video: Path,
    out_png: Path,
    *,
    duration: float,
    every_s: float = 1.0,
    cols: int = 8,
    tile_w: int = 240,
) -> Path:
    """Tile one frame every ``every_s`` seconds into a single PNG."""
    n = max(1, math.floor(duration / every_s))
    rows = math.ceil(n / cols)
    vf = f"fps=1/{every_s},scale={tile_w}:-2:flags=lanczos,tile={cols}x{rows}:padding=4:margin=4:color=0x202020"
    proc.run(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
         "-vf", vf, "-frames:v", "1", str(out_png)],
        timeout=300,
    )  # fmt: skip
    return out_png


def frame_at(video: Path, t: float, out_png: Path, *, width: int | None = None) -> Path:
    """Extract the frame at time ``t`` (seconds) as PNG, optionally scaled to ``width``."""
    args = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}",
            "-i", str(video), "-frames:v", "1"]  # fmt: skip
    if width:
        args += ["-vf", f"scale={width}:-2:flags=lanczos"]
    proc.run([*args, str(out_png)], timeout=120)
    return out_png
