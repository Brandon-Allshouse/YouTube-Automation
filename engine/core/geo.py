"""Geographic primitives: Natural Earth loading, projections, sphere rotation, graticules.

Projections (lon/lat degrees → unitless plane, y up):
* ``mercator``   x = λ, y = ln tan(π/4 + φ/2), latitude clamped to ±85°
* ``equal_earth`` Šavrič, Patterson & Jenny (2018) equal-area pseudocylindrical
* ``equirect``   x = λ, y = φ

``project(..., morph=(a, b, u))`` blends two projections point-wise, which animates e.g. Mercator
morphing into Equal Earth (Greenland visibly deflates). ``rotate_to`` moves a shape across the
sphere by a rigid rotation, so it keeps its true size and shape, and re-projecting it shows how a
projection distorts it at the new latitude (the "true size" trick).
"""

from __future__ import annotations

import functools
import itertools
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from src.common.paths import ASSETS_DIR

MAPS_DIR = ASSETS_DIR / "maps"
Arr = npt.NDArray[np.float64]

_EE = (1.340264, -0.081106, 0.000893, 0.003796)
_M = math.sqrt(3) / 2


@dataclass(frozen=True)
class Feature:
    """A country: id (ADM0_A3), name, continent, rings as (n, 2) lon/lat arrays."""

    id: str
    name: str
    continent: str
    rings: tuple[Arr, ...]
    area_km2_hint: float  # spherical area estimate (for checks)


def _rings_from_geometry(geom: dict[str, object]) -> list[Arr]:
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    polys: list[object] = []
    if gtype == "Polygon":
        polys = [coords]
    elif gtype == "MultiPolygon" and isinstance(coords, list):
        polys = list(coords)
    rings: list[Arr] = []
    for poly in polys:
        if isinstance(poly, list):
            for ring in poly:
                a = np.asarray(ring, dtype=np.float64)
                if a.ndim == 2 and a.shape[0] >= 4 and a.shape[1] >= 2:
                    rings.append(a[:, :2])
    return rings


def ring_area_km2(ring: Arr) -> float:
    """Approximate spherical area of a lon/lat ring (km², unsigned)."""
    r = 6371.0088
    lon = np.unwrap(np.radians(ring[:, 0]))  # robust across the antimeridian
    lat = np.radians(ring[:, 1])
    s = np.sum((np.roll(lon, -1) - lon) * (2 + np.sin(lat) + np.sin(np.roll(lat, -1))))
    return float(abs(s) * r * r / 2)


@functools.cache
def load_countries(resolution: str = "50m") -> dict[str, Feature]:
    """Load Natural Earth admin-0 countries keyed by ADM0_A3 (cached per process).

    Raises:
        FileNotFoundError: data not downloaded (run fetch-assets).
    """
    if resolution not in ("50m", "110m"):
        raise ValueError("resolution must be 50m or 110m")
    path: Path = MAPS_DIR / f"ne_{resolution}_admin_0_countries.geojson"
    if not path.is_file():
        raise FileNotFoundError(f"{path.name} missing (run fetch-assets)")
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, Feature] = {}
    for f in data.get("features", []):
        props = f.get("properties") or {}
        code = str(props.get("ADM0_A3") or props.get("ISO_A3") or "")
        if not code or not isinstance(f.get("geometry"), dict):
            continue
        rings = _rings_from_geometry(f["geometry"])
        if not rings:
            continue
        area = sum(ring_area_km2(r) for r in rings)
        out[code] = Feature(code, str(props.get("NAME", code)), str(props.get("CONTINENT", "")), tuple(rings), area)
    return out


# --- projections ----------------------------------------------------------------------------


def _mercator(lon: Arr, lat: Arr) -> tuple[Arr, Arr]:
    lat_c = np.clip(lat, -85.0, 85.0)
    return np.radians(lon), np.log(np.tan(np.pi / 4 + np.radians(lat_c) / 2))


def _equal_earth(lon: Arr, lat: Arr) -> tuple[Arr, Arr]:
    a1, a2, a3, a4 = _EE
    lam, phi = np.radians(lon), np.radians(lat)
    th = np.arcsin(_M * np.sin(phi))
    th2 = th * th
    th6 = th2 * th2 * th2
    x = (2 * math.sqrt(3) * lam * np.cos(th)) / (3 * (9 * a4 * th6 * th2 + 7 * a3 * th6 + 3 * a2 * th2 + a1))
    y = th * (a4 * th6 * th2 + a3 * th6 + a2 * th2 + a1)
    return x, y


def _equirect(lon: Arr, lat: Arr) -> tuple[Arr, Arr]:
    return np.radians(lon), np.radians(lat)


PROJECTIONS = {"mercator": _mercator, "equal_earth": _equal_earth, "equirect": _equirect}


def project(lonlat: Arr, projection: str = "mercator", morph: tuple[str, str, float] | None = None) -> Arr:
    """Project (n, 2) lon/lat degrees to (n, 2) plane coords (y up).

    ``morph=(a, b, u)`` linearly blends projection ``a`` (u=0) into ``b`` (u=1).
    """
    lon, lat = lonlat[:, 0], lonlat[:, 1]
    if morph is not None:
        a, b, u = morph
        xa, ya = PROJECTIONS[a](lon, lat)
        xb, yb = PROJECTIONS[b](lon, lat)
        return np.stack([xa + (xb - xa) * u, ya + (yb - ya) * u], axis=1)
    x, y = PROJECTIONS[projection](lon, lat)
    return np.stack([x, y], axis=1)


# --- sphere rotation ------------------------------------------------------------------------


def to_xyz(lonlat: Arr) -> Arr:
    """Lon/lat degrees → unit vectors."""
    lon, lat = np.radians(lonlat[:, 0]), np.radians(lonlat[:, 1])
    return np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=1)


def to_lonlat(xyz: Arr) -> Arr:
    """Unit vectors → lon/lat degrees."""
    lon = np.degrees(np.arctan2(xyz[:, 1], xyz[:, 0]))
    lat = np.degrees(np.arcsin(np.clip(xyz[:, 2], -1, 1)))
    return np.stack([lon, lat], axis=1)


def centroid(rings: tuple[Arr, ...] | list[Arr]) -> tuple[float, float]:
    """Area-weighted spherical centroid (lon, lat) of the largest-ring-dominated shape."""
    pts = np.concatenate([to_xyz(r) * max(ring_area_km2(r), 1.0) for r in rings])
    v = pts.sum(axis=0)
    v /= np.linalg.norm(v)
    ll = to_lonlat(v[None, :])[0]
    return float(ll[0]), float(ll[1])


def rotate_to(rings: tuple[Arr, ...] | list[Arr], target: tuple[float, float], u: float = 1.0) -> list[Arr]:
    """Rigidly rotate rings so their centroid moves fraction ``u`` of the way to ``target``.

    Rotation (Rodrigues) preserves true shape and area on the sphere.
    """
    c = to_xyz(np.array([centroid(rings)]))[0]
    t = to_xyz(np.array([target], dtype=np.float64))[0]
    axis = np.cross(c, t)
    n = np.linalg.norm(axis)
    if n < 1e-12 or u == 0:
        return [r.copy() for r in rings]
    axis /= n
    ang = math.acos(float(np.clip(np.dot(c, t), -1, 1))) * u
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    rot = np.eye(3) + math.sin(ang) * k + (1 - math.cos(ang)) * (k @ k)
    return [unwrap_lon(to_lonlat(to_xyz(r) @ rot.T)) for r in rings]


def slide_to(rings: tuple[Arr, ...] | list[Arr], target: tuple[float, float], u: float = 1.0) -> list[Arr]:
    """Move a shape fraction ``u`` toward ``target`` keeping it north-up.

    First a rotation along the shape's own meridian (changes latitude without twisting the shape
    relative to north), then a rotation about the polar axis (pure longitude shift). Both are rigid
    sphere rotations, so true area and shape are preserved.
    """
    c_lon, c_lat = centroid(rings)
    moved = rotate_to(rings, (c_lon, c_lat + (target[1] - c_lat) * u), 1.0)
    dlon = (target[0] - c_lon) * u
    out = []
    for r in moved:
        m = r.copy()
        m[:, 0] = m[:, 0] + dlon
        out.append(m)
    return out


def unwrap_lon(ring: Arr) -> Arr:
    """Make ring longitudes continuous so shapes crossing ±180° don't streak across the map."""
    out = ring.copy()
    out[:, 0] = np.degrees(np.unwrap(np.radians(ring[:, 0])))
    return out


def densify(points: Arr, step_deg: float = 1.0) -> Arr:
    """Insert points along great circles so long lines bend correctly when projected."""
    out = [points[0]]
    for a, b in itertools.pairwise(points):
        va, vb = to_xyz(np.array([a]))[0], to_xyz(np.array([b]))[0]
        ang = math.degrees(math.acos(float(np.clip(np.dot(va, vb), -1, 1))))
        n = max(1, int(ang / step_deg))
        for k in range(1, n + 1):
            f = k / n
            s = math.radians(ang)
            v: Arr = vb if s < 1e-9 else (math.sin((1 - f) * s) * va + math.sin(f * s) * vb) / math.sin(s)
            out.append(to_lonlat(v[None, :])[0])
    return np.asarray(out, dtype=np.float64)


def graticule(step: float = 15.0, lat_max: float = 84.0) -> list[Arr]:
    """Meridians and parallels as lon/lat polylines."""
    lines: list[Arr] = []
    lats = np.linspace(-lat_max, lat_max, 90)
    lons = np.linspace(-180, 180, 181)
    for meridian in np.arange(-180, 181, step):
        lines.append(np.stack([np.full_like(lats, meridian), lats], axis=1))
    for parallel in np.arange(-75, 76, step):
        lines.append(np.stack([lons, np.full_like(lons, parallel)], axis=1))
    return lines
