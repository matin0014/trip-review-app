"""Snap a manually-added transit trip onto real route geometry.

The public OSRM server only routes foot/bike/car, so a hand-added SkyTrain,
SeaBus, or Kelowna bus trip would otherwise follow roads (see
`routing._profiles`). Instead we snap the two clicked endpoints onto the
actual route geometry (TransLink / BC Transit GTFS, one LineString per line in
`data/<network>.geojson`) and return the sub-path between them. Pure geometry,
no network call -- the GeoJSON ships with the app, so this works offline.

This module is pure geometry: it finds the boarding and alighting points on
the line and the track/route segment between them. The caller
(`routing.route_between`) then OSRM-routes the access leg (clicked start ->
boarding point) and the egress leg (alighting point -> clicked end), so a trip
reads as walk-to-stop -> ride -> walk-to-destination.

Scope + limits (deliberately a first version):
  * Three networks: ``skytrain`` (3 rail lines), ``seabus`` (1 line), and
    ``kelowna_bus`` (28 routes, low overlap -- see the branch history for the
    ambiguity check that ruled this safe). Metro Vancouver's bus network
    (238 routes, heavily overlapping, no route number in the survey data) is
    deliberately NOT here -- nearest-line snapping would silently guess the
    wrong specific route.
  * A trip rides the SINGLE line whose nearest points to the two endpoints
    minimise the combined off-rail distance. A genuine two-line trip through an
    interchange is approximated by whichever line fits best; a graph across the
    lines would be the follow-up if that matters.
"""

from __future__ import annotations

import json
import math
import os
import sys

from engine.geo import haversine_m

_R = 6_371_000.0
_LAT0 = math.radians(49.25)  # metro-Vancouver reference latitude for the planar frame

_NETWORKS = {"skytrain": "skytrain.geojson", "seabus": "seabus.geojson",
             "kelowna_bus": "kelowna_bus.geojson"}
_cache: dict = {}  # network -> [(name, [(lat, lon), ...]), ...]


def _data_path(network: str) -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root in dev
    return os.path.join(base, "data", _NETWORKS[network])


def _load(network: str) -> list:
    if network not in _cache:
        try:
            with open(_data_path(network), encoding="utf-8") as fh:
                fc = json.load(fh)
            _cache[network] = [(f["properties"].get("line", "?"),
                                [(lat, lon) for lon, lat in f["geometry"]["coordinates"]])
                               for f in fc.get("features", [])]
        except (OSError, ValueError, KeyError):
            _cache[network] = []
    return _cache[network]


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


def nearest_rail_segment(start_lat: float, start_lon: float,
                         end_lat: float, end_lon: float,
                         network: str = "skytrain") -> "dict | None":
    """Board/ride/alight geometry for the best-fitting line in ``network``, or
    None if no line data is available.

    Picks the line whose nearest points to the two clicked endpoints minimise the
    combined off-rail (access + egress) distance, then returns::

        {"board":   (lat, lon),          # nearest point on that line to the start
         "alight":  (lat, lon),          # nearest point on that line to the end
         "rail":    [[lat, lon], ...],   # track from board to alight (inclusive)
         "access_m": float,              # start -> board straight distance
         "egress_m": float}              # alight -> end straight distance

    The clicked endpoints are NOT included -- the caller stitches OSRM access and
    egress legs onto ``board`` and ``alight``. There is no tolerance cut-off here:
    a trip that starts far from the network simply gets a long access leg, which
    the caller may reject if it is unreasonably far (i.e. not on this network)."""
    lines = _load(network)
    if not lines:
        return None

    best = None
    for _name, pts in lines:
        if len(pts) < 2:
            continue
        ds, snap_s, m_s = _snap_to_line(start_lat, start_lon, pts)
        de, snap_e, m_e = _snap_to_line(end_lat, end_lon, pts)
        combined = ds + de
        if best is None or combined < best[0]:
            best = (combined, ds, de, pts, snap_s, m_s, snap_e, m_e)

    _c, ds, de, pts, snap_s, m_s, snap_e, m_e = best
    lo, hi = (m_s, m_e) if m_s <= m_e else (m_e, m_s)
    cum = 0.0
    mids = []
    for (alat, alon), (blat, blon) in zip(pts, pts[1:]):
        cum += haversine_m(alat, alon, blat, blon)
        if lo < cum < hi:
            mids.append((blat, blon))
    if m_s > m_e:
        mids.reverse()

    rail = [snap_s] + mids + [snap_e]
    return {"board": snap_s, "alight": snap_e,
            "rail": [[lat, lon] for lat, lon in rail],
            "access_m": ds, "egress_m": de}
