"""Build a renderable :class:`Composition` from a validated scene spec.

``build_from_spec(spec_json, scale)`` is the module-level builder used by the parallel renderer:
it takes only plain arguments (the spec as a JSON string), so every worker re-validates and
rebuilds the same composition deterministically.
"""

from __future__ import annotations

from collections.abc import Callable

from engine.components import extra
from engine.components.background import ThemedBackground
from engine.components.captions import WordCaptions
from engine.components.kinetic_title import KineticTitle
from engine.components.list_reveal import ListReveal
from engine.components.quote_card import QuoteCard
from engine.components.stat_counter import StatCounter
from engine.core.canvas import Canvas
from engine.core.layout import make_context
from engine.core.scene import Composition, FnNode, Node, RenderContext, Scene, TransitionSpec
from engine.core.theme import Theme
from engine.schema.spec import ElementSpec, SceneSpec, SceneSpecDocument, validate_spec

ComponentFactory = Callable[[ElementSpec, RenderContext], Node]


def _registry() -> dict[str, ComponentFactory]:
    reg: dict[str, ComponentFactory] = {
        "KineticTitle": KineticTitle,  # type: ignore[dict-item]
        "StatCounter": StatCounter,  # type: ignore[dict-item]
        "ListReveal": ListReveal,  # type: ignore[dict-item]
        "QuoteCard": QuoteCard,  # type: ignore[dict-item]
    }
    reg.update(extra.REGISTRY)
    return reg


def _transition(scene: SceneSpec, theme: Theme) -> TransitionSpec | None:
    tr = scene.transition
    if tr is None:
        return None
    if tr == "theme":
        ts = theme.transition
        return TransitionSpec(ts.kind, ts.duration, ts.easing)
    if tr.kind == "cut":
        return None
    return TransitionSpec(tr.kind, tr.duration, tr.easing)


def _progress_bar(cv: Canvas, t: float, ctx: RenderContext) -> None:
    u = ctx.unit
    frac = min(max(t / ctx.duration, 0.0), 1.0) if ctx.duration else 0.0
    y = ctx.safe.y - 18 * u
    x0, w = ctx.safe.x, ctx.safe.w
    cv.rect(x0, y, w, 6 * u, fill=(1, 1, 1, 0.12), radius=3 * u)
    cv.rect(x0, y, w * frac, 6 * u, fill=ctx.theme.color("accent"), radius=3 * u)


def compose(doc: SceneSpecDocument, *, scale: float = 1.0) -> tuple[Composition, RenderContext]:
    """Turn a validated spec into (Composition, RenderContext)."""
    ctx = make_context(doc.theme, doc.format, fps=doc.fps, seed=doc.seed, scale=scale, duration=doc.duration)
    theme = ctx.theme
    reg = _registry()
    scenes: list[Scene] = []
    for i, sc in enumerate(doc.scenes):
        nxt = doc.scenes[i + 1].start if i + 1 < len(doc.scenes) else doc.duration
        nodes: list[Node] = []
        for el in sc.elements:
            factory = reg.get(el.type)
            if factory is None:
                raise NotImplementedError(f"component not available: {el.type}")
            nodes.append(factory(el, ctx))
        scenes.append(
            Scene(
                start=sc.start,
                duration=nxt - sc.start,
                nodes=nodes,
                background=ThemedBackground(sc.id, glow_positions=_glows(i)),
                transition_in=_transition(sc, theme) if i > 0 else None,
                # overrides may only reduce drift: regions are computed so the theme drift stays in the safe zone
                camera_drift=min(theme.motion.camera_drift, sc.camera_drift if sc.camera_drift is not None else 1.0),
                key=sc.id,
            )
        )
    overlays: list[Node] = []
    if doc.captions.enabled and doc.captions.words:
        overlays.append(WordCaptions(list(doc.captions.words), ctx, doc.captions.mode))
    if doc.progress_bar:
        overlays.append(FnNode(_progress_bar, key="progress"))
    comp = Composition(
        width=ctx.width,
        height=ctx.height,
        fps=doc.fps,
        duration=doc.duration,
        scenes=scenes,
        overlays=overlays,
        layout=doc.format,
        motion_blur=doc.motion_blur,
    )
    return comp, ctx


def _glows(i: int) -> tuple[tuple[float, float], ...]:
    """Vary light-pool placement per scene so consecutive shots don't look identical."""
    options = (
        ((0.2, 0.22), (0.85, 0.68)),
        ((0.8, 0.18), (0.15, 0.62)),
        ((0.5, 0.12), (0.7, 0.75)),
        ((0.1, 0.45), (0.9, 0.35)),
    )
    return options[i % len(options)]


def build_from_spec(spec_json: str, scale: float = 1.0) -> tuple[Composition, RenderContext]:
    """Renderer builder: validate the JSON spec and compose it (picklable plain args)."""
    return compose(validate_spec(spec_json), scale=scale)
