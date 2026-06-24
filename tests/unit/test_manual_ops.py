"""Unit tests for `engine.proposals.apply_manual_ops` — the four manual edits.

Covers delete / merge (previously untested) and the new split / add, plus the
idempotency property the live `fixpoint` depends on (it re-applies the same op
list every cascade round, so a second pass must be a no-op).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.models import RoutePoint, Trip
from engine.proposals import add_row, apply_manual_ops, split_b_row

PST = timezone(timedelta(hours=-8))
BASE = datetime(2024, 11, 5, 9, 0, 0, tzinfo=PST)


def at(minute):
    return BASE + timedelta(minutes=minute)


def mk(row, smin, emin, *, person="A", mode="Auto", purpose="", route=None):
    return Trip(
        raw_row=row, person=person, mode=mode, purpose=purpose,
        start_ts=at(smin), end_ts=at(emin),
        start_lat=0.0, start_lon=0.0, end_lat=0.0, end_lon=0.0,
        route=route if route is not None else [],
    )


def routed(row, smin, emin):
    """A trip with one breadcrumb per minute from smin..emin (lon = minute)."""
    pts = [RoutePoint(0.0, float(m), at(m)) for m in range(smin, emin + 1)]
    t = mk(row, smin, emin, route=pts)
    t.end_lon = float(emin)
    return t


# --- mode -----------------------------------------------------------------

def test_mode_relabels_only_the_target():
    trips = [mk(1, 0, 10, mode="Auto"), mk(2, 12, 20, mode="Auto")]
    out = apply_manual_ops(trips, [{"type": "manual_mode", "raw_row": 2, "mode": "Bike"}])
    assert [(t.raw_row, t.mode) for t in out] == [(1, "Auto"), (2, "Bike")]


def test_mode_idempotent_and_last_wins():
    trips = [mk(1, 0, 10, mode="Auto")]
    ops = [{"type": "manual_mode", "raw_row": 1, "mode": "Bike"},
           {"type": "manual_mode", "raw_row": 1, "mode": "Walk"}]
    once = apply_manual_ops(trips, ops)
    twice = apply_manual_ops(once, ops)
    assert once[0].mode == "Walk" and twice[0].mode == "Walk"


# --- delete ---------------------------------------------------------------

def test_delete_drops_the_row():
    trips = [mk(1, 0, 10), mk(2, 12, 20)]
    out = apply_manual_ops(trips, [{"type": "manual_delete", "raw_row": 1}])
    assert [t.raw_row for t in out] == [2]


def test_delete_idempotent():
    trips = [mk(1, 0, 10), mk(2, 12, 20)]
    op = {"type": "manual_delete", "raw_row": 1}
    once = apply_manual_ops(trips, [op])
    twice = apply_manual_ops(once, [op])
    assert [t.raw_row for t in twice] == [2]


# --- merge ----------------------------------------------------------------

def test_merge_combines_into_one_with_lowest_row():
    trips = [mk(1, 0, 10), mk(2, 12, 20), mk(3, 30, 40)]
    out = apply_manual_ops(trips, [{"type": "manual_merge", "raw_rows": [1, 2]}])
    assert [t.raw_row for t in out] == [1, 3]
    merged = out[0]
    assert merged.start_ts == at(0) and merged.end_ts == at(20)  # A.start .. B.end


def test_merge_idempotent():
    trips = [mk(1, 0, 10), mk(2, 12, 20)]
    op = {"type": "manual_merge", "raw_rows": [1, 2]}
    once = apply_manual_ops(trips, [op])
    twice = apply_manual_ops(once, [op])      # only the merged row (1) remains -> no-op
    assert [t.raw_row for t in twice] == [1]
    assert twice[0].end_ts == at(20)


# --- split ----------------------------------------------------------------

def test_split_divides_route_by_timestamp():
    trips = [routed(5, 0, 10)]
    op = {"type": "manual_split", "raw_row": 5,
          "split_ts": at(4).strftime("%Y-%m-%dT%H:%M:%S"),
          "mode_a": "Walk", "mode_b": "Auto"}
    out = apply_manual_ops(trips, [op])

    a = [t for t in out if t.raw_row == 5][0]
    b = [t for t in out if t.raw_row == split_b_row(5)][0]
    assert a.start_ts == at(0) and a.end_ts == at(4)
    assert b.start_ts == at(4) and b.end_ts == at(10)
    assert a.mode == "Walk" and b.mode == "Auto"
    # route partitions <= split into A, > split into B (minutes 0..4 vs 5..10)
    assert [p.ts for p in a.route] == [at(m) for m in range(0, 5)]
    assert [p.ts for p in b.route] == [at(m) for m in range(5, 11)]
    # A's end / B's start snap to the breadcrumbs at the cut
    assert a.end_lon == 4.0 and b.start_lon == 5.0


def test_split_b_inherits_purpose_a_blank():
    trips = [routed(5, 0, 10)]
    trips[0].purpose = "Work"
    op = {"type": "manual_split", "raw_row": 5,
          "split_ts": at(5).strftime("%Y-%m-%dT%H:%M:%S")}
    out = apply_manual_ops(trips, [op])
    a = [t for t in out if t.raw_row == 5][0]
    b = [t for t in out if t.raw_row == split_b_row(5)][0]
    assert a.purpose == "" and b.purpose == "Work"


def test_split_out_of_range_is_noop():
    trips = [routed(5, 0, 10)]
    for when in (at(0), at(10), at(20)):   # boundary + beyond -> no split
        op = {"type": "manual_split", "raw_row": 5,
              "split_ts": when.strftime("%Y-%m-%dT%H:%M:%S")}
        out = apply_manual_ops(trips, [op])
        assert [t.raw_row for t in out] == [5]


def test_split_idempotent():
    trips = [routed(5, 0, 10)]
    op = {"type": "manual_split", "raw_row": 5,
          "split_ts": at(5).strftime("%Y-%m-%dT%H:%M:%S")}
    once = apply_manual_ops(trips, [op])
    twice = apply_manual_ops(once, [op])
    assert sorted(t.raw_row for t in twice) == sorted(t.raw_row for t in once)
    assert len(twice) == 2


# --- add ------------------------------------------------------------------

def test_add_materialises_trip_with_stable_row():
    trips = [mk(1, 0, 10)]
    op = {"type": "manual_add", "mode": "Bus",
          "start_ts": at(20).strftime("%Y-%m-%dT%H:%M:%S"),
          "end_ts": at(35).strftime("%Y-%m-%dT%H:%M:%S"),
          "start_lat": 1.0, "start_lon": 2.0, "end_lat": 3.0, "end_lon": 4.0}
    out = apply_manual_ops(trips, [op])
    added = [t for t in out if t.raw_row == add_row(1)][0]
    assert added.mode == "Bus" and added.route == []
    assert added.person == "A"                 # inferred from the day
    assert added.start_ts.tzinfo is not None    # tz attached from the day
    assert added.start_ts == at(20) and added.end_ts == at(35)


def test_add_indexes_by_position_and_is_idempotent():
    trips = [mk(1, 0, 10)]
    ops = [
        {"type": "manual_add", "start_ts": at(20).strftime("%Y-%m-%dT%H:%M:%S"),
         "end_ts": at(30).strftime("%Y-%m-%dT%H:%M:%S"),
         "start_lat": 0.0, "start_lon": 0.0, "end_lat": 0.0, "end_lon": 0.0},
        {"type": "manual_add", "start_ts": at(40).strftime("%Y-%m-%dT%H:%M:%S"),
         "end_ts": at(50).strftime("%Y-%m-%dT%H:%M:%S"),
         "start_lat": 0.0, "start_lon": 0.0, "end_lat": 0.0, "end_lon": 0.0},
    ]
    once = apply_manual_ops(trips, ops)
    assert {add_row(1), add_row(2)} <= {t.raw_row for t in once}
    twice = apply_manual_ops(once, ops)        # re-applying must not duplicate
    assert sorted(t.raw_row for t in twice) == sorted(t.raw_row for t in once)
