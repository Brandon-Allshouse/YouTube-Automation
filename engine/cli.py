"""Renderer CLI.

``python -m engine.cli render SPEC.json output/x.mp4 [--theme neon] [--format long] [--scale 0.5]``
``python -m engine.cli still SPEC.json output/x.png --t 3.2``

Spec files must live inside the repository; outputs must go under ``output/``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from engine.build import build_from_spec
from engine.core.render import render_still, render_video, write_manifest
from engine.schema.spec import validate_spec
from src.common.paths import OUTPUT_DIR, REPO_ROOT, ensure_within


def _load(spec_path: Path, theme: str | None, fmt: str | None) -> str:
    raw = json.loads(ensure_within(spec_path, REPO_ROOT).read_text(encoding="utf-8"))
    if theme:
        raw["theme"] = theme
    if fmt:
        raw["format"] = fmt
    return validate_spec(raw).model_dump_json()


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    p = argparse.ArgumentParser(description="Render scene specs")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("spec", type=Path)
    r.add_argument("out", type=Path)
    r.add_argument("--theme")
    r.add_argument("--format", choices=("short", "long"))
    r.add_argument("--scale", type=float, default=1.0)
    r.add_argument("--audio", type=Path)
    r.add_argument("--workers", type=int)
    s = sub.add_parser("still")
    s.add_argument("spec", type=Path)
    s.add_argument("out", type=Path)
    s.add_argument("--t", type=float, required=True)
    s.add_argument("--theme")
    s.add_argument("--format", choices=("short", "long"))
    a = p.parse_args(argv)
    spec_json = _load(a.spec, a.theme, a.format)
    out = ensure_within(a.out, OUTPUT_DIR)
    if a.cmd == "render":
        audio = ensure_within(a.audio, REPO_ROOT) if a.audio else None
        info = render_video(build_from_spec, (spec_json, a.scale), out, audio_path=audio, workers=a.workers)
        write_manifest(out.parent, out.stem, {"spec": json.loads(spec_json), "render": info})
        print(json.dumps(info))
    else:
        print(render_still(build_from_spec, (spec_json, 1.0), a.t, out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
