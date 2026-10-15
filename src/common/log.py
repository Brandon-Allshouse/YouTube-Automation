"""Structured JSON logging with secret redaction.

Every record is one JSON object per line in ``logs/<name>.jsonl`` (and stderr), with a UTC
timestamp and the current job id. A redaction pass scrubs anything that looks like an API key,
OAuth token, bearer credential, or email address before it is written.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.common.paths import LOGS_DIR, ensure_dir

_job_id: contextvars.ContextVar[str] = contextvars.ContextVar("job_id", default="-")

_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "[REDACTED_GOOGLE_KEY]"),
    (re.compile(r"ya29\.[0-9A-Za-z_\-]+"), "[REDACTED_OAUTH_TOKEN]"),
    (re.compile(r"1//[0-9A-Za-z_\-]{20,}"), "[REDACTED_REFRESH_TOKEN]"),
    (re.compile(r"(?i)bearer\s+[0-9A-Za-z._\-]+"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\b(key|token|secret|password|api_key)=([^&\s\"']+)"), r"\1=[REDACTED]"),
    (
        re.compile(r"(?i)\"(api_key|access_token|refresh_token|client_secret|password|token)\"\s*:\s*\"[^\"]*\""),
        r'"\1": "[REDACTED]"',
    ),
    (re.compile(r"GOCSPX-[0-9A-Za-z_\-]{10,}"), "[REDACTED_CLIENT_SECRET]"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
]


def redact(text: str) -> str:
    """Return ``text`` with credential-like and email substrings replaced by placeholders."""
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


def new_job_id(prefix: str = "job") -> str:
    """Create a short unique job id and bind it to the current context."""
    job = f"{prefix}-{uuid.uuid4().hex[:12]}"
    _job_id.set(job)
    return job


def current_job_id() -> str:
    """Return the job id bound to the current context (``-`` if none)."""
    return _job_id.get()


class JsonFormatter(logging.Formatter):
    """Format records as redacted single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "job_id": getattr(record, "job_id", current_job_id()),
            "msg": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str, ensure_ascii=False))


_configured: set[str] = set()


def get_logger(name: str, *, to_file: bool = True) -> logging.Logger:
    """Return a JSON logger writing to stderr and ``logs/<top-level-name>.jsonl``."""
    logger = logging.getLogger(name)
    root_name = name.split(".", maxsplit=1)[0]
    if root_name not in _configured:
        root = logging.getLogger(root_name)
        root.setLevel(logging.INFO)
        root.propagate = False
        fmt = JsonFormatter()
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)
        if to_file:
            try:
                path: Path = ensure_dir(LOGS_DIR) / f"{root_name}.jsonl"
                fh = logging.FileHandler(path, encoding="utf-8")
                fh.setFormatter(fmt)
                root.addHandler(fh)
            except OSError:
                pass  # read-only environments (e.g. tests) still log to stderr
        _configured.add(root_name)
    return logger


def log_event(logger: logging.Logger, msg: str, **fields: Any) -> None:
    """Log ``msg`` at INFO with structured ``fields`` merged into the JSON record."""
    logger.info(msg, extra={"fields": fields})
