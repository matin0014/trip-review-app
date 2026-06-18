"""Unit tests for `engine.classify` 2a — conversion helper + pair geometry."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine.classify import expected_time_min, pair_geometry
from engine.geo import EARTH_RADIUS_M, haversine_m
from engine.models import RoutePoint, Trip
from engine.params import DEFAULT as P
from engine.velocity import trip_moving_speed

PST = timezone(timedelta(hours=-8))
BASE = datetime(2024, 11, 5, 12, 0, 0, tzinfo=PST)
ONE_DEGREE_M = EARTH_RADIUS_M * 3.141592653589793 / 180.0


def rp(lat: float, lon: float, sec: float) -> RoutePoint:
    return RoutePoint(lat, lon, BASE + timedelta(seconds=sec))


def leg(route: list, end_xy: tuple, start_off_s: float, end_off_s: float) -> Trip:
    """A trip whose declared endpoint/timing we set explicitly for gap math."""
    return Trip(
        raw_row=1, person="p", mode="Walk", purpose="",
        start_ts=BASE + timedelta(seconds=start_off_s),
        end_ts=BASE + timedelta(seconds=end_off_s),
        start_lat=0.0, start_lon=0.0, end_lat=end_xy[0], end_lon=end_xy[1],
        route=route,
    )


def test_expected_time_min_known_values() -> None:
    # 1000 m at 60 km/h = 1 min; 2000 m at 40 km/h = 3 min.
    assert expected_time_min(1000.0, 60.0) == pytest.approx(1.0)
    assert expected_time_min(2000.0, 40.0) == pytest.approx(3.0)


def moving_leg(end_xy: tuple, end_off_s: float) -> Trip:
    # A leg with one real moving segment (so v > 0), declared end at end_xy.
    route = [rp(0.0, 0.0, 0), rp(0.0, 0.005, 100)]
    return leg(route, end_xy, start_off_s=0, end_off_s=end_off_s)


def test_pair_geometry_matches_formulas() -> None:
    prev = moving_leg(end_xy=(0.0, 0.0), end_off_s=200)        # ends at origin, t=200s
    cur = Trip(
        raw_row=2, person="p", mode="Walk", purpose="",
        start_ts=BASE + timedelta(seconds=900),                 # gap = 700 s
        end_ts=BASE + timedelta(seconds=1200),
        start_lat=0.0, start_lon=0.02, end_lat=0.0, end_lon=0.03,
        route=[rp(0.0, 0.02, 0), rp(0.0, 0.03, 100)],
    )
    ev = pair_geometry(prev, cur)

    expected_gap = haversine_m(0.0, 0.0, 0.0, 0.02)
    assert ev.gap_distance_m == pytest.approx(expected_gap)
    assert ev.gap_time_min == pytest.approx(700 / 60.0)
    assert ev.v_a_kmh == pytest.approx(trip_moving_speed(prev))
    assert ev.v_b_kmh == pytest.approx(trip_moving_speed(cur))

    t = expected_time_min(expected_gap, ev.v_a_kmh)
    assert ev.t_min == pytest.approx(t)
    assert ev.buffer_min == pytest.approx(P.buffer_frac * t + P.buffer_const_min)
    assert ev.residual_min == pytest.approx(ev.gap_time_min - t)


def test_residual_is_signed_can_go_negative() -> None:
    # A large gap distance with a tiny gap time -> covered "faster" than v_A -> t
    # large, residual negative. abs() would wrongly hide this.
    prev = moving_leg(end_xy=(0.0, 0.0), end_off_s=100)
    cur = Trip(
        raw_row=2, person="p", mode="Walk", purpose="",
        start_ts=BASE + timedelta(seconds=130),                 # only 30 s gap
        end_ts=BASE + timedelta(seconds=400),
        start_lat=0.0, start_lon=0.5, end_lat=0.0, end_lon=0.51,  # far away
        route=[rp(0.0, 0.5, 0), rp(0.0, 0.51, 100)],
    )
    ev = pair_geometry(prev, cur)
    assert ev.residual_min is not None and ev.residual_min < 0


def test_v_a_zero_leaves_time_fields_none() -> None:
    # Stationary prev leg (sub-floor jitter) -> v_a = 0 -> t/buffer/residual None.
    stationary = leg(
        route=[rp(0.0, 0.0, 0), rp(0.0, 0.000001, 100)],
        end_xy=(0.0, 0.0), start_off_s=0, end_off_s=100,
    )
    cur = moving_leg(end_xy=(0.0, 0.02), end_off_s=900)
    cur = Trip(**{**cur.__dict__, "start_ts": BASE + timedelta(seconds=300),
                  "start_lat": 0.0, "start_lon": 0.02})
    ev = pair_geometry(stationary, cur)
    assert ev.v_a_kmh == 0.0
    assert ev.t_min is None and ev.buffer_min is None and ev.residual_min is None
    # gap + v_b still computed
    assert ev.gap_distance_m > 0 and ev.v_b_kmh > 0 and ev.slack_b_min is not None


def test_v_b_zero_leaves_slack_none() -> None:
    prev = moving_leg(end_xy=(0.0, 0.0), end_off_s=100)
    stationary_cur = leg(
        route=[rp(0.0, 0.02, 0), rp(0.0, 0.020001, 100)],
        end_xy=(0.0, 0.02), start_off_s=300, end_off_s=400,
    )
    stationary_cur = Trip(**{**stationary_cur.__dict__,
                             "start_lat": 0.0, "start_lon": 0.02})
    ev = pair_geometry(prev, stationary_cur)
    assert ev.v_b_kmh == 0.0
    assert ev.slack_b_min is None
    assert ev.t_min is not None  # v_a side still fine
