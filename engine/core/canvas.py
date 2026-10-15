"""Drawing interface for components, backed by skia-python.

This module is the **only** place that imports skia. Components draw through :class:`Canvas`,
:class:`PathBuilder`, :class:`Fonts` and :class:`Picture`, so a pycairo backend could replace this
module without touching components (ADR-012). Skia's raster backend gives antialiased,
subpixel-positioned geometry and text.
"""

from __future__ import annotations

import contextlib
import functools
import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
import skia

from engine.core.color import RGBA, to_argb_int
from src.common.paths import ASSETS_DIR

FONT_DIR = ASSETS_DIR / "fonts"

# Family name -> font file. Variable fonts accept any weight on their wght axis.
FONT_FILES: dict[str, str] = {
    "Inter": "Inter[opsz,wght].ttf",
    "Fraunces": "Fraunces[SOFT,WONK,opsz,wght].ttf",
    "Space Grotesk": "SpaceGrotesk[wght].ttf",
    "JetBrains Mono": "JetBrainsMono[wght].ttf",
    "Anton": "Anton-Regular.ttf",
}


def _tag(s: str) -> int:
    return int.from_bytes(s.encode("ascii"), "big")


@dataclass(frozen=True)
class FontSpec:
    """A font request: family, weight (100-900), size in px, optional extra axes."""

    family: str
    size: float
    weight: int = 400
    axes: tuple[tuple[str, float], ...] = ()


class Fonts:
    """Loads typefaces from ``assets/fonts`` and caches weight/axis instances."""

    def __init__(self, font_dir: Path = FONT_DIR) -> None:
        self._dir = font_dir
        self._base: dict[str, skia.Typeface] = {}

    @functools.lru_cache(maxsize=256)  # noqa: B019 - one Fonts per worker; bounded cache
    def typeface(self, family: str, weight: int, axes: tuple[tuple[str, float], ...] = ()) -> skia.Typeface:
        """Return a typeface instance for family/weight/axes.

        Raises:
            KeyError: Unknown family. FileNotFoundError: font not downloaded (run fetch-assets).
        """
        if family not in FONT_FILES:
            raise KeyError(f"unknown font family: {family}")
        base = self._base.get(family)
        if base is None:
            path = self._dir / FONT_FILES[family]
            if not path.is_file():
                raise FileNotFoundError(f"font missing: {path.name} (run fetch-assets)")
            base = skia.Typeface.MakeFromFile(str(path))
            if base is None:
                raise FileNotFoundError(f"font unreadable: {path.name}")
            self._base[family] = base
        params = {ax.tag: (ax.min, ax.max) for ax in base.getVariationDesignParameters()}
        wanted: dict[int, float] = {}
        if _tag("wght") in params:
            lo, hi = params[_tag("wght")]
            wanted[_tag("wght")] = float(min(max(weight, lo), hi))
        for name, value in axes:
            t = _tag(name)
            if t in params:
                lo, hi = params[t]
                wanted[t] = float(min(max(value, lo), hi))
        if not wanted:
            return base
        vp = skia.FontArguments.VariationPosition
        coords = vp.Coordinates([vp.Coordinate(t, v) for t, v in wanted.items()])
        args = skia.FontArguments()
        args.setVariationDesignPosition(vp(coords))
        return base.makeClone(args)

    def font(self, spec: FontSpec) -> skia.Font:
        """Return a skia Font with subpixel positioning and antialiasing enabled."""
        f = skia.Font(self.typeface(spec.family, spec.weight, spec.axes), spec.size)
        f.setSubpixel(True)
        f.setEdging(skia.Font.Edging.kAntiAlias)
        f.setHinting(skia.FontHinting.kNone)
        f.setLinearMetrics(True)
        return f

    def measure(self, text: str, spec: FontSpec, letter_spacing: float = 0.0) -> float:
        """Advance width of ``text`` in px (including letter spacing between glyphs)."""
        w = float(self.font(spec).measureText(text))
        if letter_spacing and len(text) > 1:
            w += letter_spacing * (len(text) - 1)
        return w

    def metrics(self, spec: FontSpec) -> tuple[float, float, float]:
        """(ascent, descent, cap_height) in px; ascent is positive upward."""
        m = self.font(spec).getMetrics()
        cap = m.fCapHeight if m.fCapHeight > 0 else -m.fAscent * 0.7
        return (-m.fAscent, m.fDescent, cap)


class PathBuilder:
    """Vector path under construction (thin wrapper so components stay backend-agnostic)."""

    def __init__(self) -> None:
        self._p = skia.Path()

    def move_to(self, x: float, y: float) -> PathBuilder:
        """Start a new contour."""
        self._p.moveTo(x, y)
        return self

    def line_to(self, x: float, y: float) -> PathBuilder:
        """Straight segment."""
        self._p.lineTo(x, y)
        return self

    def quad_to(self, cx: float, cy: float, x: float, y: float) -> PathBuilder:
        """Quadratic Bézier segment."""
        self._p.quadTo(cx, cy, x, y)
        return self

    def cubic_to(self, c1x: float, c1y: float, c2x: float, c2y: float, x: float, y: float) -> PathBuilder:
        """Cubic Bézier segment."""
        self._p.cubicTo(c1x, c1y, c2x, c2y, x, y)
        return self

    def arc(self, cx: float, cy: float, r: float, start_deg: float, sweep_deg: float) -> PathBuilder:
        """Append a circular arc."""
        self._p.addArc(skia.Rect.MakeLTRB(cx - r, cy - r, cx + r, cy + r), start_deg, sweep_deg)
        return self

    def close(self) -> PathBuilder:
        """Close the current contour."""
        self._p.close()
        return self

    def polyline(self, pts: Sequence[tuple[float, float]], closed: bool = False) -> PathBuilder:
        """Append a polyline contour."""
        if pts:
            self._p.moveTo(*pts[0])
            for x, y in pts[1:]:
                self._p.lineTo(x, y)
            if closed:
                self._p.close()
        return self

    def rings(self, arrays: Sequence[npt.NDArray[np.float64]], closed: bool = True) -> PathBuilder:
        """Append many polylines/polygons given as (n, 2) arrays (fast path for map geometry)."""
        for a in arrays:
            if len(a) >= 2:
                self._p.addPoly([skia.Point(x, y) for x, y in a.tolist()], closed)
        return self

    def smooth(self, pts: Sequence[tuple[float, float]], tension: float = 0.5) -> PathBuilder:
        """Append a Catmull-Rom spline through ``pts`` (converted to cubic Béziers)."""
        n = len(pts)
        if n < 3:
            return self.polyline(pts)
        self._p.moveTo(*pts[0])
        k = tension / 3.0 * 2.0
        for i in range(n - 1):
            p0 = pts[i - 1] if i > 0 else pts[i]
            p1, p2 = pts[i], pts[i + 1]
            p3 = pts[i + 2] if i + 2 < n else p2
            c1 = (p1[0] + (p2[0] - p0[0]) * k / 2, p1[1] + (p2[1] - p0[1]) * k / 2)
            c2 = (p2[0] - (p3[0] - p1[0]) * k / 2, p2[1] - (p3[1] - p1[1]) * k / 2)
            self._p.cubicTo(c1[0], c1[1], c2[0], c2[1], p2[0], p2[1])
        return self

    def length(self) -> float:
        """Total contour length in px."""
        meas = skia.PathMeasure(self._p, False)
        total = 0.0
        while True:
            total += float(meas.getLength())
            if not meas.nextContour():
                break
        return total

    def point_at(self, frac: float) -> tuple[float, float, float]:
        """(x, y, tangent angle in degrees) at fraction ``frac`` of the first contour."""
        meas = skia.PathMeasure(self._p, False)
        length = meas.getLength()
        if length <= 0:
            return (0.0, 0.0, 0.0)
        res = meas.getPosTan(max(0.0, min(1.0, frac)) * length)
        pos, tan = res[-2], res[-1]  # skia-python returns (pos, tan); some builds prefix a bool
        return (float(pos.x()), float(pos.y()), math.degrees(math.atan2(tan.y(), tan.x())))

    def bounds(self) -> tuple[float, float, float, float]:
        """(left, top, right, bottom) of the path's control bounds."""
        r = self._p.computeTightBounds()
        return (r.left(), r.top(), r.right(), r.bottom())

    @property
    def native(self) -> skia.Path:
        """Backend path object (only for use inside engine/core)."""
        return self._p


class Picture:
    """An immutable raster image (e.g. a cached static layer or an offscreen render)."""

    def __init__(self, image: skia.Image) -> None:
        self._img = image

    @property
    def width(self) -> int:
        """Pixel width."""
        return int(self._img.width())

    @property
    def height(self) -> int:
        """Pixel height."""
        return int(self._img.height())

    def to_png(self) -> bytes:
        """PNG-encoded bytes."""
        data = self._img.encodeToData()
        return bytes(data.bytes())

    def to_array(self) -> npt.NDArray[np.uint8]:
        """RGBA uint8 array of shape (h, w, 4)."""
        return np.asarray(self._img.toarray(colorType=skia.kRGBA_8888_ColorType), dtype=np.uint8)

    @classmethod
    def from_png(cls, data: bytes) -> Picture:
        """Decode PNG/JPEG bytes."""
        img = skia.Image.MakeFromEncoded(skia.Data.MakeWithCopy(data))
        if img is None:
            raise ValueError("could not decode image")
        return cls(img)

    def resized(self, width: int, height: int) -> Picture:
        """High-quality (mipmapped) downscale/upscale to (width, height)."""
        surf = skia.Surface(width, height)
        c = surf.getCanvas()
        sampling = skia.SamplingOptions(skia.FilterMode.kLinear, skia.MipmapMode.kLinear)
        c.drawImageRect(self._img.withDefaultMipmaps(), skia.Rect.MakeWH(width, height), sampling)
        return Picture(surf.makeImageSnapshot())

    @classmethod
    def from_array(cls, arr: npt.NDArray[np.uint8]) -> Picture:
        """Wrap an RGBA uint8 array (h, w, 4)."""
        return cls(skia.Image.fromarray(np.ascontiguousarray(arr), colorType=skia.kRGBA_8888_ColorType))

    @property
    def native(self) -> skia.Image:
        """Backend image (only for use inside engine/core)."""
        return self._img


def _paint(
    color: RGBA | None = None,
    *,
    stroke: bool = False,
    width: float = 1.0,
    cap: str = "round",
    join: str = "round",
    alpha: float = 1.0,
    blur: float = 0.0,
    shader: skia.Shader | None = None,
    blend: str | None = None,
) -> skia.Paint:
    p = skia.Paint()
    p.setAntiAlias(True)
    if color is not None:
        p.setColor(to_argb_int((color[0], color[1], color[2], color[3] * alpha)))
    elif alpha < 1.0:
        p.setAlphaf(alpha)
    if shader is not None:
        p.setShader(shader)
        if alpha < 1.0:
            p.setAlphaf(alpha)
    if stroke:
        p.setStyle(skia.Paint.kStroke_Style)
        p.setStrokeWidth(width)
        p.setStrokeCap({"butt": skia.Paint.kButt_Cap, "square": skia.Paint.kSquare_Cap}.get(cap, skia.Paint.kRound_Cap))
        p.setStrokeJoin(
            {"miter": skia.Paint.kMiter_Join, "bevel": skia.Paint.kBevel_Join}.get(join, skia.Paint.kRound_Join)
        )
    if blur > 0:
        p.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, blur))
    if blend:
        p.setBlendMode(
            {
                "screen": skia.BlendMode.kScreen,
                "multiply": skia.BlendMode.kMultiply,
                "add": skia.BlendMode.kPlus,
                "overlay": skia.BlendMode.kOverlay,
            }[blend]
        )
    return p


@dataclass(frozen=True)
class TextRecord:
    """A text draw captured for quality checks (device-space box, effective opacity)."""

    text: str
    role: str  # "caption" or "content"
    box: tuple[float, float, float, float]  # left, top, right, bottom in device pixels
    color: RGBA
    opacity: float
    size_px: float


class Canvas:
    """Immediate-mode drawing API used by every component."""

    def __init__(self, sk: skia.Canvas, width: int, height: int, fonts: Fonts) -> None:
        self._c = sk
        self.width = width
        self.height = height
        self.fonts = fonts
        # Quality-check instrumentation (off in normal renders): see src/quality/visual.py.
        self.recorder: list[TextRecord] | None = None
        self.record_role = "content"
        self.suppress_text = False
        self._opacity = 1.0

    # --- state ------------------------------------------------------------------------------
    @contextlib.contextmanager
    def saved(self) -> Iterator[Canvas]:
        """Save/restore transform and clip around a block."""
        self._c.save()
        try:
            yield self
        finally:
            self._c.restore()

    @contextlib.contextmanager
    def layer(self, alpha: float = 1.0, blur: float = 0.0, blend: str | None = None) -> Iterator[Canvas]:
        """Draw a block into an offscreen layer composited with ``alpha``/``blur``/``blend``."""
        prev_opacity = self._opacity
        self._opacity = prev_opacity * max(0.0, min(1.0, alpha))
        if alpha >= 0.999 and blur <= 0 and blend is None:
            try:
                yield self
            finally:
                self._opacity = prev_opacity
            return
        p = skia.Paint()
        p.setAlphaf(max(0.0, min(1.0, alpha)))
        if blur > 0:
            p.setImageFilter(skia.ImageFilters.Blur(blur, blur))
        if blend:
            p.setBlendMode(_paint(blend=blend).getBlendMode())
        self._c.saveLayer(None, p)
        try:
            yield self
        finally:
            self._c.restore()
            self._opacity = prev_opacity

    def translate(self, dx: float, dy: float) -> None:
        """Translate subsequent drawing."""
        self._c.translate(dx, dy)

    def scale(self, sx: float, sy: float | None = None) -> None:
        """Scale subsequent drawing."""
        self._c.scale(sx, sx if sy is None else sy)

    def rotate(self, degrees: float, cx: float = 0.0, cy: float = 0.0) -> None:
        """Rotate subsequent drawing about (cx, cy)."""
        self._c.rotate(degrees, cx, cy)

    def skew(self, sx: float, sy: float = 0.0) -> None:
        """Skew subsequent drawing."""
        self._c.skew(sx, sy)

    def clip_rect(self, x: float, y: float, w: float, h: float) -> None:
        """Intersect the clip with a rectangle (antialiased)."""
        self._c.clipRect(skia.Rect.MakeXYWH(x, y, w, h), skia.ClipOp.kIntersect, True)

    def clip_path(self, path: PathBuilder) -> None:
        """Intersect the clip with a path (antialiased)."""
        self._c.clipPath(path.native, skia.ClipOp.kIntersect, True)

    # --- primitives -------------------------------------------------------------------------
    def clear(self, color: RGBA) -> None:
        """Fill the whole surface with ``color`` (replaces pixels)."""
        self._c.clear(to_argb_int(color))

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        fill: RGBA | None = None,
        stroke: RGBA | None = None,
        stroke_width: float = 2.0,
        radius: float = 0.0,
        alpha: float = 1.0,
        blur: float = 0.0,
    ) -> None:
        """Filled and/or stroked (rounded) rectangle."""
        r = skia.Rect.MakeXYWH(x, y, w, h)
        for color, is_stroke in ((fill, False), (stroke, True)):
            if color is None:
                continue
            p = _paint(color, stroke=is_stroke, width=stroke_width, alpha=alpha, blur=blur)
            if radius > 0:
                self._c.drawRoundRect(r, radius, radius, p)
            else:
                self._c.drawRect(r, p)

    def circle(
        self,
        cx: float,
        cy: float,
        r: float,
        *,
        fill: RGBA | None = None,
        stroke: RGBA | None = None,
        stroke_width: float = 2.0,
        alpha: float = 1.0,
        blur: float = 0.0,
    ) -> None:
        """Filled and/or stroked circle."""
        for color, is_stroke in ((fill, False), (stroke, True)):
            if color is not None:
                self._c.drawCircle(
                    cx,
                    cy,
                    r,
                    _paint(color, stroke=is_stroke, width=stroke_width, alpha=alpha, blur=blur),
                )

    def line(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        color: RGBA,
        width: float = 2.0,
        *,
        cap: str = "round",
        alpha: float = 1.0,
    ) -> None:
        """Straight stroked line."""
        self._c.drawLine(x0, y0, x1, y1, _paint(color, stroke=True, width=width, cap=cap, alpha=alpha))

    def path(
        self,
        path: PathBuilder,
        *,
        fill: RGBA | None = None,
        stroke: RGBA | None = None,
        stroke_width: float = 2.0,
        trim: tuple[float, float] = (0.0, 1.0),
        alpha: float = 1.0,
        blur: float = 0.0,
        dash: tuple[float, float] | None = None,
        cap: str = "round",
    ) -> None:
        """Fill and/or stroke a path; ``trim`` draws only a fraction (for path-draw animations)."""
        if fill is not None:
            self._c.drawPath(path.native, _paint(fill, alpha=alpha, blur=blur))
        if stroke is not None:
            p = _paint(stroke, stroke=True, width=stroke_width, alpha=alpha, blur=blur, cap=cap)
            t0, t1 = max(0.0, trim[0]), min(1.0, trim[1])
            if t1 <= t0:
                return
            if (t0, t1) != (0.0, 1.0):
                p.setPathEffect(skia.TrimPathEffect.Make(t0, t1))
            if dash is not None:
                dpe = skia.DashPathEffect.Make(list(dash), 0.0)
                cur = p.getPathEffect()
                p.setPathEffect(skia.PathEffect.MakeCompose(dpe, cur) if cur else dpe)
            self._c.drawPath(path.native, p)

    def gradient_rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        colors: Sequence[RGBA],
        *,
        angle_deg: float = 90.0,
        stops: Sequence[float] | None = None,
        radius: float = 0.0,
        alpha: float = 1.0,
    ) -> None:
        """Rectangle filled with a linear gradient at ``angle_deg`` (90 = top→bottom)."""
        cx, cy = x + w / 2, y + h / 2
        a = math.radians(angle_deg)
        half = (abs(w * math.cos(a)) + abs(h * math.sin(a))) / 2
        p0 = (cx - math.cos(a) * half, cy - math.sin(a) * half)
        p1 = (cx + math.cos(a) * half, cy + math.sin(a) * half)
        shader = skia.GradientShader.MakeLinear(
            [p0, p1], [to_argb_int(c) for c in colors], list(stops) if stops else None
        )
        p = _paint(None, shader=shader, alpha=alpha)
        r = skia.Rect.MakeXYWH(x, y, w, h)
        if radius > 0:
            self._c.drawRoundRect(r, radius, radius, p)
        else:
            self._c.drawRect(r, p)

    def radial_glow(
        self,
        cx: float,
        cy: float,
        r: float,
        color: RGBA,
        *,
        alpha: float = 1.0,
        blend: str | None = None,
    ) -> None:
        """Soft radial falloff from ``color`` to transparent (vignettes, light pools, glows)."""
        c0 = to_argb_int(color)
        c1 = to_argb_int((color[0], color[1], color[2], 0.0))
        shader = skia.GradientShader.MakeRadial((cx, cy), max(r, 1.0), [c0, c1])
        p = _paint(None, shader=shader, alpha=alpha, blend=blend)
        self._c.drawCircle(cx, cy, r, p)

    def picture(
        self,
        pic: Picture,
        x: float,
        y: float,
        w: float | None = None,
        h: float | None = None,
        *,
        alpha: float = 1.0,
        src: tuple[float, float, float, float] | None = None,
        channels: tuple[float, float, float] | None = None,
        blend: str | None = None,
        blur: float = 0.0,
    ) -> None:
        """Draw a :class:`Picture` (optionally a ``src`` sub-rect) scaled into (x, y, w, h).

        ``channels`` multiplies RGB (e.g. ``(1, 0, 0)`` keeps only red, for RGB-split effects).
        """
        p = _paint(None, alpha=alpha, blend=blend)
        if channels is not None:
            r, g, b = channels
            p.setColorFilter(skia.ColorFilters.Matrix([r, 0, 0, 0, 0, 0, g, 0, 0, 0, 0, 0, b, 0, 0, 0, 0, 0, 1, 0]))
        if blur > 0:
            p.setImageFilter(skia.ImageFilters.Blur(blur, blur))
        sampling = skia.SamplingOptions(skia.FilterMode.kLinear, skia.MipmapMode.kNone)
        dst = skia.Rect.MakeXYWH(x, y, w if w is not None else pic.width, h if h is not None else pic.height)
        if src is not None:
            self._c.drawImageRect(pic.native, skia.Rect.MakeXYWH(*src), dst, sampling, p)
        else:
            self._c.drawImageRect(pic.native, dst, sampling, p)

    # --- text -------------------------------------------------------------------------------
    def text(
        self,
        s: str,
        x: float,
        y: float,
        font: FontSpec,
        color: RGBA,
        *,
        align: str = "left",
        baseline: str = "alphabetic",
        letter_spacing: float = 0.0,
        alpha: float = 1.0,
        stroke: RGBA | None = None,
        stroke_width: float = 0.0,
        shadow: tuple[float, float, float, RGBA] | None = None,
    ) -> float:
        """Draw a single line of text; returns its advance width.

        Args:
            align: ``left`` | ``center`` | ``right`` relative to ``x``.
            baseline: ``alphabetic`` | ``middle`` (cap-height centred) | ``top`` (cap top at ``y``).
            stroke: Optional outline drawn under the fill (caption legibility).
            shadow: ``(dx, dy, blur_sigma, color)`` soft drop shadow.
        """
        f = self.fonts.font(font)
        width = self.fonts.measure(s, font, letter_spacing)
        if align == "center":
            x -= width / 2
        elif align == "right":
            x -= width
        _, _, cap = self.fonts.metrics(font)
        if baseline == "middle":
            y += cap / 2
        elif baseline == "top":
            y += cap
        if self.recorder is not None and s.strip():
            _, descent, cap_h = self.fonts.metrics(font)
            m = self._c.getTotalMatrix()
            r = m.mapRect(skia.Rect.MakeLTRB(x, y - cap_h, x + width, y + descent * 0.35))
            self.recorder.append(
                TextRecord(s, self.record_role, (r.left(), r.top(), r.right(), r.bottom()), color,
                           alpha * self._opacity * color[3], font.size * abs(m.getScaleX()))
            )  # fmt: skip
        if self.suppress_text:
            return width
        blob = self._blob(s, f, letter_spacing)
        if blob is None:
            return width
        if shadow is not None:
            dx, dy, sigma, scol = shadow
            sp = _paint(scol, alpha=alpha, blur=sigma)
            self._c.drawTextBlob(blob, x + dx, y + dy, sp)
            if stroke is not None and stroke_width > 0:
                self._c.drawTextBlob(
                    blob,
                    x + dx,
                    y + dy,
                    _paint(scol, stroke=True, width=stroke_width, alpha=alpha, blur=sigma),
                )
        if stroke is not None and stroke_width > 0:
            self._c.drawTextBlob(blob, x, y, _paint(stroke, stroke=True, width=stroke_width, alpha=alpha))
        self._c.drawTextBlob(blob, x, y, _paint(color, alpha=alpha))
        return width

    @staticmethod
    def _blob(s: str, f: skia.Font, letter_spacing: float) -> skia.TextBlob | None:
        if not s:
            return None
        if not letter_spacing:
            return skia.TextBlob.MakeFromString(s, f)
        glyphs = f.textToGlyphs(s)
        widths = f.getWidths(glyphs)
        xs: list[float] = []
        acc = 0.0
        for w in widths:
            xs.append(acc)
            acc += w + letter_spacing
        builder = skia.TextBlobBuilder()
        builder.allocRunPosH(f, glyphs, xs, 0.0)
        return builder.make()


class Surface:
    """An RGBA raster render target backed by a numpy array (zero-copy into FFmpeg)."""

    def __init__(self, width: int, height: int, fonts: Fonts) -> None:
        self.width = width
        self.height = height
        self.array: npt.NDArray[np.uint8] = np.zeros((height, width, 4), dtype=np.uint8)
        self._s = skia.Surface(self.array, colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kPremul_AlphaType)
        self.canvas = Canvas(self._s.getCanvas(), width, height, fonts)

    def snapshot(self) -> Picture:
        """Immutable copy of the current pixels."""
        return Picture(self._s.makeImageSnapshot())
