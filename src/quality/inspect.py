"""CLI: produce review images for a rendered video (contact sheet + key frames).

``python -m src.quality.inspect output/demo/x.mp4 --duration 5 --every 0.5 --at 1.0 2.6``
Outputs go next to the video in ``<stem>_review/``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from src.common.paths import OUTPUT_DIR, ensure_within
from src.quality.frames import contact_sheet, frame_at


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    p = argparse.ArgumentParser(description="Make contact sheet and key frames for review")
    p.add_argument("video", type=Path)
    p.add_argument("--duration", type=float, required=True)
    p.add_argument("--every", type=float, default=1.0)
    p.add_argument("--cols", type=int, default=8)
    p.add_argument("--tile", type=int, default=240)
    p.add_argument("--at", type=float, nargs="*", default=[])
    p.add_argument("--width", type=int, default=540)
    a = p.parse_args(argv)
    video = ensure_within(a.video, OUTPUT_DIR)
    out = video.parent / f"{video.stem}_review"
    out.mkdir(parents=True, exist_ok=True)
    sheet = contact_sheet(video, out / "contact.png", duration=a.duration, every_s=a.every, cols=a.cols, tile_w=a.tile)
    print(sheet)
    for t in a.at:
        print(frame_at(video, t, out / f"frame_{t:06.2f}.png", width=a.width))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
