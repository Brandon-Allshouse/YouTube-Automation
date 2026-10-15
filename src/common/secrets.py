"""Secret loading. Secrets come only from environment variables (populated from ``.env``).

In Docker mode, compose injects ``.env`` into the ``research`` service only. In venv mode we parse
``.env`` ourselves (no third-party dotenv). Values are never logged or included in exceptions.
"""

from __future__ import annotations

import os
import re

from src.common.paths import REPO_ROOT

_LINE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$")


def _load_dotenv_once() -> None:
    path = REPO_ROOT / ".env"
    if os.environ.get("_VP_DOTENV_LOADED") or not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        m = _LINE.match(line)
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = m.group(2).strip("'\"")
    os.environ["_VP_DOTENV_LOADED"] = "1"


def get_secret(name: str) -> str | None:
    """Return the secret named ``name`` or None if unset/empty. Never logs the value."""
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
        raise ValueError("invalid secret name")
    _load_dotenv_once()
    value = os.environ.get(name, "").strip()
    return value or None


def mask(value: str | None) -> str:
    """Return a non-reversible display form of a secret (presence + length only)."""
    return "<unset>" if not value else f"<set:{len(value)} chars>"
