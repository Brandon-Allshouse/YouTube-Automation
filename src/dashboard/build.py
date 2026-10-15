"""Build the portfolio dashboard.

``python -m src.dashboard.build``              → ``output/dashboard/index.html`` + ``dashboard.json``
``python -m src.dashboard.build --fixtures``   → ``output/dashboard/demo.html`` (SYNTHETIC fixture data)

The real build opens the pipeline database read-only. The fixture build loads the synthetic
analytics fixtures into an in-memory database, so it never touches real data. Output paths are
confined to ``output/``; files are written atomically.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from src import db as dbm
from src.common.log import get_logger, log_event
from src.common.paths import OUTPUT_DIR, ensure_dir, ensure_within, safe_join
from src.dashboard.collect import collect, open_readonly
from src.dashboard.render import render

log = get_logger("src.dashboard")
DEFAULT_OUT = OUTPUT_DIR / "dashboard"


def fixture_connection() -> sqlite3.Connection:
    """In-memory pipeline DB with the synthetic analytics fixtures and bandit posteriors loaded."""
    from src.analytics.pull_stats import load_fixture_into
    from src.learning.bandit import update

    conn = dbm.connect(":memory:")  # type: ignore[arg-type]
    load_fixture_into(conn)
    update(conn)
    return conn


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def build(*, fixtures: bool = False, db_path: Path | None = None, out_dir: Path = DEFAULT_OUT) -> dict[str, Any]:
    """Collect, render and write the dashboard; returns the snapshot dict."""
    out = ensure_dir(ensure_within(out_dir, OUTPUT_DIR))
    if fixtures:
        conn = fixture_connection()
        source = "SYNTHETIC analytics fixtures (tests/fixtures/analytics)"
    else:
        conn = open_readonly(db_path or dbm.DEFAULT_DB)
        source = "pipeline database (read-only)"
    try:
        snap = collect(conn, source=source)
    finally:
        conn.close()
    stem = "demo" if fixtures else "index"
    _write_atomic(safe_join(out, f"{stem}.html"), render(snap))
    _write_atomic(safe_join(out, f"{stem}.json"), json.dumps(snap, indent=1, default=str))
    log_event(
        log,
        "dashboard built",
        fixtures=fixtures,
        videos=snap["portfolio"]["videos_total"],
        scored=snap["portfolio"]["scored"],
        portfolio_score=snap["portfolio"]["score"],
    )
    return snap


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    p = argparse.ArgumentParser(description="Build the portfolio dashboard (static HTML, read-only)")
    p.add_argument("--fixtures", action="store_true", help="demo with SYNTHETIC fixture data (output demo.html)")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--db", type=Path, default=None, help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    try:
        snap = build(fixtures=a.fixtures, db_path=a.db, out_dir=a.out)
    except (ValueError, FileNotFoundError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    pf = snap["portfolio"]
    print(json.dumps({"portfolio_score": pf["score"], "videos": pf["videos_total"], "scored": pf["scored"],
                      "out": str((ensure_within(a.out, OUTPUT_DIR)).relative_to(OUTPUT_DIR.parent))}))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
