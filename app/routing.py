"""OSM shortest-path routing for hand-added trips (display + export geometry),
and route-distance estimation for the manual-add end-time suggestion.

A ``manual_add`` places a trip from two clicked endpoints with no GPS breadcrumbs,
so it would otherwise draw as a straight line. To follow real streets we ask the
public OSRM server for the shortest path between the endpoints ONCE, at add time
(in the Flask handler), and store the resulting coordinates in the add op. Replay
and export then stay deterministic and offline, and the pure `engine/` never makes
a network call. Any routing failure degrades gracefully to ``[]`` (the straight
line), exactly as before this feature.

The SAME lookup also yields the route's real street-following distance, used to
suggest an end time (distance / a canonical per-mode speed, NOT OSRM's own
duration estimate -- see `server._REF_SPEED_KMH`'s docstring for why: OSRM only
has foot/bike/driving profiles, so its own duration would score a SkyTrain trip
as if it were a car on that road route).
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import replace

from app import rail
from engine.geo import haversine_m
from engine.proposals import _route_from_coords

OSRM = "https://router.project-osrm.org/route/v1"
_TIMEOUT_S = 8
_CACHE: dict = {}  # (profile, rounded coords) -> {"coords": [[lat,lon],...], "distance_m": float|None}


def _profiles(mode: str) -> tuple:
    """OSRM public-server profiles to try for a mode, most specific first, with
    ``driving`` as the universal fallback (the demo server only hosts these few)."""
    if mode in ("Walk", "Run"):
        return ("foot", "driving")
    if mode in ("Bike", "e-scooter"):
        return ("bike", "driving")
    return ("driving",)


def _lookup(start_lat: float, start_lon: float, end_lat: float, end_lon: float,
           mode: str) -> dict:
    """The cached ``{"coords", "distance_m"}`` for the first profile (in mode
    priority order) that yields a route; ``{"coords": [], "distance_m": None}``
    if every profile fails."""
    for profile in dict.fromkeys(_profiles(mode)):
        key = (profile, round(start_lat, 6), round(start_lon, 6),
               round(end_lat, 6), round(end_lon, 6))
        if key not in _CACHE:
            _CACHE[key] = _query(profile, start_lat, start_lon, end_lat, end_lon)
        if _CACHE[key]["coords"]:
            return _CACHE[key]
    return {"coords": [], "distance_m": None}


def osrm_route(start_lat: float, start_lon: float, end_lat: float, end_lon: float,
               mode: str = "") -> list:
    """Shortest-path ``[[lat, lon], ...]`` between two points, or ``[]`` on any
    failure (network error, timeout, no route). Cached in-process per endpoint."""
    return _lookup(start_lat, start_lon, end_lat, end_lon, mode)["coords"]


def osrm_distance_m(start_lat: float, start_lon: float, end_lat: float, end_lon: float,
                    mode: str = "") -> "float | None":
    """The real street-following distance (metres) of the same shortest path
    `osrm_route` would draw, or None on any routing failure. Shares `osrm_route`'s
    cache, so computing both for the same add costs exactly one network call."""
    return _lookup(start_lat, start_lon, end_lat, end_lon, mode)["distance_m"]


def straight_line_distance_m(start_lat: float, start_lon: float,
                             end_lat: float, end_lon: float) -> float:
    """Haversine fallback distance when OSRM is unreachable -- always available,
    never fails, just less accurate than a real routed path."""
    return haversine_m(start_lat, start_lon, end_lat, end_lon)


def _query(profile: str, start_lat: float, start_lon: float,
           end_lat: float, end_lon: float) -> dict:
    q = urllib.parse.urlencode({"overview": "full", "geometries": "geojson"})
    url = f"{OSRM}/{profile}/{start_lon},{start_lat};{end_lon},{end_lat}?{q}"
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {"coords": [], "distance_m": None}
    routes = data.get("routes") or []
    if not routes:
        return {"coords": [], "distance_m": None}
    coords = routes[0].get("geometry", {}).get("coordinates", [])
    return {
        "coords": [[float(lat), float(lon)] for lon, lat in coords],  # OSRM yields [lon, lat]
        "distance_m": routes[0].get("distance"),
    }


# A seam must clear BOTH thresholds to be routed. Distance matches the engine's
# 200 m contiguity gate (`engine.params.gate_m`); time matches its
# `short_gap_fragment_min` (3 min), the existing "long enough to be a real gap
# rather than one sampling interval" constant.
#
# The time test is what makes this safe on real data: in wave 2, 19,824 pairs of
# CONSECUTIVE fixes inside a single trip are already >200 m apart (sparse sampling
# and cell-tower jitter), and 94% of those are under a minute apart. Routing on
# distance alone would fabricate street paths across all of them the moment such a
# trip was merged -- and, because a split partitions a route by timestamp, would
# make split-then-merge silently insert points the original never had.
_GAP_FILL_M = 200.0
_GAP_FILL_S = 180.0


def fill_route_gaps(t, min_gap_m: float = _GAP_FILL_M, min_gap_s: float = _GAP_FILL_S):
    """Route the SEAMS of a merged trip through OSM streets.

    A merge concatenates its legs' real breadcrumbs and inserts nothing between
    them, so a merge across a real gap both draws and measures as a single
    straight line from one leg's last fix to the next leg's first fix. This asks
    OSRM for the street path across each seam and splices it in, timestamps spread
    evenly across that seam's own span.

    Guards, because the input is real GPS and this SYNTHESIZES geometry:
      * only merged trips (``absorbed``) are touched -- an unmerged trip's jumps
        are its own data, not a seam;
      * a seam must be far apart AND separated in time: two consecutive fixes
        seconds apart are one sampling interval (or tower jitter), never an
        unlogged journey. This is also what keeps split-then-merge lossless --
        a split partitions the route by timestamp, so re-merging it sees only
        sampling-interval seams and restores the original exactly;
      * an N-leg merge has at most N-1 seams, so a route with MORE large jumps
        than that is GPS ping-pong (a phone alternating between a real fix and a
        cell-tower fix), and is left completely alone rather than fabricating a
        route across noise;
      * any OSRM failure degrades to the existing straight line.

    The inserted points are synthesized, not observed -- the same trade already
    made for a hand-added trip's route.
    """
    legs = len(t.absorbed)
    route = t.route or []
    if legs < 2 or len(route) < 2:
        return t
    gaps = [i for i, (a, b) in enumerate(zip(route, route[1:]))
            if haversine_m(a.lat, a.lon, b.lat, b.lon) > min_gap_m
            and (b.ts - a.ts).total_seconds() >= min_gap_s]
    if not gaps or len(gaps) > legs - 1:
        return t

    out = [route[0]]
    for i, (prev, cur) in enumerate(zip(route, route[1:])):
        if i in set(gaps):
            coords = osrm_route(prev.lat, prev.lon, cur.lat, cur.lon, t.mode)
            # drop the routed endpoints: they duplicate the real fixes we keep
            out.extend(_route_from_coords(coords, prev.ts, cur.ts)[1:-1])
        out.append(cur)
    return replace(t, route=out) if len(out) != len(route) else t


# Beyond this from the nearest track, a "SkyTrain" trip is not a Metro Vancouver
# trip at all (e.g. an Okanagan participant), so we don't force it onto the rail;
# it falls back to an ordinary OSRM route between the clicked points.
_RAIL_MAX_ACCESS_M = 30_000.0
# A clicked point this close to the boarding/alighting point needs no access leg.
_RAIL_SNAP_SKIP_M = 120.0


def _concat(*chunks) -> list:
    """Join coordinate chunks, dropping points that coincide with the previous one
    (the access leg's end == the boarding point == the rail's first point, etc.)."""
    out: list = []
    for chunk in chunks:
        for lat, lon in chunk:
            if not out or haversine_m(out[-1][0], out[-1][1], lat, lon) > 1.0:
                out.append([lat, lon])
    return out


def route_between(mode: str, start_lat: float, start_lon: float,
                  end_lat: float, end_lon: float) -> list:
    """Best ``[[lat, lon], ...]`` geometry between two clicked points for a manual
    add. Empty on failure, so the caller draws a straight line.

    A SkyTrain trip is modelled as walk-to-station -> ride -> walk-to-destination:
    OSRM routes the access leg (clicked start -> nearest point on the best line)
    and the egress leg (alighting point -> clicked end), with the real track
    (`app.rail`, offline) in between. This is because the OSRM public server has no
    rail profile, so the ride itself would otherwise follow roads (and cross
    water/bridges the train does not). If the nearest line is unreasonably far, the
    trip isn't really on the network and falls back to a plain OSRM route.

    Every other mode is just the OSRM street path between the two points."""
    if mode == "SkyTrain":
        seg = rail.nearest_rail_segment(start_lat, start_lon, end_lat, end_lon)
        if seg is not None and max(seg["access_m"], seg["egress_m"]) < _RAIL_MAX_ACCESS_M:
            b_lat, b_lon = seg["board"]
            a_lat, a_lon = seg["alight"]
            # access: clicked start -> boarding point (walk); skip if already there
            if seg["access_m"] <= _RAIL_SNAP_SKIP_M:
                access = [[start_lat, start_lon], [b_lat, b_lon]]
            else:
                access = (osrm_route(start_lat, start_lon, b_lat, b_lon, "Walk")
                          or [[start_lat, start_lon], [b_lat, b_lon]])
            # egress: alighting point -> clicked end (walk)
            if seg["egress_m"] <= _RAIL_SNAP_SKIP_M:
                egress = [[a_lat, a_lon], [end_lat, end_lon]]
            else:
                egress = (osrm_route(a_lat, a_lon, end_lat, end_lon, "Walk")
                          or [[a_lat, a_lon], [end_lat, end_lon]])
            return _concat(access, seg["rail"], egress)
    return osrm_route(start_lat, start_lon, end_lat, end_lon, mode)


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
