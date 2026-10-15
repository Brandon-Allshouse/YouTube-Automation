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


@pytest.fixture(scope="session", autouse=True)
def _clean_pipeline_dirs() -> Iterator[None]:
    """Remove what tests create in the real ``data/scripts`` and ``output/videos``.

    Gate tests must use those directories (path confinement only accepts them); without this,
    every run left test scripts and renders next to the owner's real ones.
    """
    import shutil

    from src.common.paths import DATA_DIR, OUTPUT_DIR

    dirs = (DATA_DIR / "scripts", OUTPUT_DIR / "videos")
    before = {d: set(d.iterdir()) if d.is_dir() else set() for d in dirs}
    yield
    for d in dirs:
        for entry in (set(d.iterdir()) if d.is_dir() else set()) - before[d]:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    """In-memory pipeline database."""
    from src.db import connect

    conn = connect(":memory:")  # type: ignore[arg-type]
    yield conn
    conn.close()
