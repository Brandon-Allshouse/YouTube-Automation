"""AnimatedMap: Natural Earth maps with camera moves, projection morphs and true-size moves.

Performance: country geometry is projected and converted to vector paths once per projection
state (cached per worker); camera pans/zooms are canvas transforms, so steady shots cost a few
milliseconds per frame. Only projection morphs and true-size moves rebuild geometry, and only for
the frames where they animate (morph progress is quantised to 1/90 steps).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from engine.components.base import exit_alpha, region_rect
from engine.core.canvas import Canvas, PathBuilder
from engine.core.color import mix, with_alpha
from engine.core.easing import get_easing
from engine.core.geo import Arr, densify, graticule, load_countries, project, slide_to
from engine.core.scene import Node, Rect, RenderContext
from engine.schema.spec import AnimatedMapSpec

MORPH_STEPS = 90


class AnimatedMap(Node):
    """Map component."""

    def __init__(self, spec: AnimatedMapSpec, ctx: RenderContext) -> None:
        super().__init__(start=spec.start, end=spec.end, key="AnimatedMap")
        self.spec = spec
        self.box: Rect = region_rect(ctx, spec.region)
        self.countries = load_countries(spec.resolution)
        hl = set(spec.highlight)
        for f in self.countries.values():
            if f.continent in spec.highlight_continents:
                hl.add(f.id)
        self.highlight_ids = hl
        self.move_ids = {m.country for m in spec.moves}
        for code in hl | self.move_ids:
            if code not in self.countries:
                raise ValueError(f"unknown country code {code}")
        # rhumb lines are straight in Mercator space: keep vertices and interpolate in projected coords
        self.routes = [
            np.asarray(p.points, dtype=np.float64)
            if p.kind == "rhumb"
            else densify(np.asarray(p.points, dtype=np.float64), 1.5)
            for p in spec.paths
        ]
        self.grat = graticule(15.0)
        # zoom 1 = world width fills a 9:16-equivalent width, so a spec frames the same latitudes in
        # Shorts and long-form (landscape just shows more longitude instead of cropping north/south)
        self.base_scale = min(self.box.w, self.box.h * 9 / 16) / (2 * math.pi)

    # --- projection state -----------------------------------------------------------------
    def _morph_u(self, t: float) -> float:
        s = self.spec
        if s.morph_to is None:
            return 0.0
        raw = min(max((t - s.morph_start) / s.morph_duration, 0.0), 1.0)
        q = round(get_easing("in_out_cubic")(raw) * MORPH_STEPS) / MORPH_STEPS
        return q

    def _proj(self, pts: Arr, u: float) -> Arr:
        s = self.spec
        if s.morph_to is None or u <= 0:
            return project(pts, s.projection)
        if u >= 1:
            return project(pts, s.morph_to)
        return project(pts, morph=(s.projection, s.morph_to, u))

    def _layer_paths(self, ctx: RenderContext, u: float) -> dict[str, Any]:
        s = self.spec
        key = (
            f"map:{s.resolution}:{s.projection}:{s.morph_to}:{u:.4f}:"
            f"{sorted(self.highlight_ids)}:{sorted(self.move_ids)}"
        )
        cached: dict[str, Any] | None = ctx.cache.get(key)
        if cached is not None:
            return cached
        land = PathBuilder()
        hl = PathBuilder()
        for f in self.countries.values():
            if f.id in self.move_ids:
                continue  # drawn separately (ghost + mover)
            rings = [self._proj(r, u) * (1, -1) for r in f.rings]
            (hl if f.id in self.highlight_ids else land).rings(rings)
        grat = PathBuilder().rings([self._proj(g, u) * (1, -1) for g in self.grat], closed=False)
        out = {"land": land, "hl": hl, "grat": grat}
        if len(ctx.cache) > 400:  # bound memory on long morphs
            for k in [k for k in ctx.cache if k.startswith("map:")][:200]:
                ctx.cache.pop(k, None)
        ctx.cache[key] = out
        return out

    # --- camera ---------------------------------------------------------------------------
    def _camera(self, t: float, u: float) -> tuple[float, float, float]:
        s = self.spec
        raw = min(max((t - s.move_start) / s.move_duration, 0.0), 1.0)
        k = get_easing("in_out_cubic")(raw)
        c0 = np.array(s.center_from, dtype=np.float64)
        c1 = np.array(s.center_to if s.center_to else s.center_from, dtype=np.float64)
        z0 = s.zoom_from
        z1 = s.zoom_to if s.zoom_to else s.zoom_from
        zoom = math.exp(math.log(z0) + (math.log(z1) - math.log(z0)) * k)  # perceptually even zoom
        center = c0 + (c1 - c0) * k
        cx, cy = self._proj(center[None, :], u)[0]
        return float(cx), float(-cy), zoom * self.base_scale

    def render(self, cv: Canvas, t: float, ctx: RenderContext) -> None:
        """Draw the map at local time ``t``."""
        th = ctx.theme
        s = self.spec
        un = ctx.unit
        fade = exit_alpha(ctx, t + self.start, self.end)
        intro = min(max(t / 0.5, 0.0), 1.0)
        if fade <= 0 or intro <= 0:
            return
        u = self._morph_u(t)
        paths = self._layer_paths(ctx, u)
        camx, camy, scale = self._camera(t, u)
        box = self.box
        land_c = mix(th.color("surface"), th.color("text"), 0.10)
        border_c = with_alpha(th.color("text"), 0.22)
        hl_k = get_easing(th.motion.enter)(min(max((t - s.highlight_at) / 0.7, 0.0), 1.0))
        hl_c = mix(land_c, th.color("accent2"), hl_k)
        with cv.layer(alpha=fade * intro), cv.saved():
            cv.clip_rect(box.x, box.y, box.w, box.h)
            cv.translate(box.cx, box.cy)
            cv.scale(scale)
            cv.translate(-camx, -camy)
            px = 1.0 / scale  # one screen pixel in map units
            if s.graticule:
                cv.path(
                    paths["grat"],
                    stroke=with_alpha(th.color("accent"), 0.16),
                    stroke_width=1.6 * un * px,
                )
            cv.path(paths["land"], fill=land_c)
            cv.path(paths["land"], stroke=border_c, stroke_width=1.3 * un * px)
            if self.highlight_ids:
                cv.path(paths["hl"], fill=hl_c)
                if hl_k > 0:
                    cv.path(
                        paths["hl"],
                        stroke=with_alpha(th.color("accent2"), 0.55 * hl_k),
                        stroke_width=7 * un * px,
                        blur=6 * un * px,
                    )
                cv.path(paths["hl"], stroke=border_c, stroke_width=1.3 * un * px)
            for m in s.moves:
                self._draw_move(cv, ctx, t, u, m, px)
            for i, route in enumerate(self.routes):
                ps = s.paths[i]
                prog = get_easing("in_out_cubic")(min(max((t - ps.start) / ps.duration, 0.0), 1.0))
                if prog <= 0:
                    continue
                pts = self._proj(route, u) * (1, -1)
                pb = PathBuilder().polyline([tuple(p) for p in pts.tolist()])
                cv.path(
                    pb,
                    stroke=with_alpha(th.color("highlight"), 0.35),
                    stroke_width=12 * un * px,
                    trim=(0, prog),
                    blur=6 * un * px,
                )
                cv.path(
                    pb,
                    stroke=th.color("highlight"),
                    stroke_width=5 * un * px,
                    trim=(0, prog),
                    dash=(14 * un * px, 9 * un * px),
                )
                hx, hy, _ = pb.point_at(prog)
                cv.circle(hx, hy, 9 * un * px, fill=th.color("highlight"))
        # markers and labels are drawn in screen space so text stays crisp and constant-size
        for mk in s.markers:
            if t < mk.at:
                continue
            p = self._proj(np.array([[mk.lon, mk.lat]]), u)[0]
            sx = box.cx + (p[0] - camx) * scale
            sy = box.cy + (-p[1] - camy) * scale
            self._marker(cv, ctx, t - mk.at, sx, sy, mk.label, fade)
        if s.title:
            cv.text(
                s.title,
                box.x + 40 * un,
                box.y + 90 * un,
                th.fonts.display.spec(72 * un),
                with_alpha(th.color("text"), fade * intro),
                shadow=(0, 6 * un, 16 * un, (0, 0, 0, 0.6)),
            )

    def _draw_move(self, cv: Canvas, ctx: RenderContext, t: float, u: float, m: Any, px: float) -> None:
        th = ctx.theme
        un = ctx.unit
        feat = self.countries[m.country]
        k = get_easing("in_out_cubic")(min(max((t - m.start) / m.duration, 0.0), 1.0))
        ghost = PathBuilder().rings([self._proj(r, u) * (1, -1) for r in feat.rings])
        if k > 0:  # dashed ghost of where it "looks" like it is
            cv.path(
                ghost,
                stroke=with_alpha(th.color("accent"), 0.6),
                stroke_width=2.5 * un * px,
                dash=(10 * un * px, 8 * un * px),
            )
        moved = slide_to(feat.rings, m.to, k)
        mover = PathBuilder().rings([self._proj(r, u) * (1, -1) for r in moved])
        cv.path(mover, fill=with_alpha(th.color("accent"), 0.92))
        cv.path(mover, stroke=with_alpha(th.color("highlight"), 0.9), stroke_width=2.5 * un * px)

    def _marker(
        self,
        cv: Canvas,
        ctx: RenderContext,
        age: float,
        x: float,
        y: float,
        label: str,
        fade: float,
    ) -> None:
        th = ctx.theme
        un = ctx.unit
        pop = get_easing(th.motion.emphasis)(min(age / 0.4, 1.0))
        pulse = (age * 0.8) % 1.0
        cv.circle(
            x,
            y,
            (14 + 46 * pulse) * un,
            stroke=with_alpha(th.color("highlight"), (1 - pulse) * 0.8 * fade),
            stroke_width=4 * un,
        )
        cv.circle(x, y, 12 * un * pop, fill=with_alpha(th.color("highlight"), fade))
        if label:
            f = th.fonts.body.spec(40 * un)
            w = ctx.fonts.measure(label, f)
            bw, bh = w + 36 * un, 64 * un
            safe = ctx.safe
            # keep labels inside the safe zone: flip left of the point near the right edge, clamp vertically
            bx = x + 26 * un if x + 26 * un + bw <= safe.right else x - 26 * un - bw
            bx = max(bx, safe.x)
            by = min(max(y - 34 * un, safe.y), safe.bottom - bh)
            cv.rect(
                bx,
                by,
                w + 36 * un,
                64 * un,
                fill=with_alpha(th.color("bg"), 0.82 * fade * pop),
                radius=14 * un,
            )
            cv.text(label, bx + 18 * un, by + 44 * un, f, with_alpha(th.color("text"), fade * pop))
