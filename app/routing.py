"""Network-route enrichment for map display of synthetic/merged trips."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import replace

from engine.geo import haversine_m
from engine.models import RoutePoint, Trip

OSRM = "https://router.project-osrm.org/route/v1"
_CACHE: dict = {}


def enrich_live_routes(live_trips: list[Trip], original_trips: list[Trip]) -> list[Trip]:
    """Add display routes for synthetic trips and accepted fragment merges.

    The classifier/fixpoint operates on the original geometry. This function runs
    after that, just before JSON serialization, so a routing-service miss cannot
    change decisions. It only improves map geometry.
    """
    original = {t.raw_row: t for t in original_trips}
    out = []
    for trip in live_trips:
        route = trip.route
        if len(route) < 2:
            route = route_between(
                trip.start_lat, trip.start_lon, trip.end_lat, trip.end_lon,
                trip.start_ts, trip.end_ts, trip.mode
            )
        elif _is_merged_fragment(trip, original):
            route = _fill_stitched_gaps(route, trip.mode)
        out.append(replace(trip, route=route) if route is not trip.route else trip)
    return out


def route_between(start_lat: float, start_lon: float, end_lat: float, end_lon: float,
                  start_ts, end_ts, mode: str) -> list[RoutePoint]:
    coords = _osrm_coords(start_lat, start_lon, end_lat, end_lon, mode)
    if len(coords) < 2:
        return []
    return _timed_points(coords, start_ts, end_ts)


def _is_merged_fragment(trip: Trip, original: dict) -> bool:
    base = original.get(trip.raw_row)
    return base is not None and (
        trip.start_ts != base.start_ts
        or trip.end_ts != base.end_ts
        or len(trip.route) > len(base.route)
    )


def _fill_stitched_gaps(route: list[RoutePoint], mode: str) -> list[RoutePoint]:
    if len(route) < 2:
        return route
    filled = [route[0]]
    changed = False
    for prev, cur in zip(route, route[1:]):
        gap_m = haversine_m(prev.lat, prev.lon, cur.lat, cur.lon)
        gap_s = (cur.ts - prev.ts).total_seconds()
        if gap_m > 250 and gap_s > 30:
            insert = route_between(prev.lat, prev.lon, cur.lat, cur.lon, prev.ts, cur.ts, mode)
            if len(insert) > 2:
                filled.extend(insert[1:])
                changed = True
                continue
        filled.append(cur)
    return filled if changed else route


def _osrm_coords(start_lat: float, start_lon: float, end_lat: float, end_lon: float,
                 mode: str) -> list[tuple[float, float]]:
    profiles = []
    if mode in ("Walk", "Run"):
        profiles.append("foot")
    elif mode == "Bike":
        profiles.append("bike")
    profiles.append("driving")

    for profile in dict.fromkeys(profiles):
        key = (
            profile,
            round(start_lat, 6), round(start_lon, 6),
            round(end_lat, 6), round(end_lon, 6),
        )
        if key in _CACHE:
            return _CACHE[key]
        query = urllib.parse.urlencode({"overview": "full", "geometries": "geojson"})
        url = f"{OSRM}/{profile}/{start_lon},{start_lat};{end_lon},{end_lat}?{query}"
        try:
            with urllib.request.urlopen(url, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            _CACHE[key] = []
            continue
        routes = data.get("routes") or []
        coords = routes[0].get("geometry", {}).get("coordinates", []) if routes else []
        points = [(float(lat), float(lon)) for lon, lat in coords]
        _CACHE[key] = points
        if points:
            return points
    return []


def _timed_points(coords: list[tuple[float, float]], start_ts, end_ts) -> list[RoutePoint]:
    if len(coords) < 2:
        return []
    step = (end_ts - start_ts) / (len(coords) - 1)
    return [RoutePoint(lat, lon, start_ts + step * i) for i, (lat, lon) in enumerate(coords)]
