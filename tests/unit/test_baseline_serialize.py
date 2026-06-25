"""Unit tests for the shared serializers in `app.baseline`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.baseline import serialize_flag, serialize_trip
from engine.models import Flag, PairEvidence, RoutePoint, Trip

PST = timezone(timedelta(hours=-8))
BASE = datetime(2024, 11, 5, 9, 0, 0, tzinfo=PST)


def mk(row, purpose="", route=None):
    return Trip(
        raw_row=row, person="A", mode="Walk", purpose=purpose,
        start_ts=BASE, end_ts=BASE + timedelta(minutes=15),
        start_lat=49.1, start_lon=-123.1, end_lat=49.2, end_lon=-123.2,
        route=route if route is not None else [],
    )


def test_serialize_trip_basic_fields():
    d = serialize_trip(mk(3, purpose="Work"), seq=2)
    assert d["seq"] == 2 and d["id"] == 3
    assert d["mode"] == "Walk" and d["purpose"] == "Work"
    assert d["start"] == "09:00" and d["end"] == "09:15" and d["dur"] == 15.0
    assert d["s"] == [49.1, -123.1] and d["e"] == [49.2, -123.2]


def test_serialize_trip_falls_back_to_endpoints_without_route():
    d = serialize_trip(mk(1), seq=1)
    assert d["route"] == [[49.1, -123.1], [49.2, -123.2]]
    assert d["r0_time"] is None and d["rN_time"] is None


def test_serialize_trip_uses_route_when_present():
    route = [RoutePoint(49.15, -123.15, BASE)]
    d = serialize_trip(mk(1, route=route), seq=1)
    assert d["route"] == [[49.15, -123.15]]
    assert d["r0_time"] == "09:00" and d["rN_time"] == "09:00"


def test_serialize_pair_flag_maps_seqs_and_status():
    ev = PairEvidence(gap_distance_m=300, gap_time_min=5, v_a_kmh=30, v_b_kmh=30,
                      residual_min=1.4, buffer_min=3.1)
    f = Flag(flag_type="fragment", left=(2,), right=(5,), flag_id="abc123")
    f = type(f)(**{**f.__dict__, "evidence": ev})
    d = serialize_flag(f, seq_of={2: 1, 5: 2}, status="pending")
    assert d["id"] == "abc123" and d["type"] == "fragment"
    assert d["seqs"] == [1, 2] and d["status"] == "pending"
    assert d["ev"]["gap_distance_m"] == 300 and d["ev"]["residual_min"] == 1.4


def test_serialize_flag_unknown_member_gets_none_seq():
    f = Flag(flag_type="missing_trip", left=(2,), right=(9,), flag_id="zz")
    d = serialize_flag(f, seq_of={2: 1}, status="accepted")  # 9 no longer a live trip
    assert d["seqs"] == [1, None]


def test_serialize_zero_move_flag_single_seq():
    f = Flag(flag_type="zero_move", trip=4, flag_id="z4")
    d = serialize_flag(f, seq_of={4: 3})
    assert d["seqs"] == [3] and "status" not in d  # status omitted when not given
