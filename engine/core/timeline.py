"""Keyframes and animated property tracks.

A :class:`Track` holds time-sorted :class:`Keyframe`\\ s. The value between two keyframes is
interpolated with the *destination* keyframe's easing, so ``Keyframe(1.0, 100, "out_back")`` means
"arrive at 100 at t=1 s with an overshooting ease". Values may be scalars or equal-length tuples
(colors, points). Before the first keyframe the first value holds; after the last, the last holds.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from engine.core.easing import get_easing

Value = float | tuple[float, ...]


def lerp(a: float, b: float, u: float) -> float:
    """Linear interpolation (``u`` may exceed [0, 1] for overshooting easings)."""
    return a + (b - a) * u


def lerp_value(a: Value, b: Value, u: float) -> Value:
    """Interpolate scalars or equal-length tuples component-wise."""
    if isinstance(a, tuple) and isinstance(b, tuple):
        if len(a) != len(b):
            raise ValueError("tuple values must have equal length")
        return tuple(lerp(x, y, u) for x, y in zip(a, b, strict=True))
    if isinstance(a, tuple) or isinstance(b, tuple):
        raise TypeError("cannot interpolate scalar with tuple")
    return lerp(a, b, u)


@dataclass(frozen=True, order=True)
class Keyframe:
    """A value reached at time ``t`` (seconds), approached with ``easing``."""

    t: float
    value: Value = field(compare=False)
    easing: str = field(default="out_cubic", compare=False)


class Track:
    """An animated property: keyframes plus fast lookup."""

    __slots__ = ("_easings", "_keys", "_times")

    def __init__(self, keyframes: Iterable[Keyframe]) -> None:
        keys = sorted(keyframes)
        if not keys:
            raise ValueError("a track needs at least one keyframe")
        self._keys: list[Keyframe] = keys
        self._times: list[float] = [k.t for k in keys]
        self._easings = [get_easing(k.easing) for k in keys]

    @classmethod
    def constant(cls, value: Value) -> Track:
        """A track that always returns ``value``."""
        return cls([Keyframe(0.0, value)])

    @property
    def keyframes(self) -> Sequence[Keyframe]:
        """The keyframes in time order."""
        return tuple(self._keys)

    @property
    def end_time(self) -> float:
        """Time of the last keyframe."""
        return self._times[-1]

    def value_at(self, t: float) -> Value:
        """Interpolated value at time ``t``."""
        times = self._times
        if t <= times[0]:
            return self._keys[0].value
        if t >= times[-1]:
            return self._keys[-1].value
        i = bisect.bisect_right(times, t)
        k0, k1 = self._keys[i - 1], self._keys[i]
        span = k1.t - k0.t
        u = (t - k0.t) / span if span > 0 else 1.0
        return lerp_value(k0.value, k1.value, self._easings[i](u))

    def scalar_at(self, t: float) -> float:
        """Interpolated scalar value; raises if the track holds tuples."""
        v = self.value_at(t)
        if isinstance(v, tuple):
            raise TypeError("track holds tuple values")
        return v

    def is_static_between(self, t0: float, t1: float) -> bool:
        """True if no keyframe transition happens strictly inside [t0, t1]."""
        if len(self._keys) == 1:
            return True
        return t1 <= self._times[0] or t0 >= self._times[-1]


def tween(start: float, duration: float, frm: Value, to: Value, easing: str = "out_cubic") -> Track:
    """Shorthand: hold ``frm`` until ``start``, then ease to ``to`` over ``duration`` seconds."""
    return Track([Keyframe(start, frm, "linear"), Keyframe(start + max(duration, 1e-6), to, easing)])


def stagger(count: int, offset: float, *, start: float = 0.0, mode: str = "forward") -> list[float]:
    """Start times for ``count`` elements entering ``offset`` seconds apart.

    ``mode`` is ``forward``, ``reverse`` or ``center`` (middle first, spreading outward).
    Motion-design guidance: 0.03-0.08 s offsets read as one gesture rather than a list.
    """
    if count <= 0:
        return []
    if mode == "forward":
        order = list(range(count))
    elif mode == "reverse":
        order = list(range(count - 1, -1, -1))
    elif mode == "center":
        mid = (count - 1) / 2.0
        ranks = sorted(range(count), key=lambda i: (abs(i - mid), i))
        order = [0] * count
        for rank, i in enumerate(ranks):
            order[i] = rank
        return [start + offset * order[i] for i in range(count)]
    else:
        raise ValueError(f"unknown stagger mode: {mode}")
    times = [0.0] * count
    for rank, i in enumerate(order):
        times[i] = start + offset * rank
    return times
