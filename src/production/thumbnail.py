"""Thumbnail engine: one bold focal visual, ≤ 4 words, high contrast, rendered by our engine.

Focal kinds:
* ``silhouettes``: countries drawn at true relative size (equal-area projection, shared scale)
* ``map_ghost``: Mercator crop where a country is highlighted and its true size is outlined
* ``stat``: a huge number with a short unit
* ``icon``: one of the code-drawn icons

Text sits on the left (or top on 9:16 covers), with an accent box behind the highlight word, a
heavy outline and shadow. Every thumbnail is checked by ``src.quality.legibility``.

CLI: ``python -m src.production.thumbnail --demo`` renders the demo script's 3 concepts.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Annotated

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field, field_validator

from engine.components.ambient import ICONS
from engine.components.background import ThemedBackground
from engine.core.canvas import Canvas, Fonts, FontSpec, PathBuilder, Surface
from engine.core.color import RGBA, mix, with_alpha
from engine.core.geo import load_countries, project, rotate_to, slide_to
from engine.core.scene import Rect, RenderContext
from engine.core.text import fit_text
from engine.core.theme import load_theme
from src.common.paths import OUTPUT_DIR, REPO_ROOT, ensure_dir, ensure_within, safe_join, validate_id
from src.production.script import Focal, Script, load_script
from src.quality.legibility import LegibilityReport, TextBox, check_legibility, phone_preview

W, H = 1280, 720


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ThumbSpec(_M):
    """A thumbnail variant."""

    id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
    theme: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
    text: Annotated[str, Field(min_length=1, max_length=32)]
    highlight: Annotated[str, Field(max_length=16)] = ""
    focal: Focal
    seed: int = 0

    @field_validator("text")
    @classmethod
    def _four_words(cls, v: str) -> str:
        if len(v.split()) > 4:
            raise ValueError("thumbnail text is limited to 4 words")
        return v


def _ctx(theme: str, seed: int) -> RenderContext:
    th = load_theme(theme)
    return RenderContext(
        width=W,
        height=H,
        fps=30,
        layout="long",
        theme=th,
        fonts=Fonts(),
        seed=seed,
        safe=Rect(0, 0, W, H),
    )


def _shape_paths(
    rings: list[npt.NDArray[np.float64]],
    projection: str,
    box: Rect,
    scale: float,
    center: tuple[float, float],
) -> PathBuilder:
    pts = []
    for r in rings:
        p = project(r, projection)
        p[:, 1] *= -1
        pts.append(
            np.stack(
                [box.cx + (p[:, 0] - center[0]) * scale, box.cy + (p[:, 1] - center[1]) * scale],
                axis=1,
            )
        )
    return PathBuilder().rings(pts)


def _bounds(rings: list[npt.NDArray[np.float64]], projection: str) -> tuple[float, float, float, float]:
    allp = np.concatenate([project(r, projection) for r in rings])
    allp[:, 1] *= -1
    return (
        float(allp[:, 0].min()),
        float(allp[:, 1].min()),
        float(allp[:, 0].max()),
        float(allp[:, 1].max()),
    )


def _draw_silhouettes(cv: Canvas, ctx: RenderContext, f: Focal, box: Rect) -> None:
    """Countries at true relative size: each rotated to the equator, then equal-area projected."""
    th = ctx.theme
    countries = load_countries("50m")
    shapes: list[list[npt.NDArray[np.float64]]] = []
    if f.continent:
        rings = [r for c in countries.values() if c.continent == f.continent for r in c.rings]
        shapes.append(slide_to(rings, (0.0, 0.0)))
    for code in f.countries:
        shapes.append(slide_to(countries[code].rings, (0.0, 0.0)))
    bounds = [_bounds(s, "equal_earth") for s in shapes]
    widths = [b[2] - b[0] for b in bounds]
    heights = [b[3] - b[1] for b in bounds]
    gap = 0.08 * max(widths)
    scale = min((box.w * 0.92) / (sum(widths) + gap * (len(shapes) - 1)), (box.h * 0.82) / max(heights))
    x = box.cx - (sum(widths) + gap * (len(shapes) - 1)) * scale / 2
    base_y = box.y + box.h * 0.88
    colors = [th.color("accent"), th.color("accent2"), th.color("highlight")]
    for i, (shape, b) in enumerate(zip(shapes, bounds, strict=True)):
        w, h = (b[2] - b[0]) * scale, (b[3] - b[1]) * scale
        sub = Rect(x, base_y - h, w, h)
        center = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        path = _shape_paths(shape, "equal_earth", sub, scale, center)
        col = colors[i % len(colors)]
        cv.path(path, fill=with_alpha(col, 0.45), blur=18)
        cv.path(path, fill=col)
        cv.path(path, stroke=mix(col, (1, 1, 1, 1), 0.5), stroke_width=3)
        if i < len(f.labels) and f.labels[i]:
            cv.text(
                f.labels[i].upper(),
                x + w / 2,
                base_y + 46,
                FontSpec(th.fonts.body.family, 34, 800),
                th.color("text"),
                align="center",
                stroke=(0, 0, 0, 1),
                stroke_width=6,
            )
        x += w + gap * scale


def _draw_map_ghost(cv: Canvas, ctx: RenderContext, f: Focal, box: Rect) -> None:
    th = ctx.theme
    countries = load_countries("50m")
    code = f.countries[0] if f.countries else "GRL"
    feat = countries[code]
    b = _bounds(list(feat.rings), "mercator")
    scale = min(box.w / (b[2] - b[0]), box.h / (b[3] - b[1])) * 0.82
    center = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    with cv.saved():
        cv.clip_rect(box.x, box.y, box.w, box.h)
        for c in countries.values():  # neighbours for context
            cb = _bounds(list(c.rings[:1]), "mercator")
            if cb[2] < center[0] - box.w / scale or cb[0] > center[0] + box.w / scale:
                continue
            p = _shape_paths(list(c.rings), "mercator", box, scale, center)
            if c.id == code:
                cv.path(p, fill=with_alpha(th.color("accent"), 0.5), blur=20)
                cv.path(p, fill=th.color("accent"))
            else:
                cv.path(p, fill=mix(th.color("surface"), th.color("text"), 0.12))
    # true size at its own latitude: equal-area area ratio → scale the outline about its centroid
    true_scale = math.sqrt(_true_area_ratio(feat.rings))
    ghost = _shape_paths(list(feat.rings), "mercator", box, scale * true_scale, center)
    cv.path(ghost, fill=with_alpha(th.color("highlight"), 0.25))
    cv.path(ghost, stroke=th.color("highlight"), stroke_width=5, dash=(16, 10))
    if f.strike:
        cv.line(
            box.x + box.w * 0.15,
            box.y + box.h * 0.85,
            box.right - box.w * 0.15,
            box.y + box.h * 0.15,
            th.color("negative"),
            18,
        )


def _true_area_ratio(rings: tuple[npt.NDArray[np.float64], ...]) -> float:
    """(true area) / (Mercator-apparent area at equator scale) for a shape."""

    def planar(rs: tuple[npt.NDArray[np.float64], ...] | list[npt.NDArray[np.float64]], proj: str) -> float:
        total = 0.0
        for r in rs:
            p = project(r, proj)
            x, y = p[:, 0], p[:, 1]
            total += 0.5 * abs(float(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))))
        return total

    at_equator = planar(rotate_to(rings, (0.0, 0.0)), "mercator")
    here = planar(rings, "mercator")
    return at_equator / here if here > 0 else 1.0


def _draw_stat(cv: Canvas, ctx: RenderContext, f: Focal, box: Rect) -> None:
    th = ctx.theme
    blk = fit_text(
        f.value,
        ctx.fonts,
        th.fonts.display.spec(420),
        box.w * 0.95,
        box.h * 0.9,
        max_lines=1,
        min_size=120,
    )
    cv.radial_glow(box.cx, box.cy, box.w * 0.6, with_alpha(th.color("accent"), 0.45), blend="screen")
    cv.text(
        f.value,
        box.cx,
        box.cy,
        blk.font,
        th.color("highlight"),
        align="center",
        baseline="middle",
        stroke=(0, 0, 0, 1),
        stroke_width=14,
        shadow=(0, 12, 26, (0, 0, 0, 0.7)),
    )


def _draw_icon(cv: Canvas, ctx: RenderContext, f: Focal, box: Rect) -> None:
    th = ctx.theme
    if f.icon not in ICONS:
        raise ValueError("unknown icon")
    size = min(box.w, box.h) * 0.38
    with cv.saved():
        cv.translate(box.cx, box.cy)
        cv.scale(size)
        cv.circle(0, 0, 1.4, fill=with_alpha(th.color("accent"), 0.22), blur=0.3)
        for path, filled in ICONS[f.icon](1.0):
            if filled:
                cv.path(path, fill=th.color("accent"))
            else:
                cv.path(path, stroke=th.color("accent"), stroke_width=0.09)


FOCALS = {
    "silhouettes": _draw_silhouettes,
    "map_ghost": _draw_map_ghost,
    "stat": _draw_stat,
    "icon": _draw_icon,
}


def render_thumbnail(spec: ThumbSpec, out_png: Path, *, with_text: bool = True) -> tuple[Path, list[TextBox], RGBA]:
    """Render a thumbnail; returns (path, text boxes, text colour) for the legibility check."""
    ctx = _ctx(spec.theme, spec.seed)
    th = ctx.theme
    surf = Surface(W, H, ctx.fonts)
    cv = surf.canvas
    ThemedBackground("thumb", glow_positions=((0.72, 0.45), (0.15, 0.85))).draw(cv, 2.0, ctx)
    focal_box = Rect(W * 0.44, H * 0.06, W * 0.54, H * 0.88)
    FOCALS[spec.focal.kind](cv, ctx, spec.focal, focal_box)
    boxes: list[TextBox] = []
    text_col = th.color("text")
    # scrim: guarantees text contrast whatever the focal visual does behind it
    cv.gradient_rect(
        0,
        0,
        W * 0.62,
        H,
        [with_alpha(th.color("bg"), 0.92), with_alpha(th.color("bg"), 0.0)],
        angle_deg=0,
        stops=[0.55, 1.0],
    )
    if with_text:
        role = th.fonts.thumb or th.fonts.display
        words = spec.text.upper().split()
        blk = fit_text(
            " ".join(words),
            ctx.fonts,
            FontSpec(role.family, 230, role.weight, role.axes),
            W * 0.47,
            H * 0.80,
            max_lines=3,
            min_size=70,
            line_height=1.0,
        )
        y = H / 2 - blk.height / 2
        x0 = W * 0.05
        hl = spec.highlight.upper()
        for ln in blk.lines:
            base = y + blk.line_height * 0.80
            for w in ln.words:
                is_hl = hl and w.text.strip(".,!?") == hl
                cap = ctx.fonts.metrics(blk.font)[2]  # real cap height of this face
                if is_hl:
                    cv.rect(
                        x0 + w.x - 14,
                        base - cap - 16,
                        w.width + 28,
                        cap + 32,
                        fill=th.color("highlight"),
                        radius=12,
                    )
                col = th.color("bg") if is_hl else text_col
                if is_hl:
                    cv.text(w.text, x0 + w.x, base, blk.font, col)
                else:
                    cv.text(
                        w.text,
                        x0 + w.x,
                        base,
                        blk.font,
                        col,
                        stroke=(0, 0, 0, 1),
                        stroke_width=12,
                        shadow=(0, 10, 18, (0, 0, 0, 0.75)),
                    )
                boxes.append(
                    TextBox(
                        x0 + w.x,
                        base - cap,
                        w.width,
                        cap,
                        th.color("bg") if is_hl else text_col,
                        bool(is_hl),
                        th.color("highlight") if is_hl else None,
                    )
                )
            y += blk.line_height
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out_png.write_bytes(surf.snapshot().to_png())
    return out_png, boxes, text_col


def build_variant(spec: ThumbSpec, out_dir: Path) -> LegibilityReport:
    """Render a variant (+ a text-free plate for contrast sampling) and run the legibility check."""
    stem = validate_id(spec.id, kind="thumbnail id")
    png, boxes, _ = render_thumbnail(spec, safe_join(out_dir, f"{stem}.png"))
    plate, _, _ = render_thumbnail(spec, safe_join(out_dir, f"{stem}.plate.png"), with_text=False)
    report = check_legibility(plate, boxes, word_count=len(spec.text.split()))
    phone_preview(png, safe_join(out_dir, f"{stem}.phone.png"))
    plate.unlink(missing_ok=True)
    return report


def script_variants(script: Script, theme: str) -> list[ThumbSpec]:
    """Thumbnail specs for every renderable concept in a script (``thumb-a``, ``thumb-b``, ...)."""
    return [
        ThumbSpec(
            id=f"thumb-{chr(97 + i)}", theme=theme, text=c.text, highlight=c.highlight, focal=c.focal, seed=script.seed
        )
        for i, c in enumerate(script.thumbnails)
        if c.focal is not None
    ]


def build_for_script(script: Script, theme: str, out_dir: Path) -> dict[str, dict[str, object]]:
    """Render a script's thumbnails into ``out_dir`` and write ``thumbnails.json`` (legibility per variant).

    Returns:
        ``{variant id: legibility report}`` in concept order (empty if no concept has a ``focal``).
    """
    out = ensure_dir(out_dir)
    results = {s.id: build_variant(s, out).as_dict() for s in script_variants(script, theme)}
    safe_join(out, "thumbnails.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


DEMO_SCRIPT = REPO_ROOT / "config" / "demo" / "mercator_short.yaml"
DEMO_VARIANTS = script_variants(load_script(DEMO_SCRIPT), "atlas")


def main(argv: list[str] | None = None) -> int:
    """CLI: render demo thumbnails (or a JSON list of ThumbSpecs) and print legibility reports."""
    p = argparse.ArgumentParser(description="Render thumbnails with legibility checks")
    p.add_argument("--demo", action="store_true")
    p.add_argument("--specs", type=Path, help="JSON list of thumbnail specs")
    p.add_argument("--script", type=Path, help="render a script's thumbnail concepts (needs --theme)")
    p.add_argument("--theme", help="theme for --script (the channel's theme)")
    p.add_argument("--out", type=Path, default=OUTPUT_DIR / "demo" / "thumbnails")
    a = p.parse_args(argv)
    out = ensure_dir(ensure_within(a.out, OUTPUT_DIR))
    if a.script:
        if not a.theme:
            p.error("--script needs --theme")
        script = load_script(ensure_within(a.script, REPO_ROOT))
        res = build_for_script(script, a.theme, out)
        print(json.dumps(res, indent=2))
        return 0 if res and all(r["passed"] for r in res.values()) else 1
    specs = (
        DEMO_VARIANTS
        if a.demo or not a.specs
        else [
            ThumbSpec.model_validate(x)
            for x in json.loads(ensure_within(a.specs, REPO_ROOT).read_text(encoding="utf-8"))
        ]
    )
    results = {s.id: build_variant(s, out).as_dict() for s in specs}
    print(json.dumps(results, indent=2))
    return 0 if all(r["passed"] for r in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
