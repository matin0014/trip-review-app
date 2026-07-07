"""OSM shortest-path routing for hand-added trips (display + export geometry).

A ``manual_add`` places a trip from two clicked endpoints with no GPS breadcrumbs,
so it would otherwise draw as a straight line. To follow real streets we ask the
public OSRM server for the shortest path between the endpoints ONCE, at add time
(in the Flask handler), and store the resulting coordinates in the add op. Replay
and export then stay deterministic and offline, and the pure `engine/` never makes
a network call. Any routing failure degrades gracefully to ``[]`` (the straight
line), exactly as before this feature.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import replace

from engine.proposals import _route_from_coords

OSRM = "https://router.project-osrm.org/route/v1"
_TIMEOUT_S = 8
_CACHE: dict = {}  # (profile, rounded coords) -> [[lat, lon], ...]


def _profiles(mode: str) -> tuple:
    """OSRM public-server profiles to try for a mode, most specific first, with
    ``driving`` as the universal fallback (the demo server only hosts these few)."""
    if mode in ("Walk", "Run"):
        return ("foot", "driving")
    if mode in ("Bike", "e-scooter"):
        return ("bike", "driving")
    return ("driving",)


def osrm_route(start_lat: float, start_lon: float, end_lat: float, end_lon: float,
               mode: str = "") -> list:
    """Shortest-path ``[[lat, lon], ...]`` between two points, or ``[]`` on any
    failure (network error, timeout, no route). Cached in-process per endpoint."""
    for profile in dict.fromkeys(_profiles(mode)):
        key = (profile, round(start_lat, 6), round(start_lon, 6),
               round(end_lat, 6), round(end_lon, 6))
        if key not in _CACHE:
            _CACHE[key] = _query(profile, start_lat, start_lon, end_lat, end_lon)
        if _CACHE[key]:
            return _CACHE[key]
    return []


def _query(profile: str, start_lat: float, start_lon: float,
           end_lat: float, end_lon: float) -> list:
    q = urllib.parse.urlencode({"overview": "full", "geometries": "geojson"})
    url = f"{OSRM}/{profile}/{start_lon},{start_lat};{end_lon},{end_lat}?{q}"
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []
    routes = data.get("routes") or []
    coords = routes[0].get("geometry", {}).get("coordinates", []) if routes else []
    return [[float(lat), float(lon)] for lon, lat in coords]  # OSRM yields [lon, lat]


def route_trip(t):
    """Fill an OSM street path into a route-less SYNTHESIZED trip (a missing-trip
    stub, or a manual add whose add-time routing failed), so it follows real streets
    in the viewer and the export instead of drawing a straight line. Timestamps are
    spread evenly across the trip's span (reusing engine `_route_from_coords`).

    A negative ``raw_row`` marks a synthesized trip; real logged trips (>= 0) and
    trips that already carry breadcrumbs are returned unchanged, as is any OSRM
    failure (degrades to the existing straight line). Cached per endpoint, so a
    re-render / re-export of the same stub makes no new network call."""
    if t.route or t.raw_row >= 0:
        return t
    coords = osrm_route(t.start_lat, t.start_lon, t.end_lat, t.end_lon, t.mode)
    pts = _route_from_coords(coords, t.start_ts, t.end_ts)
    return replace(t, route=pts) if pts else t
