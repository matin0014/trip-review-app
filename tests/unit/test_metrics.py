"""Unit tests for `app.metrics.precision_table` — TP/FP tallying per detector.

Uses the same synthetic-route trick as `test_classify_ladder.py` (a leg's
speed comes from a single route segment spanning a known distance/duration,
matching engine's real haversine math via `EARTH_RADIUS_M`), monkeypatching
`load_trips` so no real workbook is needed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import app.metrics as metrics
from engine.geo import EARTH_RADIUS_M
from engine.models import RoutePoint, Trip

PST = timezone(timedelta(hours=-8))
BASE = datetime(2024, 11, 5, 12, 0, 0, tzinfo=PST)
ONE_DEGREE_M = EARTH_RADIUS_M * 3.141592653589793 / 180.0


def dlon(m):
    return m / ONE_DEGREE_M


def route_for_speed(v_kmh):
    dist_m = v_kmh / 3.6 * 100.0
    return [RoutePoint(0.0, -100.0, BASE), RoutePoint(0.0, -100.0 + dlon(dist_m), BASE + timedelta(seconds=100))]


def leg(raw, v_kmh, end_lon_m, start_min, end_min, start_lon_m=0.0):
    return Trip(raw_row=raw, person="p", mode="x", purpose="",
                start_ts=BASE + timedelta(minutes=start_min), end_ts=BASE + timedelta(minutes=end_min),
                start_lat=0.0, start_lon=dlon(start_lon_m), end_lat=0.0, end_lon=dlon(end_lon_m),
                route=route_for_speed(v_kmh))


def fragment_pair():
    # same-speed-class, <200m/<3min gate -> fragment, origin=ingest
    prev = leg(1, 30, end_lon_m=0.0, start_min=0, end_min=10)
    cur = leg(2, 30, end_lon_m=1050, start_min=12, end_min=17, start_lon_m=1000)
    return [prev, cur]


def mode_change_pair():
    # walk-speed leg then car-speed leg, gap lands in the residual band -> mode_change
    prev = leg(1, 5, end_lon_m=0.0, start_min=8, end_min=10)
    cur = leg(2, 30, end_lon_m=1050, start_min=23, end_min=28, start_lon_m=1000)
    return [prev, cur]


def missing_activity_pair():
    # small gap (1000m) but a huge time gap (40min) -> residual far past
    # buffer+missing_band -> missing_activity (unlike missing_trip's smaller gap)
    prev = leg(1, 30, end_lon_m=0.0, start_min=0, end_min=10)
    cur = leg(2, 30, end_lon_m=1050, start_min=50, end_min=55, start_lon_m=1000)
    return [prev, cur]


def dec(flag_id, resolution):
    return {"type": "decision", "flag_id": flag_id, "resolution": resolution}


def _flag_id(trips, ftype):
    from engine.classify import classify_day
    from engine.ids import with_ids
    return next(f.flag_id for f in with_ids(classify_day(trips)) if f.flag_type == ftype)


def test_accepted_fragment_counts_as_tp_ingest(monkeypatch):
    trips = fragment_pair()
    monkeypatch.setattr(metrics, "load_trips", lambda path: trips)
    events = [dec(_flag_id(trips, "fragment"), "accept")]
    table = metrics.precision_table("unused", events)
    assert table[("fragment", "ingest")] == {"tp": 1, "fp": 0, "precision": 1.0}


def test_waived_fragment_counts_as_fp(monkeypatch):
    trips = fragment_pair()
    monkeypatch.setattr(metrics, "load_trips", lambda path: trips)
    events = [dec(_flag_id(trips, "fragment"), "waive")]
    table = metrics.precision_table("unused", events)
    assert table[("fragment", "ingest")] == {"tp": 0, "fp": 1, "precision": 0.0}


def test_merged_mode_change_counts_as_tp(monkeypatch):
    trips = mode_change_pair()
    monkeypatch.setattr(metrics, "load_trips", lambda path: trips)
    events = [dec(_flag_id(trips, "mode_change"), "merge")]
    table = metrics.precision_table("unused", events)
    assert table[("mode_change", "ingest")]["tp"] == 1
    assert table[("mode_change", "ingest")]["fp"] == 0


def test_reviewed_missing_activity_is_not_scored(monkeypatch):
    trips = missing_activity_pair()
    monkeypatch.setattr(metrics, "load_trips", lambda path: trips)
    events = [dec(_flag_id(trips, "missing_activity"), "reviewed")]
    table = metrics.precision_table("unused", events)
    assert ("missing_activity", "ingest") not in table


def test_undecided_flag_is_not_counted(monkeypatch):
    trips = fragment_pair()
    monkeypatch.setattr(metrics, "load_trips", lambda path: trips)
    table = metrics.precision_table("unused", [])
    assert table == {}
