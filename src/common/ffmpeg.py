"""Single resolver for the FFmpeg binary, so code is agnostic to Docker vs. venv mode.

Docker mode uses the image's system ``ffmpeg``. The venv fallback uses the binary bundled by the
``imageio-ffmpeg`` package. A host ``ffmpeg`` outside those two is never used (see ADR-002).
"""

from __future__ import annotations

import functools
import os
import shutil


class FFmpegNotFoundError(RuntimeError):
    """Raised when no permitted FFmpeg binary is available."""


def _in_container() -> bool:
    return os.path.exists("/.dockerenv") or os.environ.get("VIDEO_PIPELINE_CONTAINER") == "1"


@functools.cache
def ffmpeg_exe() -> str:
    """Return the path to the FFmpeg executable for the current mode.

    Raises:
        FFmpegNotFoundError: If neither the container binary nor imageio-ffmpeg is available.
    """
    if _in_container():
        found = shutil.which("ffmpeg")
        if found:
            return found
    try:
        import imageio_ffmpeg  # type: ignore[import-not-found]  # optional venv-mode dependency
    except ImportError as exc:
        raise FFmpegNotFoundError(
            "FFmpeg unavailable: run inside the Docker image or install imageio-ffmpeg in .venv"
        ) from exc
    return str(imageio_ffmpeg.get_ffmpeg_exe())


@functools.cache
def ffprobe_exe() -> str | None:
    """Return the ffprobe path in container mode, else None (imageio-ffmpeg has no ffprobe)."""
    if _in_container():
        return shutil.which("ffprobe")
    return None
