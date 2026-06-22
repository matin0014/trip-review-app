"""Unit tests for `app.generate.clean_day` — accepted state -> cleaned trip_ids."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.generate import clean_day
from engine.classify import classify_day
from engine.geo import EARTH_RADIUS_M
from engine.ids import with_ids
from engine.models import RoutePoint, Trip

PST = timezone(timedelta(hours=-8))
BASE = datetime(2024, 11, 5, 12, 0, 0, tzinfo=PST)
ONE_DEGREE_M = EARTH_RADIUS_M * 3.141592653589793 / 180.0


def dlon(m): return m / ONE_DEGREE_M
def route_for_speed(v):
    d = v / 3.6 * 100.0
    return [RoutePoint(0.0, -100.0, BASE), RoutePoint(0.0, -100.0 + dlon(d), BASE + timedelta(seconds=100))]


def leg(raw, v, smin, emin, slon_m, elon_m, route=None):
    return Trip(raw_row=raw, person="p", mode="x", purpose="",
                start_ts=BASE + timedelta(minutes=smin), end_ts=BASE + timedelta(minutes=emin),
                start_lat=0.0, start_lon=dlon(slon_m), end_lat=0.0, end_lon=dlon(elon_m),
                route=route if route is not None else route_for_speed(v))


def fid(trips, ftype):
    return next(f.flag_id for f in with_ids(classify_day(trips)) if f.flag_type == ftype)


# fragment(1,2) + zero_move(2): leg 2 is a short low-speed hop
def conflict_day():
    return [leg(1, 5, 0, 10, 0.0, 300.0), leg(2, 5, 12, 17, 1000.0, 1050.0)]


def test_no_decisions_all_keeps():
    trips = [leg(1, 30, 0, 5, 0.0, 0.0), leg(2, 30, 7, 12, 100.0, 150.0)]  # contiguous, no flags
    rows, stubs = clean_day(trips, set())
    assert rows == [(1, "1"), (2, "2")] and stubs == []


def test_accept_fragment_shares_merge_id():
    trips = conflict_day()
    rows, stubs = clean_day(trips, {fid(trips, "fragment")})
    assert rows == [(1, "M1"), (2, "M1")] and stubs == []


def test_accept_zero_move_drops_the_row():
    trips = conflict_day()
    rows, _ = clean_day(trips, {fid(trips, "zero_move")})
    assert rows == [(1, "1")]   # row 2 removed -> excluded from cleaned file


def test_merge_wins_both_accepted_keeps_both_as_merge():
    trips = conflict_day()
    rows, _ = clean_day(trips, {fid(trips, "fragment"), fid(trips, "zero_move")})
    assert rows == [(1, "M1"), (2, "M1")]   # zero_move did NOT remove the merged piece


def test_accept_missing_trip_yields_a_stub():
    trips = [leg(1, 30, 8, 10, 0.0, 0.0), leg(2, 30, 20, 25, 1000.0, 1050.0)]  # 10-min gap
    rows, stubs = clean_day(trips, {fid(trips, "missing_trip")})
    assert rows == [(1, "1"), (2, "2")] and stubs == [(1, 2)]
