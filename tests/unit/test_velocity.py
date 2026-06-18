"""Unit tests for `engine.velocity` — segment speeds, median moving speed,
the stationary (zero_move) signature, and the displacement/duration fallback."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median

import pytest

from engine.geo import EARTH_RADIUS_M, haversine_m
from engine.models import RoutePoint, Trip
from engine.velocity import pair_speeds, segment_speeds, trip_moving_speed

PST = timezone(timedelta(hours=-8))
BASE = datetime(2024, 11, 5, 18, 0, 0, tzinfo=PST)
ONE_DEGREE_M = EARTH_RADIUS_M * 3.141592653589793 / 180.0


def rp(lat: float, lon: float, sec: float) -> RoutePoint:
    return RoutePoint(lat, lon, BASE + timedelta(seconds=sec))


def make_trip(
    route: list,
    start: tuple = (0.0, 0.0),
    end: tuple = (0.0, 0.0),
    dur_s: float = 600.0,
) -> Trip:
    return Trip(
        raw_row=1,
        person="p",
        mode="Walk",
        purpose="",
        start_ts=BASE,
        end_ts=BASE + timedelta(seconds=dur_s),
        start_lat=start[0],
        start_lon=start[1],
        end_lat=end[0],
        end_lon=end[1],
        route=route,
    )


def test_segment_speed_known_value() -> None:
    # 0.01 deg of longitude at the equator over 100 s.
    route = [rp(0.0, 0.0, 0), rp(0.0, 0.01, 100)]
    expected = (ONE_DEGREE_M * 0.01) / 100.0 * 3.6
    assert segment_speeds(route)[0] == pytest.approx(expected, rel=1e-6)


def test_non_positive_dt_skipped() -> None:
    # Two points at the same instant produce no segment (no divide-by-zero).
    route = [rp(0.0, 0.0, 0), rp(0.0, 0.01, 0)]
    assert segment_speeds(route) == []


def test_moving_speed_is_median_excluding_glitch() -> None:
    # Three moderate segments + one >150 km/h glitch (1 deg in 5 s ~ 80,000 km/h).
    route = [
        rp(0.0, 0.000, 0),
        rp(0.0, 0.010, 100),
        rp(0.0, 0.020, 200),
        rp(0.0, 0.035, 300),
        rp(0.0, 1.035, 305),  # glitch jump
    ]
    trip = make_trip(route)
    speeds = segment_speeds(route)
    moving = [s for s in speeds if 2.0 <= s <= 150.0]
    assert max(speeds) > 150.0  # the glitch really is out of band
    assert len(moving) == 3  # glitch excluded
    assert trip_moving_speed(trip) == pytest.approx(median(moving), rel=1e-9)


def test_stationary_jitter_reads_zero() -> None:
    # Real but sub-floor movement (<2 km/h) at every segment -> stationary.
    route = [rp(0.0, 0.0, 0), rp(0.0, 0.000001, 100), rp(0.0, 0.000002, 200)]
    assert trip_moving_speed(make_trip(route)) == 0.0


def test_all_glitch_route_falls_back_to_displacement() -> None:
    # Every segment is a >150 glitch -> fall back to declared displacement/duration.
    route = [rp(0.0, 0.0, 0), rp(0.0, 1.0, 1), rp(0.0, 2.0, 2)]
    trip = make_trip(route, start=(0.0, 0.0), end=(0.0, 0.1), dur_s=3600.0)
    expected = haversine_m(0.0, 0.0, 0.0, 0.1) / 3600.0 * 3.6
    assert trip_moving_speed(trip) == pytest.approx(expected, rel=1e-6)


def test_sparse_route_falls_back() -> None:
    trip = make_trip([], start=(0.0, 0.0), end=(0.0, 0.1), dur_s=3600.0)
    one_point = make_trip([rp(0.0, 0.0, 0)], start=(0.0, 0.0), end=(0.0, 0.1), dur_s=3600.0)
    expected = haversine_m(0.0, 0.0, 0.0, 0.1) / 3600.0 * 3.6
    assert trip_moving_speed(trip) == pytest.approx(expected, rel=1e-6)
    assert trip_moving_speed(one_point) == pytest.approx(expected, rel=1e-6)


def test_round_trip_is_spared_not_zero() -> None:
    # Net displacement ~0 (returns to start) but real moving segments -> median
    # reflects real speed, so a round trip is NOT mistaken for stationary.
    route = [rp(0.0, 0.0, 0), rp(0.0, 0.02, 100), rp(0.0, 0.0, 200)]
    speed = trip_moving_speed(make_trip(route, start=(0.0, 0.0), end=(0.0, 0.0)))
    assert speed > 10.0


def test_fallback_zero_duration_is_zero() -> None:
    trip = make_trip([], start=(0.0, 0.0), end=(0.0, 0.1), dur_s=0.0)
    assert trip_moving_speed(trip) == 0.0


def test_pair_speeds_maps_to_each_leg() -> None:
    a = make_trip([rp(0.0, 0.0, 0), rp(0.0, 0.01, 100)])
    b = make_trip([rp(0.0, 0.0, 0), rp(0.0, 0.02, 100)])
    v_a, v_b = pair_speeds(a, b)
    assert v_a == pytest.approx(trip_moving_speed(a))
    assert v_b == pytest.approx(trip_moving_speed(b))
    assert v_b > v_a  # b moved twice as far in the same time
