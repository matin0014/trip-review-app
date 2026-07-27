"""Snap a manually-added SkyTrain trip to the real rail line.

The public OSRM server only routes foot/bike/car, so a hand-added SkyTrain trip
would otherwise follow roads (see `routing._profiles`). Instead we snap the two
clicked endpoints onto the actual SkyTrain track geometry (TransLink GTFS, one
LineString per line in `data/skytrain.geojson`) and return the sub-path of the
line between them. Pure geometry, no network -- the GeoJSON ships with the app,
so this works offline.

Scope + limits (deliberately a first version):
  * Only the three rail lines (Expo / Millennium / Canada). SeaBus etc. are not
    in the data yet.
  * A trip is snapped to the SINGLE line that best fits BOTH endpoints (minimum
    combined snap distance). A genuine two-line trip through an interchange is
    approximated by whichever single line fits best; a graph across the lines
    would be the follow-up if that matters.
  * If neither endpoint sits within `_SNAP_TOL_M` of any line, we give up and
    return `[]`, so the caller falls back to its straight line rather than
    fabricating a rail path for a click nowhere near the network.
"""

from __future__ import annotations

import json
import math
import os
import sys

from engine.geo import haversine_m

# How far a clicked point may be from the track and still be treated as "on" the
# line. Generous, because this only runs when the reviewer explicitly chose
# SkyTrain, and a click at a station entrance can be a few hundred metres from
# the guideway itself.
_SNAP_TOL_M = 1500.0

_R = 6_371_000.0
_LAT0 = math.radians(49.25)  # metro-Vancouver reference latitude for the planar frame
_lines: "list | None" = None  # cached [(name, [(lat, lon), ...]), ...]


def _data_path() -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root in dev
    return os.path.join(base, "data", "skytrain.geojson")


def _load() -> list:
    global _lines
    if _lines is None:
        try:
            with open(_data_path(), encoding="utf-8") as fh:
                fc = json.load(fh)
            _lines = [(f["properties"].get("line", "?"),
                       [(lat, lon) for lon, lat in f["geometry"]["coordinates"]])
                      for f in fc.get("features", [])]
        except (OSError, ValueError, KeyError):
            _lines = []
    return _lines


def _xy(lat: float, lon: float) -> tuple:
    """Equirectangular metres from the origin -- fine for projection within a metro."""
    return (math.radians(lon) * math.cos(_LAT0) * _R, math.radians(lat) * _R)


def _snap_to_line(lat: float, lon: float, pts: list) -> tuple:
    """Closest point on a polyline to (lat, lon).

    Returns ``(distance_m, snap_latlon, measure_m)`` where `measure_m` is the
    arc-length position of the snap along the line (so two snaps on the same line
    can be ordered and the segment between them cut out)."""
    px, py = _xy(lat, lon)
    best = (float("inf"), (lat, lon), 0.0)
    cum = 0.0
    for (alat, alon), (blat, blon) in zip(pts, pts[1:]):
        ax, ay = _xy(alat, alon)
        bx, by = _xy(blat, blon)
        seg_len = haversine_m(alat, alon, blat, blon)
        dx, dy = bx - ax, by - ay
        d2 = dx * dx + dy * dy
        t = 0.0 if d2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / d2))
        snap = (alat + (blat - alat) * t, alon + (blon - alon) * t)
        dist = haversine_m(lat, lon, snap[0], snap[1])
        if dist < best[0]:
            best = (dist, snap, cum + seg_len * t)
        cum += seg_len
    return best


def snap_route(start_lat: float, start_lon: float,
               end_lat: float, end_lon: float) -> list:
    """The SkyTrain track path between two clicked points, ``[[lat, lon], ...]``
    (the clicked endpoints prepended/appended so the drawn line reaches them), or
    ``[]`` if neither fits a line within tolerance."""
    lines = _load()
    if not lines:
        return []

    best_line = None
    for _name, pts in lines:
        if len(pts) < 2:
            continue
        ds, snap_s, m_s = _snap_to_line(start_lat, start_lon, pts)
        de, snap_e, m_e = _snap_to_line(end_lat, end_lon, pts)
        if ds > _SNAP_TOL_M or de > _SNAP_TOL_M:
            continue
        combined = ds + de
        if best_line is None or combined < best_line[0]:
            best_line = (combined, pts, snap_s, m_s, snap_e, m_e)

    if best_line is None:
        return []

    _c, pts, snap_s, m_s, snap_e, m_e = best_line
    lo, hi = (m_s, m_e) if m_s <= m_e else (m_e, m_s)
    cum = 0.0
    mids = []
    for (alat, alon), (blat, blon) in zip(pts, pts[1:]):
        cum += haversine_m(alat, alon, blat, blon)
        if lo < cum < hi:
            mids.append((blat, blon))
    if m_s > m_e:
        mids.reverse()

    path = [(start_lat, start_lon), snap_s] + mids + [snap_e, (end_lat, end_lon)]
    # collapse consecutive duplicates (a clicked point right on a snap point)
    out = [path[0]]
    for p in path[1:]:
        if haversine_m(out[-1][0], out[-1][1], p[0], p[1]) > 1e-6:
            out.append(p)
    return [[lat, lon] for lat, lon in out]
