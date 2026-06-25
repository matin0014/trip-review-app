"""Unit tests for `app.generate.clean_day` — accepted state -> cleaned trip_ids."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.generate import _extra_ids, clean_day
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


def test_manual_mode_override_for_a_kept_trip():
    trips = [leg(1, 30, 0, 5, 0.0, 0.0), leg(2, 30, 7, 12, 100.0, 150.0)]
    op = {"type": "manual_mode", "raw_row": 2, "mode": "Bike", "day_key": "p|2024-11-05"}
    *_, mode_overrides = clean_day(trips, set(), day_ops=[op])
    assert mode_overrides == {2: "Bike"}


def test_manual_mode_on_merged_trip_relabels_all_members():
    # accept the fragment so rows 1 and 2 share an M-id, then relabel the group
    trips = conflict_day()
    ops = [{"type": "manual_mode", "raw_row": 1, "mode": "Bike", "day_key": "p|2024-11-05"}]
    *_, mode_overrides = clean_day(trips, {fid(trips, "fragment")}, day_ops=ops)
    assert mode_overrides == {1: "Bike", 2: "Bike"}   # both members, option (b)


def test_manual_purpose_override_for_a_kept_trip():
    trips = [leg(1, 30, 0, 5, 0.0, 0.0), leg(2, 30, 7, 12, 100.0, 150.0)]
    op = {"type": "manual_purpose", "raw_row": 2, "purpose": "Work", "day_key": "p|2024-11-05"}
    *_, purpose_overrides, _mode_overrides = clean_day(trips, set(), day_ops=[op])
    assert purpose_overrides == {2: "Work"}


def test_manual_purpose_on_merged_trip_relabels_all_members():
    trips = conflict_day()
    ops = [{"type": "manual_purpose", "raw_row": 1, "purpose": "School", "day_key": "p|2024-11-05"}]
    *_, purpose_overrides, _mode_overrides = clean_day(trips, {fid(trips, "fragment")}, day_ops=ops)
    assert purpose_overrides == {1: "School", 2: "School"}


def test_no_decisions_all_keeps():
    trips = [leg(1, 30, 0, 5, 0.0, 0.0), leg(2, 30, 7, 12, 100.0, 150.0)]  # contiguous, no flags
    rows, stubs, *_ = clean_day(trips, set())
    assert rows == [(1, "1"), (2, "2")] and stubs == []


def test_accept_fragment_shares_merge_id():
    trips = conflict_day()
    rows, stubs, *_ = clean_day(trips, {fid(trips, "fragment")})
    assert rows == [(1, "M1"), (2, "M1")] and stubs == []


def test_accept_zero_move_drops_the_row():
    trips = conflict_day()
    rows, *_ = clean_day(trips, {fid(trips, "zero_move")})
    assert rows == [(1, "1")]   # row 2 removed -> excluded from cleaned file


def test_merge_wins_both_accepted_keeps_both_as_merge():
    trips = conflict_day()
    rows, *_ = clean_day(trips, {fid(trips, "fragment"), fid(trips, "zero_move")})
    assert rows == [(1, "M1"), (2, "M1")]   # zero_move did NOT remove the merged piece


def test_accept_missing_trip_yields_an_add_stub():
    trips = [leg(1, 30, 8, 10, 0.0, 0.0), leg(2, 30, 20, 25, 1000.0, 1050.0)]  # 10-min gap
    rows, extras, *_ = clean_day(trips, {fid(trips, "missing_trip")})
    assert rows == [(1, "1"), (2, "2")]
    assert len(extras) == 1
    kind, ref, stub = extras[0]
    assert kind == "add" and ref is None
    assert stub.route == [] and stub.mode == "Unknown"
    assert stub.start_ts == trips[0].end_ts and stub.end_ts == trips[1].start_ts  # A.end->B.start


def test_reviewed_missing_activity_yields_a_q_gap():
    trips = [leg(1, 30, 8, 10, 0.0, 0.0), leg(2, 30, 40, 45, 1000.0, 1050.0)]  # 30-min gap
    rows, extras, *_ = clean_day(trips, {fid(trips, "missing_activity")})
    assert rows == [(1, "1"), (2, "2")]                  # reviewed = no geometry change
    assert len(extras) == 1
    kind, ref, gap = extras[0]
    assert kind == "review" and ref == 1                 # Q tagged to the trip before the gap
    assert gap.start_ts == trips[0].end_ts and gap.end_ts == trips[1].start_ts


def test_waived_flag_is_not_in_accepted_so_no_row():
    # a missing_activity left undecided/waived isn't in accepted_ids -> no extras
    trips = [leg(1, 30, 8, 10, 0.0, 0.0), leg(2, 30, 40, 45, 1000.0, 1050.0)]
    rows, extras, *_ = clean_day(trips, set())
    assert rows == [(1, "1"), (2, "2")] and extras == []


# --- manual split / add export projection ---------------------------------

def test_manual_split_yields_s_a_row_b_extra_and_override():
    # BASE is 12:00:00 PST; the leg's 2 route points sit at BASE and BASE+100s.
    trips = [leg(1, 30, 0, 5, 0.0, 0.0)]
    op = {"type": "manual_split", "raw_row": 1, "day_key": "p|2024-11-05",
          "split_ts": "2024-11-05T12:00:50", "mode_a": "Walk", "mode_b": "Auto"}
    rows, extras, _surf, overrides, _purposes, _modes = clean_day(trips, set(), day_ops=[op])

    assert rows == [(1, "S1-A")]                  # origin row kept as the A piece
    assert 1 in overrides and overrides[1].mode == "Walk"   # truncated A overwrite
    assert len(extras) == 1
    kind, ref, b = extras[0]
    assert kind == "split" and ref == 1 and b.mode == "Auto"


def test_manual_add_is_an_a_extra():
    trips = [leg(1, 30, 0, 5, 0.0, 0.0)]
    op = {"type": "manual_add", "day_key": "p|2024-11-05",
          "start_ts": "2024-11-05T13:00:00", "end_ts": "2024-11-05T13:10:00",
          "mode": "Bus", "purpose": "",
          "start_lat": 0.0, "start_lon": 0.0, "end_lat": 0.0, "end_lon": 0.0}
    rows, extras, *_ = clean_day(trips, set(), day_ops=[op])
    assert rows == [(1, "1")]                      # untouched original
    assert len(extras) == 1 and extras[0][0] == "add"
    assert extras[0][2].mode == "Bus"


def test_extra_ids_labels_split_b():
    b = leg(99, 30, 0, 5, 0.0, 0.0)
    (trip_id, kind, _trip), = _extra_ids([("split", 7, b)])
    assert trip_id == "S7-B" and kind == "split"
