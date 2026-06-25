"""Unit tests for `app.decisions.fixpoint` — per-day apply + cascade recompute.

Trips are crafted to classify into a known flag (same helpers as the ladder
tests); we then accept that flag *by its real content id* and assert what the
live recompute produces, including a cascade-born flag on freshly-adjacent legs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.decisions import fixpoint
from engine.classify import classify_day
from engine.geo import EARTH_RADIUS_M
from engine.ids import with_ids
from engine.models import RoutePoint, Trip

PST = timezone(timedelta(hours=-8))
BASE = datetime(2024, 11, 5, 12, 0, 0, tzinfo=PST)
ONE_DEGREE_M = EARTH_RADIUS_M * 3.141592653589793 / 180.0


def dlon(m: float) -> float:
    return m / ONE_DEGREE_M


def route_for_speed(v_kmh: float) -> list:
    dist_m = v_kmh / 3.6 * 100.0
    return [RoutePoint(0.0, -100.0, BASE),
            RoutePoint(0.0, -100.0 + dlon(dist_m), BASE + timedelta(seconds=100))]


def stationary_route() -> list:
    return [RoutePoint(0.0, -100.0, BASE),
            RoutePoint(0.0, -100.0 + dlon(0.05), BASE + timedelta(seconds=100))]


def leg(raw, v_kmh, start_min, end_min, start_lon_m, end_lon_m, route=None):
    return Trip(
        raw_row=raw, person="p", mode="x", purpose="",
        start_ts=BASE + timedelta(minutes=start_min),
        end_ts=BASE + timedelta(minutes=end_min),
        start_lat=0.0, start_lon=dlon(start_lon_m),
        end_lat=0.0, end_lon=dlon(end_lon_m),
        route=route if route is not None else route_for_speed(v_kmh),
    )


def surfaced_flags(trips):
    return with_ids(classify_day(trips))


def of_type(flags, ftype):
    return next(f for f in flags if f.flag_type == ftype)


def test_manual_mode_relabel_survives_the_cascade():
    # A label-only edit changes no geometry, so the convergence signature must
    # still register it; otherwise the loop "converges" and drops the relabel.
    trips = [leg(1, 30, 8, 10, 0.0, 0.0)]
    op = {"type": "manual_mode", "raw_row": 1, "mode": "Bike", "day_key": "p|d"}
    live, _ = fixpoint(trips, set(), ops=[op])
    assert [t.mode for t in live] == ["Bike"]


# ---- a fragment-able pair (same speed class, gap 1000 m, 2 min) ----
def fragment_day():
    return [leg(1, 30, 8, 10, 0.0, 0.0),
            leg(2, 30, 12, 17, 1000.0, 1050.0)]


def test_no_accepted_returns_baseline_unchanged():
    trips = fragment_day()
    live, surfaced = fixpoint(trips, accepted_ids=set())
    assert [t.raw_row for t in live] == [1, 2]            # nothing applied
    assert of_type(surfaced, "fragment")                  # but the flag is surfaced (pending)


def test_accept_fragment_merges_into_one_trip():
    trips = fragment_day()
    frag = of_type(surfaced_flags(trips), "fragment")
    live, _ = fixpoint(trips, accepted_ids={frag.flag_id})
    assert len(live) == 1
    assert live[0].raw_row == 1                            # merged, group root
    assert live[0].start_ts == trips[0].start_ts and live[0].end_ts == trips[1].end_ts


def test_accept_missing_trip_inserts_stub_and_converges():
    # same-class pair with a 10-min gap -> missing_trip
    trips = [leg(1, 30, 8, 10, 0.0, 0.0), leg(2, 30, 20, 25, 1000.0, 1050.0)]
    mt = of_type(surfaced_flags(trips), "missing_trip")
    live, surfaced = fixpoint(trips, accepted_ids={mt.flag_id})
    assert len(live) == 3                                  # stub inserted
    stub = [t for t in live if t.raw_row < 0][0]
    assert stub.route == [] and stub.start_ts == trips[0].end_ts
    assert [t.raw_row for t in live] == [1, -1, 2]         # stub sits chronologically between
    # stub adjacencies are zero-gap -> no new cascade flags beyond the original
    assert {f.flag_type for f in surfaced} == {"missing_trip"}


def test_cascade_born_flag_surfaces_after_removal():
    # A ends at origin; B sits stationary at origin (zero_move); C is far/late.
    # Baseline: B's zero speed keeps both its pairs unflagged -> only zero_move(B).
    # Removing B makes A & C adjacent across a big gap -> a NEW missing_activity.
    trips = [leg(1, 30, 0, 5, -1000.0, 0.0),
             leg(2, 0, 10, 15, 0.0, 0.0, route=stationary_route()),
             leg(3, 30, 210, 215, 5000.0, 6000.0)]

    baseline = surfaced_flags(trips)
    assert {f.flag_type for f in baseline} == {"zero_move"}   # no pair flags yet
    zm = of_type(baseline, "zero_move")

    live, surfaced = fixpoint(trips, accepted_ids={zm.flag_id})
    assert [t.raw_row for t in live] == [1, 3]               # B removed, A & C remain

    cascade = of_type(surfaced, "missing_activity")          # born on the new A-C adjacency
    assert cascade.left == (1,) and cascade.right == (3,)
    assert cascade.flag_id not in {f.flag_id for f in baseline}  # genuinely new


# A fragment whose second piece is also a zero_move (the aixz-awv-wa 2024-11-12
# trap): leg 1 moves 300 m, leg 2 is a short 50 m hop -> fragment(1,2) + zero_move(2).
def conflict_day():
    return [leg(1, 5, 0, 10, 0.0, 300.0), leg(2, 5, 12, 17, 1000.0, 1050.0)]


def test_accept_fragment_and_zero_move_merge_wins():
    trips = conflict_day()
    flags = surfaced_flags(trips)
    frag, zm = of_type(flags, "fragment"), of_type(flags, "zero_move")
    # accept BOTH -> the merge rescues the piece instead of deleting it
    live, _ = fixpoint(trips, {frag.flag_id, zm.flag_id})
    assert len(live) == 1
    assert live[0].start_ts == trips[0].start_ts and live[0].end_ts == trips[1].end_ts


def test_accepted_fragment_drops_its_pieces_zero_move():
    # accepting the fragment swallows piece 2 -> its zero_move must NOT linger
    trips = conflict_day()
    base = surfaced_flags(trips)
    assert any(f.flag_type == "zero_move" for f in base)   # it exists at baseline
    frag = of_type(base, "fragment")
    _, surfaced = fixpoint(trips, {frag.flag_id})
    types = {f.flag_type for f in surfaced}
    assert "fragment" in types and "zero_move" not in types  # frag kept, zero_move gone


def test_accept_fragment_alone_also_merges():
    trips = conflict_day()
    frag = of_type(surfaced_flags(trips), "fragment")
    live, _ = fixpoint(trips, {frag.flag_id})
    assert len(live) == 1 and live[0].end_ts == trips[1].end_ts


def test_zero_move_alone_still_removes_piece():
    # fragment NOT accepted -> the piece is genuinely removed (explicit choice)
    trips = conflict_day()
    zm = of_type(surfaced_flags(trips), "zero_move")
    live, _ = fixpoint(trips, {zm.flag_id})
    assert len(live) == 1 and live[0].raw_row == 1
    assert live[0].end_ts == trips[0].end_ts   # leg 2 gone, leg 1 left standalone


def test_contiguous_day_is_a_noop():
    # gap_m = 100, gap_min = (10 - 5) = 5 (using start_min=10, end_min=15)
    trips = [leg(1, 30, 0, 5, 0.0, 0.0), leg(2, 30, 10, 15, 100.0, 150.0)]  # gap < gate, gap_time >= 3 min
    live, surfaced = fixpoint(trips, accepted_ids=set())
    assert [t.raw_row for t in live] == [1, 2]
    assert surfaced == []
