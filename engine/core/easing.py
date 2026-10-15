"""Easing library: maps normalized progress ``u`` in [0, 1] to eased progress.

Every function satisfies ``f(0) == 0`` and ``f(1) == 1`` exactly; overshooting curves (back,
elastic, spring) may leave [0, 1] in between, which is how entrances get anticipation/overshoot.

Easings are referenced by name in scene specs, e.g. ``"out_cubic"``, ``"spring"``,
``"spring(240,18)"`` (stiffness, damping), ``"cubic_bezier(0.2,0.8,0.2,1)"``, ``"back_out(2.2)"``.
"""

from __future__ import annotations

import functools
import math
import re
from collections.abc import Callable

Easing = Callable[[float], float]


def _clamp01(u: float) -> float:
    return 0.0 if u <= 0.0 else 1.0 if u >= 1.0 else u


def linear(u: float) -> float:
    """Identity: deliberate constant-speed motion only (tickers, scrolls)."""
    return _clamp01(u)


def _poly_in(p: int) -> Easing:
    def f(u: float) -> float:
        return _clamp01(u) ** p

    return f


def _poly_out(p: int) -> Easing:
    def f(u: float) -> float:
        return 1.0 - (1.0 - _clamp01(u)) ** p

    return f


def _poly_in_out(p: int) -> Easing:
    def f(u: float) -> float:
        u = _clamp01(u)
        if u < 0.5:
            return float((2.0**p) / 2.0 * u**p)
        return 1.0 - ((-2.0 * u + 2.0) ** p) / 2.0

    return f


def in_sine(u: float) -> float:
    """Sinusoidal ease-in."""
    return 1.0 - math.cos(_clamp01(u) * math.pi / 2.0)


def out_sine(u: float) -> float:
    """Sinusoidal ease-out."""
    return math.sin(_clamp01(u) * math.pi / 2.0)


def in_out_sine(u: float) -> float:
    """Sinusoidal ease-in-out (gentle; good for ambient drift)."""
    return -(math.cos(math.pi * _clamp01(u)) - 1.0) / 2.0


def in_expo(u: float) -> float:
    """Exponential ease-in."""
    u = _clamp01(u)
    return 0.0 if u == 0.0 else 2.0 ** (10.0 * u - 10.0) if u < 1.0 else 1.0


def out_expo(u: float) -> float:
    """Exponential ease-out: fast start, long settle; a strong default for UI-like entrances."""
    u = _clamp01(u)
    return 1.0 if u == 1.0 else 1.0 - 2.0 ** (-10.0 * u)


def in_out_expo(u: float) -> float:
    """Exponential ease-in-out: snappy camera moves."""
    u = _clamp01(u)
    if u in (0.0, 1.0):
        return u
    if u < 0.5:
        return math.pow(2.0, 20.0 * u - 10.0) / 2.0
    return (2.0 - math.pow(2.0, -20.0 * u + 10.0)) / 2.0


def in_circ(u: float) -> float:
    """Circular ease-in."""
    u = _clamp01(u)
    return 1.0 - math.sqrt(1.0 - u * u)


def out_circ(u: float) -> float:
    """Circular ease-out."""
    u = _clamp01(u)
    return math.sqrt(1.0 - (u - 1.0) ** 2)


def back_in(s: float = 1.70158) -> Easing:
    """Ease-in with anticipation (pulls back below 0 before moving)."""

    def f(u: float) -> float:
        u = _clamp01(u)
        return (s + 1.0) * u**3 - s * u**2

    return f


def back_out(s: float = 1.70158) -> Easing:
    """Ease-out with overshoot past 1 before settling."""

    def f(u: float) -> float:
        u = _clamp01(u) - 1.0
        return 1.0 + (s + 1.0) * u**3 + s * u**2

    return f


def back_in_out(s: float = 1.70158) -> Easing:
    """Anticipation on the way in, overshoot on the way out."""
    c2 = s * 1.525

    def f(u: float) -> float:
        u = _clamp01(u)
        if u < 0.5:
            return ((2 * u) ** 2 * ((c2 + 1) * 2 * u - c2)) / 2
        return ((2 * u - 2) ** 2 * ((c2 + 1) * (u * 2 - 2) + c2) + 2) / 2

    return f


def elastic_out(u: float) -> float:
    """Elastic ease-out (rubber-band wobble)."""
    u = _clamp01(u)
    if u in (0.0, 1.0):
        return u
    c4 = (2.0 * math.pi) / 3.0
    return math.pow(2.0, -10.0 * u) * math.sin((u * 10.0 - 0.75) * c4) + 1.0


def bounce_out(u: float) -> float:
    """Bounce ease-out (ball dropping onto the floor)."""
    u = _clamp01(u)
    n1, d1 = 7.5625, 2.75
    if u < 1 / d1:
        return n1 * u * u
    if u < 2 / d1:
        u -= 1.5 / d1
        return n1 * u * u + 0.75
    if u < 2.5 / d1:
        u -= 2.25 / d1
        return n1 * u * u + 0.9375
    u -= 2.625 / d1
    return n1 * u * u + 0.984375


def spring(stiffness: float = 170.0, damping: float = 26.0, mass: float = 1.0) -> Easing:
    """Physically based damped spring from 0 to 1, time-normalised to settle by ``u = 1``.

    The raw solution is computed over the time it takes the envelope to decay below 0.1 %, then a
    tiny linear correction makes ``f(1) == 1`` exact. Under-damped springs overshoot naturally.
    """
    if stiffness <= 0 or damping <= 0 or mass <= 0:
        raise ValueError("spring parameters must be positive")
    w0 = math.sqrt(stiffness / mass)
    zeta = damping / (2.0 * math.sqrt(stiffness * mass))
    decay = zeta * w0
    settle_t = min(10.0, math.log(1000.0) / decay)

    def raw(t: float) -> float:
        if zeta < 1.0:
            wd = w0 * math.sqrt(1.0 - zeta * zeta)
            return 1.0 - math.exp(-decay * t) * (math.cos(wd * t) + (decay / wd) * math.sin(wd * t))
        if zeta == 1.0:
            return 1.0 - math.exp(-w0 * t) * (1.0 + w0 * t)
        r1 = -w0 * (zeta - math.sqrt(zeta * zeta - 1.0))
        r2 = -w0 * (zeta + math.sqrt(zeta * zeta - 1.0))
        c2 = r1 / (r1 - r2)
        c1 = 1.0 - c2
        return 1.0 - (c1 * math.exp(r2 * t) + c2 * math.exp(r1 * t))

    end_error = 1.0 - raw(settle_t)

    def f(u: float) -> float:
        u = _clamp01(u)
        if u == 0.0:
            return 0.0
        if u == 1.0:
            return 1.0
        return raw(u * settle_t) + end_error * u

    return f


def cubic_bezier(x1: float, y1: float, x2: float, y2: float) -> Easing:
    """CSS-style cubic Bézier timing function (control points (x1,y1), (x2,y2)).

    Solves x(s) = u with Newton-Raphson, falling back to bisection, then returns y(s).
    """
    if not (0.0 <= x1 <= 1.0 and 0.0 <= x2 <= 1.0):
        raise ValueError("cubic_bezier x control points must be in [0, 1]")

    cx = 3.0 * x1
    bx = 3.0 * (x2 - x1) - cx
    ax = 1.0 - cx - bx
    cy = 3.0 * y1
    by = 3.0 * (y2 - y1) - cy
    ay = 1.0 - cy - by

    def sx(s: float) -> float:
        return ((ax * s + bx) * s + cx) * s

    def sy(s: float) -> float:
        return ((ay * s + by) * s + cy) * s

    def dsx(s: float) -> float:
        return (3.0 * ax * s + 2.0 * bx) * s + cx

    def f(u: float) -> float:
        u = _clamp01(u)
        if u in (0.0, 1.0):
            return u
        s = u
        for _ in range(8):
            err = sx(s) - u
            if abs(err) < 1e-7:
                return sy(s)
            d = dsx(s)
            if abs(d) < 1e-6:
                break
            s -= err / d
        lo, hi = 0.0, 1.0
        s = u
        for _ in range(60):
            x = sx(s)
            if abs(x - u) < 1e-7:
                break
            if x < u:
                lo = s
            else:
                hi = s
            s = (lo + hi) / 2.0
        return sy(s)

    return f


_STATIC: dict[str, Easing] = {
    "linear": linear,
    "hold": lambda u: 1.0 if u >= 1.0 else 0.0,
    "in_quad": _poly_in(2),
    "out_quad": _poly_out(2),
    "in_out_quad": _poly_in_out(2),
    "in_cubic": _poly_in(3),
    "out_cubic": _poly_out(3),
    "in_out_cubic": _poly_in_out(3),
    "in_quart": _poly_in(4),
    "out_quart": _poly_out(4),
    "in_out_quart": _poly_in_out(4),
    "in_quint": _poly_in(5),
    "out_quint": _poly_out(5),
    "in_out_quint": _poly_in_out(5),
    "in_sine": in_sine,
    "out_sine": out_sine,
    "in_out_sine": in_out_sine,
    "in_expo": in_expo,
    "out_expo": out_expo,
    "in_out_expo": in_out_expo,
    "in_circ": in_circ,
    "out_circ": out_circ,
    "back_in": back_in(),
    "back_out": back_out(),
    "back_in_out": back_in_out(),
    "elastic_out": elastic_out,
    "bounce_out": bounce_out,
    "spring": spring(),
    "spring_soft": spring(120.0, 20.0),
    "spring_snappy": spring(320.0, 30.0),
    "spring_bouncy": spring(240.0, 14.0),
    "smooth": cubic_bezier(0.4, 0.0, 0.2, 1.0),  # material "standard"
    "emphasized": cubic_bezier(0.2, 0.0, 0.0, 1.0),
}

_PARAM_RE = re.compile(r"^([a-z_]+)\(([-0-9.,\s]+)\)$")
_PARAM_FACTORIES: dict[str, tuple[Callable[..., Easing], int, int]] = {
    "spring": (spring, 2, 3),
    "cubic_bezier": (cubic_bezier, 4, 4),
    "back_in": (back_in, 1, 1),
    "back_out": (back_out, 1, 1),
    "back_in_out": (back_in_out, 1, 1),
}

EASING_NAMES: tuple[str, ...] = tuple(sorted(_STATIC))


@functools.cache
def get_easing(name: str) -> Easing:
    """Resolve an easing by name, including parameterised forms like ``spring(240,14)``.

    Raises:
        ValueError: Unknown easing or malformed parameters.
    """
    key = name.strip().lower()
    if key in _STATIC:
        return _STATIC[key]
    m = _PARAM_RE.fullmatch(key)
    if not m or m.group(1) not in _PARAM_FACTORIES:
        raise ValueError(f"unknown easing: {name!r}")
    factory, lo, hi = _PARAM_FACTORIES[m.group(1)]
    try:
        params = [float(p) for p in m.group(2).split(",")]
    except ValueError as exc:
        raise ValueError(f"bad easing parameters: {name!r}") from exc
    if not lo <= len(params) <= hi or not all(math.isfinite(p) for p in params):
        raise ValueError(f"bad easing parameters: {name!r}")
    return factory(*params)


def is_valid_easing(name: str) -> bool:
    """True if :func:`get_easing` would accept ``name``."""
    try:
        get_easing(name)
    except ValueError:
        return False
    return True
