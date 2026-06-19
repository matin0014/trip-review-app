"""Unit tests for `engine.classify` 2b — the decision ladder + zero_move.

Each pair is crafted to land in exactly one branch. Speeds are set by giving a
leg a single route segment of the desired km/h; the gap distance/time are set by
the declared endpoints and timestamps (independent of the route).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine.classify import classify_pair, classify_trip_zero_move
from engine.geo import EARTH_RADIUS_M
from engine.models import RoutePoint, Trip

PST = timezone(timedelta(hours=-8))
BASE = datetime(2024, 11, 5, 12, 0, 0, tzinfo=PST)
ONE_DEGREE_M = EARTH_RADIUS_M * 3.141592653589793 / 180.0


def dlon(meters: float) -> float:
    return meters / ONE_DEGREE_M


def route_for_speed(v_kmh: float) -> list:
    # One 100 s segment covering the distance that yields v_kmh (route sits far
    # from the declared endpoints; only its internal speed matters).
    dist_m = v_kmh / 3.6 * 100.0
    return [RoutePoint(0.0, -100.0, BASE),
            RoutePoint(0.0, -100.0 + dlon(dist_m), BASE + timedelta(seconds=100))]


def stationary_route() -> list:
    return [RoutePoint(0.0, -100.0, BASE),
            RoutePoint(0.0, -100.0 + dlon(0.05), BASE + timedelta(seconds=100))]


def leg(raw, v_kmh, end_lon_m, start_min, end_min, start_lon_m=0.0, route=None):
    """A leg ending at (0, end_lon_m metres east of 0); speed from its route."""
    return Trip(
        raw_row=raw, person="p", mode="x", purpose="",
        start_ts=BASE + timedelta(minutes=start_min),
        end_ts=BASE + timedelta(minutes=end_min),
        start_lat=0.0, start_lon=dlon(start_lon_m),
        end_lat=0.0, end_lon=dlon(end_lon_m),
        route=route if route is not None else route_for_speed(v_kmh),
    )


def pair(v_a, v_b, gap_m, gap_min, *, va_route=None, vb_route=None):
    """prev ends at lon 0 at t=10min; cur starts `gap_m` east at t=10+gap_min."""
    prev = leg(1, v_a, end_lon_m=0.0, start_min=8, end_min=10, route=va_route)
    cur = leg(2, v_b, end_lon_m=gap_m + 50, start_min=10 + gap_min,
              end_min=10 + gap_min + 5, start_lon_m=gap_m, route=vb_route)
    return prev, cur


def test_contiguous_under_gate() -> None:
    assert classify_pair(*pair(30, 30, gap_m=100, gap_min=2)) is None


def test_fragment_same_speed_class() -> None:
    f = classify_pair(*pair(30, 30, gap_m=1000, gap_min=2))
    assert f is not None and f.flag_type == "fragment"
    assert f.group == 1 and f.left == (1,) and f.right == (2,)


def test_mode_change_walk_to_car() -> None:
    f = classify_pair(*pair(5, 30, gap_m=1000, gap_min=13))
    assert f is not None and f.flag_type == "mode_change"
    assert f.group is None  # never merges


def test_mode_change_car_to_walk_symmetric() -> None:
    f = classify_pair(*pair(30, 5, gap_m=1000, gap_min=3))
    assert f is not None and f.flag_type == "mode_change"


def test_missing_trip_band() -> None:
    f = classify_pair(*pair(30, 30, gap_m=1000, gap_min=10))
    assert f is not None and f.flag_type == "missing_trip"


def test_missing_activity_above_band() -> None:
    f = classify_pair(*pair(30, 30, gap_m=1000, gap_min=30))
    assert f is not None and f.flag_type == "missing_activity"


def test_pathological_negative_gap_time_kept() -> None:
    prev = leg(1, 30, end_lon_m=0.0, start_min=8, end_min=20)
    cur = leg(2, 30, end_lon_m=1050, start_min=10, end_min=15, start_lon_m=1000)
    assert classify_pair(prev, cur) is None  # cur starts before prev ends


def test_pathological_impossible_speed_kept() -> None:
    # 10 km gap in 1 min => ~600 km/h implied -> keep untouched.
    assert classify_pair(*pair(30, 30, gap_m=10000, gap_min=1)) is None


def test_v_a_zero_kept() -> None:
    f = pair(0, 30, gap_m=1000, gap_min=10, va_route=stationary_route())
    assert classify_pair(*f) is None


def test_v_b_zero_kept_no_spurious_mode_change() -> None:
    # cur is stationary (zero_move noise): fast v_A + v_B=0 used to read as a
    # speed-class crossing and emit a spurious mode_change. Now kept (None).
    f = pair(30, 0, gap_m=1000, gap_min=3, vb_route=stationary_route())
    assert classify_pair(*f) is None


def test_zero_move_flagged() -> None:
    # Net displacement 0, stationary route -> speed 0 -> zero_move.
    t = leg(7, 0, end_lon_m=0.0, start_min=0, end_min=5,
            start_lon_m=0.0, route=stationary_route())
    flag = classify_trip_zero_move(t)
    assert flag is not None and flag.flag_type == "zero_move"
    assert flag.trip == 7 and flag.left == () and flag.right == ()


def test_round_trip_not_zero_move() -> None:
    # Returns to start (displacement 0) but real moving segments -> speed > 10.
    route = [RoutePoint(0.0, 0.0, BASE),
             RoutePoint(0.0, dlon(1000), BASE + timedelta(seconds=120)),
             RoutePoint(0.0, 0.0, BASE + timedelta(seconds=240))]
    t = leg(8, 0, end_lon_m=0.0, start_min=0, end_min=5, start_lon_m=0.0, route=route)
    assert classify_trip_zero_move(t) is None


def test_long_trip_not_zero_move() -> None:
    # Displacement 300 m (> 200) -> not zero_move even if slow.
    t = leg(9, 5, end_lon_m=300.0, start_min=0, end_min=20, start_lon_m=0.0)
    assert classify_trip_zero_move(t) is None
