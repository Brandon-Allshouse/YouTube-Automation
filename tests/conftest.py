"""Shared pytest fixtures. Hypothesis stores its example DB in /tmp (the repo mount is read-only)."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator

import pytest
from hypothesis import settings
from hypothesis.database import DirectoryBasedExampleDatabase

settings.register_profile(
    "ci",
    database=DirectoryBasedExampleDatabase(os.path.join(os.environ.get("TMPDIR", "/tmp"), "hypothesis")),
    deadline=None,
    max_examples=150,
)
settings.load_profile("ci")


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    """In-memory pipeline database."""
    from src.db import connect

    conn = connect(":memory:")  # type: ignore[arg-type]
    yield conn
    conn.close()
