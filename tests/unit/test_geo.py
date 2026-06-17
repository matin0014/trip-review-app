"""Unit tests for `engine.geo` — the haversine distance."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from engine.geo import EARTH_RADIUS_M, haversine_m, haversine_points
from engine.models import RoutePoint

# One degree of arc on a great circle, in metres (R * pi / 180).
ONE_DEGREE_M = EARTH_RADIUS_M * 3.141592653589793 / 180.0  # ~111194.9


def test_zero_distance_for_same_point() -> None:
    assert haversine_m(40.0, -3.0, 40.0, -3.0) == pytest.approx(0.0, abs=1e-6)


def test_one_degree_longitude_at_equator() -> None:
    # At the equator, 1 degree of longitude == 1 degree of arc.
    d = haversine_m(0.0, 0.0, 0.0, 1.0)
    assert d == pytest.approx(ONE_DEGREE_M, rel=1e-6)


def test_one_degree_latitude() -> None:
    # 1 degree of latitude is ~1 degree of arc anywhere.
    d = haversine_m(0.0, 0.0, 1.0, 0.0)
    assert d == pytest.approx(ONE_DEGREE_M, rel=1e-6)


def test_symmetry() -> None:
    a = haversine_m(51.5074, -0.1278, 48.8566, 2.3522)
    b = haversine_m(48.8566, 2.3522, 51.5074, -0.1278)
    assert a == pytest.approx(b, rel=1e-12)


def test_known_city_distance_london_paris() -> None:
    # Great-circle London -> Paris is ~343 km.
    d = haversine_m(51.5074, -0.1278, 48.8566, 2.3522)
    assert 335_000 < d < 350_000


def test_points_overload_matches_scalar() -> None:
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    p1 = RoutePoint(40.0, -3.0, ts)
    p2 = RoutePoint(40.5, -3.5, ts)
    assert haversine_points(p1, p2) == pytest.approx(
        haversine_m(40.0, -3.0, 40.5, -3.5)
    )
