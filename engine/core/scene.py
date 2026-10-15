"""Scene graph: nodes with animated transforms, scenes, and the full composition timeline.

Timing model
------------
* A :class:`Composition` has scenes with explicit ``start`` times (aligned to narration) and
  global ``overlays`` (captions, progress bar) drawn on top in global time.
* A scene stays on screen until the next scene's transition finishes, so its content never pops
  off. The transition *into* scene B runs over ``[B.start, B.start + transition.duration]`` while
  B's entrance animations play, so the reveal and the entrances read as one gesture.
* Nodes receive *local* time (seconds since their scene/parent started).

Determinism: all randomness comes from :meth:`RenderContext.rng`, seeded from the composition seed
and a stable key, so every frame is reproducible from (spec, theme, seed).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import numpy.typing as npt

from engine.core.canvas import Canvas, Fonts, Picture, Surface
from engine.core.theme import Theme
from engine.core.timeline import Track

Layout = Literal["short", "long"]


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle in pixels."""

    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        """Centre x."""
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        """Centre y."""
        return self.y + self.h / 2

    @property
    def right(self) -> float:
        """Right edge."""
        return self.x + self.w

    @property
    def bottom(self) -> float:
        """Bottom edge."""
        return self.y + self.h

    def inset(self, dx: float, dy: float | None = None) -> Rect:
        """Shrink by ``dx``/``dy`` on each side."""
        dy = dx if dy is None else dy
        return Rect(self.x + dx, self.y + dy, self.w - 2 * dx, self.h - 2 * dy)

    def sub(self, fx: float, fy: float, fw: float, fh: float) -> Rect:
        """Sub-rectangle from fractions of this rect."""
        return Rect(self.x + fx * self.w, self.y + fy * self.h, fw * self.w, fh * self.h)


@dataclass
class RenderContext:
    """Everything a node needs besides the canvas and time."""

    width: int
    height: int
    fps: int
    layout: Layout
    theme: Theme
    fonts: Fonts
    seed: int
    safe: Rect
    duration: float = 0.0
    frame_index: int = 0
    cache: dict[str, Any] = field(default_factory=dict)  # per-worker caches (pictures, paths)

    @property
    def unit(self) -> float:
        """Scale factor: 1.0 when the frame's short side is 1080 px."""
        return min(self.width, self.height) / 1080.0

    def rng(self, key: str) -> np.random.Generator:
        """Deterministic RNG for ``key`` (independent of frame order and worker)."""
        h = hashlib.sha256(f"{self.seed}:{key}".encode()).digest()
        return np.random.default_rng(int.from_bytes(h[:8], "big"))


TRANSFORM_PROPS = ("x", "y", "scale", "rotation", "opacity")


class Node:
    """Base scene-graph node: timing, animated transform, opacity.

    Subclasses implement :meth:`render` in their own local coordinates and local time.
    """

    def __init__(
        self,
        *,
        start: float = 0.0,
        end: float | None = None,
        x: float = 0.0,
        y: float = 0.0,
        scale: float = 1.0,
        rotation: float = 0.0,
        opacity: float = 1.0,
        tracks: dict[str, Track] | None = None,
        key: str = "",
    ) -> None:
        self.start = start
        self.end = end
        self.base = {"x": x, "y": y, "scale": scale, "rotation": rotation, "opacity": opacity}
        self.tracks: dict[str, Track] = dict(tracks or {})
        self.key = key or type(self).__name__

    def prop(self, name: str, t: float) -> float:
        """Animated scalar property at local time ``t`` (track overrides base)."""
        tr = self.tracks.get(name)
        return tr.scalar_at(t) if tr is not None else self.base.get(name, 0.0)

    def active(self, t: float) -> bool:
        """Whether the node is visible at parent-local time ``t``."""
        return t >= self.start and (self.end is None or t < self.end)

    def draw(self, cv: Canvas, t: float, ctx: RenderContext) -> None:
        """Apply transform/opacity and render at local time ``t - start``."""
        if not self.active(t):
            return
        lt = t - self.start
        opacity = self.prop("opacity", lt)
        if opacity <= 0.002:
            return
        x, y = self.prop("x", lt), self.prop("y", lt)
        s, r = self.prop("scale", lt), self.prop("rotation", lt)
        with cv.saved():
            if x or y:
                cv.translate(x, y)
            if r:
                cv.rotate(r)
            if s != 1.0:
                if abs(s) < 1e-4:
                    return
                cv.scale(s)
            with cv.layer(alpha=opacity):
                self.render(cv, lt, ctx)

    def render(self, cv: Canvas, t: float, ctx: RenderContext) -> None:
        """Draw content in local space. Override in subclasses."""

    def duration_hint(self) -> float:
        """Latest local time at which this node still animates (for static-frame checks)."""
        ends = [tr.end_time for tr in self.tracks.values()]
        return max(ends, default=0.0)


class Group(Node):
    """A node that draws children in its own transformed space."""

    def __init__(self, children: Sequence[Node] = (), **kw: object) -> None:
        super().__init__(**kw)  # type: ignore[arg-type]
        self.children = list(children)

    def render(self, cv: Canvas, t: float, ctx: RenderContext) -> None:
        """Draw children in order."""
        for child in self.children:
            child.draw(cv, t, ctx)

    def duration_hint(self) -> float:
        """Max of own tracks and children's hints (offset by their start)."""
        own = super().duration_hint()
        kids = [c.start + c.duration_hint() for c in self.children]
        return max([own, *kids])


class FnNode(Node):
    """Node whose rendering is a plain function ``fn(cv, t, ctx)``; handy for one-off visuals."""

    def __init__(self, fn: Callable[[Canvas, float, RenderContext], None], **kw: object) -> None:
        super().__init__(**kw)  # type: ignore[arg-type]
        self.fn = fn

    def render(self, cv: Canvas, t: float, ctx: RenderContext) -> None:
        """Call the wrapped function."""
        self.fn(cv, t, ctx)


class CachedLayer(Node):
    """Renders a static child once per worker into a full-frame picture, then blits it.

    Use for backgrounds and other layers whose pixels never change within a scene.
    """

    def __init__(self, child: Node, cache_key: str, **kw: object) -> None:
        super().__init__(**kw)  # type: ignore[arg-type]
        self.child = child
        self.cache_key = cache_key

    def render(self, cv: Canvas, t: float, ctx: RenderContext) -> None:
        """Blit the cached picture, rendering it on first use."""
        pic = ctx.cache.get(self.cache_key)
        if pic is None:
            surf = Surface(ctx.width, ctx.height, ctx.fonts)
            self.child.draw(surf.canvas, 0.0, ctx)
            pic = surf.snapshot()
            ctx.cache[self.cache_key] = pic
        cv.picture(pic, 0, 0)


@dataclass
class TransitionSpec:
    """How the composition moves into a scene."""

    kind: str
    duration: float
    easing: str = "in_out_expo"


@dataclass
class Scene:
    """A shot: background + content nodes, starting at ``start`` seconds (global)."""

    start: float
    duration: float
    nodes: list[Node]
    background: Node | None = None
    transition_in: TransitionSpec | None = None
    camera_drift: float = 0.0
    key: str = "scene"

    def draw(self, cv: Canvas, t_local: float, ctx: RenderContext, hold_until: float) -> None:
        """Draw the scene at local time, with a slow camera push-in for life on static shots."""
        if self.background is not None:
            self.background.draw(cv, t_local, ctx)
        if self.camera_drift > 0 and hold_until > 0:
            u = min(max(t_local / hold_until, 0.0), 1.0)
            s = 1.0 + self.camera_drift * (u * u * (3 - 2 * u))
            with cv.saved():
                cv.translate(ctx.width / 2, ctx.height / 2)
                cv.scale(s)
                cv.translate(-ctx.width / 2, -ctx.height / 2)
                for n in self.nodes:
                    n.draw(cv, t_local, ctx)
        else:
            for n in self.nodes:
                n.draw(cv, t_local, ctx)


TransitionFn = Callable[[Canvas, Picture, Picture, float, RenderContext, int], None]
_TRANSITIONS: dict[str, TransitionFn] = {}


def register_transition(name: str) -> Callable[[TransitionFn], TransitionFn]:
    """Decorator registering a transition compositor ``fn(cv, from_pic, to_pic, u, ctx, seed)``."""

    def deco(fn: TransitionFn) -> TransitionFn:
        _TRANSITIONS[name] = fn
        return fn

    return deco


def get_transition(name: str) -> TransitionFn:
    """Look up a registered transition (importing the component library on first use)."""
    if not _TRANSITIONS:
        import engine.components.transitions  # noqa: F401  # registers built-ins
    if name not in _TRANSITIONS:
        raise KeyError(f"unknown transition: {name}")
    return _TRANSITIONS[name]


class Composition:
    """The full video timeline."""

    def __init__(
        self,
        *,
        width: int,
        height: int,
        fps: int,
        duration: float,
        scenes: Sequence[Scene],
        overlays: Sequence[Node] = (),
        layout: Layout = "short",
        motion_blur: int = 1,
    ) -> None:
        self.width, self.height, self.fps = width, height, fps
        self.duration = duration
        self.scenes = sorted(scenes, key=lambda s: s.start)
        self.overlays = list(overlays)
        self.layout: Layout = layout
        self.motion_blur = max(1, motion_blur)
        self._buffers: tuple[Surface, Surface] | None = None

    @property
    def frame_count(self) -> int:
        """Total frames."""
        return round(self.duration * self.fps)

    def _scene_index(self, t: float) -> int:
        idx = 0
        for i, s in enumerate(self.scenes):
            if s.start <= t:
                idx = i
        return idx

    def _hold(self, i: int) -> float:
        nxt = self.scenes[i + 1].start if i + 1 < len(self.scenes) else self.duration
        return max(nxt - self.scenes[i].start, 0.0)

    def draw(self, cv: Canvas, t: float, ctx: RenderContext) -> None:
        """Draw the composition at global time ``t``."""
        from engine.core.easing import get_easing

        i = self._scene_index(t)
        scene = self.scenes[i]
        tr = scene.transition_in
        in_transition = i > 0 and tr is not None and t < scene.start + tr.duration
        if in_transition and tr is not None:
            prev = self.scenes[i - 1]
            if self._buffers is None:
                self._buffers = (
                    Surface(ctx.width, ctx.height, ctx.fonts),
                    Surface(ctx.width, ctx.height, ctx.fonts),
                )
            a, b = self._buffers
            a.canvas.clear((0, 0, 0, 1))
            prev.draw(a.canvas, t - prev.start, ctx, self._hold(i - 1))
            b.canvas.clear((0, 0, 0, 1))
            scene.draw(b.canvas, t - scene.start, ctx, self._hold(i))
            u = get_easing(tr.easing)((t - scene.start) / tr.duration)
            get_transition(tr.kind)(cv, a.snapshot(), b.snapshot(), u, ctx, i)
        else:
            scene.draw(cv, t - scene.start, ctx, self._hold(i))
        for ov in self.overlays:
            ov.draw(cv, t, ctx)

    def scene_boundaries(self) -> list[float]:
        """Global start times of every scene (for key-frame extraction and checks)."""
        return [s.start for s in self.scenes]


@dataclass
class FrameRenderer:
    """Renders frames of a composition into a reusable RGBA buffer (one per worker)."""

    comp: Composition
    ctx: RenderContext
    surface: Surface = field(init=False)

    def __post_init__(self) -> None:
        self.surface = Surface(self.comp.width, self.comp.height, self.ctx.fonts)

    def render(self, frame: int) -> npt.NDArray[np.uint8]:
        """Render frame index ``frame``; returns the (h, w, 4) uint8 buffer (reused!)."""
        self.ctx.frame_index = frame
        n = self.comp.motion_blur
        if n == 1:
            self.surface.canvas.clear((0, 0, 0, 1))
            self.comp.draw(self.surface.canvas, frame / self.comp.fps, self.ctx)
            return self.surface.array
        # Motion blur: average n sub-frames across a 180-degree shutter.
        acc = np.zeros(self.surface.array.shape, dtype=np.float32)
        for k in range(n):
            t = (frame + 0.5 * k / n) / self.comp.fps
            self.surface.canvas.clear((0, 0, 0, 1))
            self.comp.draw(self.surface.canvas, t, self.ctx)
            acc += self.surface.array
        self.surface.array[...] = np.clip(acc / n + 0.5, 0, 255).astype(np.uint8)
        return self.surface.array
