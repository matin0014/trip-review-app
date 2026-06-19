"""Unit tests for `engine.classify` 2c — grouping + the day driver."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.classify import classify_all, classify_day, group_person_days
from engine.geo import EARTH_RADIUS_M
from engine.models import RoutePoint, Trip

PST = timezone(timedelta(hours=-8))
ONE_DEGREE_M = EARTH_RADIUS_M * 3.141592653589793 / 180.0


def dlon(m: float) -> float:
    return m / ONE_DEGREE_M


def route_for_speed(v_kmh: float, day: datetime) -> list:
    dist_m = v_kmh / 3.6 * 100.0
    return [RoutePoint(0.0, -100.0, day),
            RoutePoint(0.0, -100.0 + dlon(dist_m), day + timedelta(seconds=100))]


def stationary(day: datetime) -> list:
    return [RoutePoint(0.0, -100.0, day),
            RoutePoint(0.0, -100.0 + dlon(0.05), day + timedelta(seconds=100))]


def trip(raw, person, day, start_min, end_min, end_lon_m, start_lon_m=0.0, route=None):
    base = day + timedelta(minutes=start_min)
    return Trip(
        raw_row=raw, person=person, mode="x", purpose="",
        start_ts=base, end_ts=day + timedelta(minutes=end_min),
        start_lat=0.0, start_lon=dlon(start_lon_m),
        end_lat=0.0, end_lon=dlon(end_lon_m),
        route=route if route is not None else route_for_speed(30.0, day),
    )


D1 = datetime(2024, 11, 5, 9, 0, 0, tzinfo=PST)
D2 = datetime(2024, 11, 6, 9, 0, 0, tzinfo=PST)


def test_group_person_days_splits_and_sorts() -> None:
    a2 = trip(2, "A", D1, 60, 65, end_lon_m=2000)
    a1 = trip(1, "A", D1, 10, 15, end_lon_m=1000)
    b1 = trip(3, "B", D1, 10, 15, end_lon_m=1000)
    days = group_person_days([a2, a1, b1])
    assert set(days) == {("A", D1.date()), ("B", D1.date())}
    assert [t.raw_row for t in days[("A", D1.date())]] == [1, 2]  # sorted by start


def test_classify_day_emits_pair_and_zero_move() -> None:
    # A fragment pair (gap 1000 m, 2 min) + a separate stationary (zero_move) trip.
    prev = trip(1, "A", D1, 8, 10, end_lon_m=0.0)
    cur = trip(2, "A", D1, 12, 17, end_lon_m=1050, start_lon_m=1000)
    # Stationary trip sitting AT cur's endpoint (gap ~0 -> contiguous, no pair flag).
    zm = trip(3, "A", D1, 20, 25, end_lon_m=1050, start_lon_m=1050, route=stationary(D1))
    flags = classify_day([prev, cur, zm])
    kinds = sorted(f.flag_type for f in flags)
    assert kinds == ["fragment", "zero_move"]


def test_classify_all_no_cross_day_pairs() -> None:
    # Same person, two single-trip days that WOULD pair into a fragment if adjacent.
    t1 = trip(1, "A", D1, 10, 12, end_lon_m=0.0)
    t2 = trip(2, "A", D2, 10, 15, end_lon_m=1050, start_lon_m=1000)
    flags = classify_all([t1, t2])
    assert flags == []  # no pair spans the day boundary; neither is zero_move
