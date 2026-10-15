"""Safe subprocess execution (OWASP A03 Injection).

All external programs are launched through :func:`run` / :func:`popen`: argument lists only, never
``shell=True``, executables restricted to an allowlist, every argument type-checked, and a timeout
on every blocking call.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404
from collections.abc import Sequence
from pathlib import Path

ALLOWED_EXECUTABLES = frozenset({"ffmpeg", "ffprobe", "git"})
DEFAULT_TIMEOUT_S = 600.0


class UnsafeCommandError(ValueError):
    """Raised when a command is not an allowlisted executable with plain string arguments."""


def _validate(args: Sequence[str | Path]) -> list[str]:
    if isinstance(args, str | bytes) or not args:
        raise UnsafeCommandError("command must be a non-empty argument list")
    out: list[str] = []
    for a in args:
        if not isinstance(a, str | Path):
            raise UnsafeCommandError("arguments must be str or Path")
        s = str(a)
        if "\x00" in s or "\n" in s:
            raise UnsafeCommandError("control characters in argument")
        out.append(s)
    exe_name = os.path.basename(out[0]).lower().removesuffix(".exe")
    # imageio-ffmpeg ships binaries named like 'ffmpeg-linux64-v4.2.2'; accept that prefix.
    base = exe_name.split("-", 1)[0]
    if base not in ALLOWED_EXECUTABLES:
        raise UnsafeCommandError(f"executable not allowlisted: {base}")
    return out


def run(
    args: Sequence[str | Path],
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    input_bytes: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    """Run an allowlisted program and capture its output.

    Args:
        args: Executable followed by arguments.
        timeout: Seconds before the process is killed.
        input_bytes: Optional stdin payload.
        check: Raise ``CalledProcessError`` on non-zero exit.

    Returns:
        The completed process with captured stdout/stderr bytes.

    Raises:
        UnsafeCommandError: If the command fails validation.
        subprocess.TimeoutExpired / CalledProcessError: Propagated from subprocess.
    """
    argv = _validate(args)
    return subprocess.run(  # noqa: S603  # nosec B603
        argv, input=input_bytes, capture_output=True, timeout=timeout, check=check, shell=False
    )


def popen(args: Sequence[str | Path], **kwargs: object) -> subprocess.Popen[bytes]:
    """Start an allowlisted program for streaming I/O (e.g. piping raw frames into FFmpeg).

    ``shell`` may not be passed; it is always False.
    """
    if "shell" in kwargs:
        raise UnsafeCommandError("shell is never allowed")
    argv = _validate(args)
    proc: subprocess.Popen[bytes] = subprocess.Popen(  # type: ignore[call-overload]  # noqa: S603  # nosec B603
        argv, shell=False, **kwargs
    )
    return proc
