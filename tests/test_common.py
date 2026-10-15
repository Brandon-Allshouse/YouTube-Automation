"""Tests for path safety, subprocess safety, redaction, secrets and config loading."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.common import proc
from src.common.config import rules
from src.common.log import redact
from src.common.paths import UnsafePathError, safe_join, slug_filename, validate_id
from src.common.secrets import mask


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/../../b", "/abs", "C:/win", "..", "a\x00b", ""])
def test_safe_join_rejects_traversal(tmp_path: Path, bad: str) -> None:
    with pytest.raises(UnsafePathError):
        safe_join(tmp_path, bad)


def test_safe_join_accepts_nested(tmp_path: Path) -> None:
    assert safe_join(tmp_path, "a", "b.txt") == (tmp_path / "a" / "b.txt").resolve()


@given(st.text(max_size=80))
def test_slug_filename_always_safe(text: str) -> None:
    name = slug_filename(text, suffix=".mp4")
    assert name.endswith(".mp4")
    assert "/" not in name and "\\" not in name and ".." not in name.removesuffix(".mp4")


@pytest.mark.parametrize("bad", ["", "A", "../x", "x" * 65, "a b", "a/b"])
def test_validate_id_rejects(bad: str) -> None:
    with pytest.raises(ValueError, match="invalid"):
        validate_id(bad)


def test_proc_rejects_shell_strings_and_unknown_exes() -> None:
    with pytest.raises(proc.UnsafeCommandError):
        proc.run("ffmpeg -version")  # type: ignore[arg-type]
    with pytest.raises(proc.UnsafeCommandError):
        proc.run(["bash", "-c", "echo hi"])
    with pytest.raises(proc.UnsafeCommandError):
        proc.run(["ffmpeg", "a\nb"])
    with pytest.raises(proc.UnsafeCommandError):
        proc.popen(["ffmpeg"], shell=True)


def test_redaction() -> None:
    key = "AIza" + "B" * 35
    out = redact(f"url?key={key}&q=1 token=ya29.abc mail me@example.com Bearer abc.def")
    assert key not in out
    assert "ya29.abc" not in out
    assert "me@example.com" not in out
    assert "abc.def" not in out


def test_mask_never_reveals() -> None:
    assert mask("secretvalue") == "<set:11 chars>"
    assert mask(None) == "<unset>"


def test_rules_load_and_validate() -> None:
    r = rules()
    assert r.quota.session_cap_units <= r.quota.daily_limit_units
    assert r.cadence.max_shorts_per_day_per_channel == 1
