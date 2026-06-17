"""Geographic distance helpers — pure functions.

Depends only on the standard library (and `models` for the convenience overload).
Everything here is a referentially-transparent function of its inputs.
"""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

from engine.models import RoutePoint

EARTH_RADIUS_M = 6_371_000.0
"""Mean Earth radius (metres) — the physical constant the haversine uses."""


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in metres.

    Uses the haversine formula with the `asin(sqrt(...))` form, which is stable
    for the small distances we deal with (consecutive GPS points).
    """
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


def haversine_points(p1: RoutePoint, p2: RoutePoint) -> float:
    """Great-circle distance between two `RoutePoint`s, in metres."""
    return haversine_m(p1.lat, p1.lon, p2.lat, p2.lon)
