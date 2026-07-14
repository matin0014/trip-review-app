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


# --- purpose --------------------------------------------------------------

def test_purpose_relabels_only_the_target():
    trips = [mk(1, 0, 10, purpose=""), mk(2, 12, 20, purpose="Shopping")]
    out = apply_manual_ops(trips, [{"type": "manual_purpose", "raw_row": 1, "purpose": "Work"}])
    assert [(t.raw_row, t.purpose) for t in out] == [(1, "Work"), (2, "Shopping")]


def test_purpose_idempotent_and_last_wins():
    trips = [mk(1, 0, 10, purpose="")]
    ops = [{"type": "manual_purpose", "raw_row": 1, "purpose": "Work"},
           {"type": "manual_purpose", "raw_row": 1, "purpose": "School"}]
    once = apply_manual_ops(trips, ops)
    twice = apply_manual_ops(once, ops)
    assert once[0].purpose == "School" and twice[0].purpose == "School"


# --- time -----------------------------------------------------------------

def test_time_restamps_start_and_end():
    trips = [mk(1, 0, 10), mk(2, 12, 20)]
    op = {"type": "manual_time", "raw_row": 1,
          "start_ts": at(2).strftime("%Y-%m-%dT%H:%M:%S"),
          "end_ts": at(8).strftime("%Y-%m-%dT%H:%M:%S")}
    out = apply_manual_ops(trips, [op])
    edited = [t for t in out if t.raw_row == 1][0]
    assert edited.start_ts == at(2) and edited.end_ts == at(8)
    assert edited.start_ts.tzinfo is not None      # tz preserved from the trip


def test_time_resorts_when_order_changes():
    # push trip 1 to AFTER trip 2 -> the list must re-sort by start_ts
    trips = [mk(1, 0, 10), mk(2, 12, 20)]
    op = {"type": "manual_time", "raw_row": 1,
          "start_ts": at(30).strftime("%Y-%m-%dT%H:%M:%S"),
          "end_ts": at(40).strftime("%Y-%m-%dT%H:%M:%S")}
    out = apply_manual_ops(trips, [op])
    assert [t.raw_row for t in out] == [2, 1]


def test_time_idempotent_and_last_wins():
    trips = [mk(1, 0, 10)]
    ops = [{"type": "manual_time", "raw_row": 1,
            "start_ts": at(1).strftime("%Y-%m-%dT%H:%M:%S"),
            "end_ts": at(9).strftime("%Y-%m-%dT%H:%M:%S")},
           {"type": "manual_time", "raw_row": 1,
            "start_ts": at(3).strftime("%Y-%m-%dT%H:%M:%S"),
            "end_ts": at(7).strftime("%Y-%m-%dT%H:%M:%S")}]
    once = apply_manual_ops(trips, ops)
    twice = apply_manual_ops(once, ops)
    assert once[0].start_ts == at(3) and once[0].end_ts == at(7)
    assert twice[0].start_ts == at(3) and twice[0].end_ts == at(7)


def test_time_respreads_a_synthetic_add_route():
    # a manual add carries a SYNTHETIC route; editing its window must re-spread the
    # SAME coordinates across the new span (so the measured speed tracks the new
    # duration), keeping every point — not clip them away.
    t = routed(add_row(1), 0, 10)                       # 11 breadcrumbs at at(0)..at(10)
    op = {"type": "manual_time", "raw_row": add_row(1),
          "start_ts": at(2).strftime("%Y-%m-%dT%H:%M:%S"),
          "end_ts": at(8).strftime("%Y-%m-%dT%H:%M:%S")}
    out = apply_manual_ops([t], [op])[0]
    assert len(out.route) == 11                         # full path preserved
    assert out.route[0].ts == at(2) and out.route[-1].ts == at(8)   # re-spread to new window
    assert [p.lon for p in out.route] == [float(m) for m in range(11)]  # coords unchanged
    # idempotent (fixpoint re-applies each round)
    assert apply_manual_ops([out], [op])[0].route[0].ts == at(2)


def test_time_clips_real_gps_breadcrumbs():
    # a real logged trip carries REAL GPS; editing its window must CLIP to the
    # breadcrumbs whose own timestamps fall inside it, never rescaling real data.
    t = routed(1, 0, 10)                                # real trip, ts at(0)..at(10)
    op = {"type": "manual_time", "raw_row": 1,
          "start_ts": at(2).strftime("%Y-%m-%dT%H:%M:%S"),
          "end_ts": at(8).strftime("%Y-%m-%dT%H:%M:%S")}
    out = apply_manual_ops([t], [op])[0]
    assert [p.ts for p in out.route] == [at(m) for m in range(2, 9)]  # minutes 2..8 kept, real ts


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


def test_merge_logged_with_added_anchors_to_the_workbook_row():
    # add a trip (negative raw_row), then merge it with logged row 1. The merged
    # trip must keep the WORKBOOK row id (1), not the added trip's negative id, so
    # the export shows one M-row instead of a stray A-row (the reported bug).
    logged = mk(1, 0, 10)
    add_op = {"type": "manual_add",
              "start_ts": at(30).strftime("%Y-%m-%dT%H:%M:%S"),
              "end_ts": at(40).strftime("%Y-%m-%dT%H:%M:%S"),
              "mode": "Bike", "purpose": "",
              "start_lat": 0.0, "start_lon": 0.0, "end_lat": 0.1, "end_lon": 0.1,
              "route": [[0.0, 0.0], [0.1, 0.1]]}
    merge_op = {"type": "manual_merge", "raw_rows": [1, add_row(1)]}
    out = apply_manual_ops([logged], [add_op, merge_op])
    assert len(out) == 1
    m = out[0]
    assert m.raw_row == 1                       # anchored to the workbook row, NOT add_row(1)
    assert m.start_ts == at(0) and m.end_ts == at(40)   # spans the logged + added legs


def test_merge_two_added_trips_reapplies_without_corruption():
    # Merging two ADDED trips: the merged trip keeps add_row(2)'s id, so add_row(1)
    # would re-materialise on the next fixpoint round and re-merge, flipping the end
    # back to the first leg + duplicating its route. `absorbed` must stop that.
    seed = mk(1, 0, 5)                           # a workbook trip for tz/person inference
    a1 = {"type": "manual_add",
          "start_ts": at(10).strftime("%Y-%m-%dT%H:%M:%S"),
          "end_ts": at(20).strftime("%Y-%m-%dT%H:%M:%S"),
          "mode": "Bike", "purpose": "",
          "start_lat": 0.0, "start_lon": 0.0, "end_lat": 0.1, "end_lon": 0.1,
          "route": [[0.0, 0.0], [0.1, 0.1]]}
    a2 = {"type": "manual_add",
          "start_ts": at(30).strftime("%Y-%m-%dT%H:%M:%S"),
          "end_ts": at(40).strftime("%Y-%m-%dT%H:%M:%S"),
          "mode": "Bike", "purpose": "",
          "start_lat": 0.1, "start_lon": 0.1, "end_lat": 0.2, "end_lon": 0.2,
          "route": [[0.1, 0.1], [0.2, 0.2]]}
    ops = [a1, a2, {"type": "manual_merge", "raw_rows": [add_row(1), add_row(2)]}]
    once = apply_manual_ops([seed], ops)
    twice = apply_manual_ops(once, ops)          # fixpoint re-applies ops every round
    m1 = [t for t in once if t.raw_row < 0][0]
    m2 = [t for t in twice if t.raw_row < 0][0]
    assert m1.end_ts == at(40) and m2.end_ts == at(40)         # spans to the SECOND leg
    assert (m1.end_lat, m1.end_lon) == (0.2, 0.2)              # second leg's endpoint
    assert (m2.end_lat, m2.end_lon) == (0.2, 0.2)             # stable across re-apply
    assert len(m1.route) == 4 and len(m2.route) == 4           # no breadcrumb duplication


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


def test_add_with_route_builds_evenly_timed_breadcrumbs():
    trips = [mk(1, 0, 10)]
    op = {"type": "manual_add", "mode": "Walk",
          "start_ts": at(20).strftime("%Y-%m-%dT%H:%M:%S"),
          "end_ts": at(30).strftime("%Y-%m-%dT%H:%M:%S"),
          "start_lat": 1.0, "start_lon": 2.0, "end_lat": 3.0, "end_lon": 4.0,
          "route": [[1.0, 2.0], [1.5, 2.5], [3.0, 4.0]]}    # OSM shortest path
    out = apply_manual_ops(trips, [op])
    added = [t for t in out if t.raw_row == add_row(1)][0]
    assert [(p.lat, p.lon) for p in added.route] == [(1.0, 2.0), (1.5, 2.5), (3.0, 4.0)]
    # timestamps spread evenly across start..end
    assert added.route[0].ts == at(20) and added.route[-1].ts == at(30)
    assert added.route[1].ts == at(25)


def test_add_without_route_has_empty_breadcrumbs():
    trips = [mk(1, 0, 10)]
    op = {"type": "manual_add", "mode": "Bus",
          "start_ts": at(20).strftime("%Y-%m-%dT%H:%M:%S"),
          "end_ts": at(35).strftime("%Y-%m-%dT%H:%M:%S"),
          "start_lat": 1.0, "start_lon": 2.0, "end_lat": 3.0, "end_lon": 4.0}
    added = [t for t in apply_manual_ops(trips, [op]) if t.raw_row == add_row(1)][0]
    assert added.route == []


def test_add_indexes_by_position_and_is_idempotent():
    # LEGACY fallback: ops with no `add_seq` (pre-fix logs) still number by
    # position, same as before. New adds always carry add_seq (see below).
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


def test_add_seq_is_honored_over_position():
    # a stable add_seq assigns raw_row directly, regardless of how many manual_add
    # ops precede it in the list.
    trips = [mk(1, 0, 10)]
    op = {"type": "manual_add", "add_seq": 7, "mode": "Bus",
          "start_ts": at(20).strftime("%Y-%m-%dT%H:%M:%S"),
          "end_ts": at(35).strftime("%Y-%m-%dT%H:%M:%S"),
          "start_lat": 0.0, "start_lon": 0.0, "end_lat": 0.0, "end_lon": 0.0}
    out = apply_manual_ops(trips, [op])
    assert any(t.raw_row == add_row(7) for t in out)


def test_reverted_add_edits_do_not_leak_onto_a_later_add(person="A"):
    # Reproduces the reported bug: an earlier add (seq=2) was edited (time/mode/
    # purpose) then reverted -- but the edit ops themselves stayed active. A
    # brand-new add (seq=3, its OWN stable id) must NOT inherit those stale
    # edits across fixpoint rounds, even though the edits still target raw_row
    # add_row(2) and a re-apply loop (like the live fixpoint) re-runs every round.
    day_key = "p|2024-11-05"
    ops = [
        {"type": "manual_add", "add_seq": 1, "day_key": day_key, "mode": "Auto Driver",
         "purpose": "Shopping",
         "start_ts": at(0).strftime("%Y-%m-%dT%H:%M:%S"),
         "end_ts": at(15).strftime("%Y-%m-%dT%H:%M:%S"),
         "start_lat": 0.0, "start_lon": 0.0, "end_lat": 0.0, "end_lon": 0.0},
        # stale edits from the now-reverted seq=2 add, still active, aimed at
        # its raw_row (which no longer has a matching add op)
        {"type": "manual_time", "raw_row": add_row(2), "day_key": day_key,
         "start_ts": at(15).strftime("%Y-%m-%dT%H:%M:%S"),
         "end_ts": at(38).strftime("%Y-%m-%dT%H:%M:%S")},
        {"type": "manual_mode", "raw_row": add_row(2), "day_key": day_key, "mode": "Auto Passenger"},
        {"type": "manual_purpose", "raw_row": add_row(2), "day_key": day_key, "purpose": "Shopping"},
        # the NEW add: a distinct, stable seq=3 (NOT a reused slot)
        {"type": "manual_add", "add_seq": 3, "day_key": day_key, "mode": "Auto Driver",
         "purpose": "Social",
         "start_ts": at(60).strftime("%Y-%m-%dT%H:%M:%S"),
         "end_ts": at(75).strftime("%Y-%m-%dT%H:%M:%S"),
         "start_lat": 1.0, "start_lon": 1.0, "end_lat": 2.0, "end_lon": 2.0},
    ]
    trips = [mk(1, -30, -20, person=person)]
    for _ in range(3):   # simulate several fixpoint rounds re-applying the same ops
        trips = apply_manual_ops(trips, ops)
    new_add = [t for t in trips if t.raw_row == add_row(3)][0]
    assert new_add.start_ts == at(60) and new_add.end_ts == at(75)
    assert new_add.mode == "Auto Driver" and new_add.purpose == "Social"


# --- reposition -------------------------------------------------------------

def _triplets(pts):
    """[[lat, lon, iso_ts], ...] -- the JSON-safe shape manual_reposition stores
    (mirrors what the real server handler would precompute)."""
    return [[p.lat, p.lon, p.ts.isoformat()] for p in pts]


def test_reposition_end_replaces_route_and_endpoint():
    # kept breadcrumbs (0..5 min) + a patch reaching the new farther-away point
    kept = [RoutePoint(0.0, float(m), at(m)) for m in range(0, 6)]
    patch = [RoutePoint(0.0, 6.0, at(6)), RoutePoint(0.0, 9.0, at(10))]
    t = mk(1, 0, 10, route=kept)
    op = {"type": "manual_reposition", "raw_row": 1, "end": "end",
          "lat": 0.0, "lon": 9.0, "route": _triplets(kept + patch)}
    out = apply_manual_ops([t], [op])[0]
    assert (out.end_lat, out.end_lon) == (0.0, 9.0)
    assert len(out.route) == 8  # 6 kept + 2 patch
    assert out.route[0].lon == 0.0 and out.route[-1].lon == 9.0
    assert out.start_lat == 0.0 and out.start_lon == 0.0  # untouched


def test_reposition_start_replaces_route_and_endpoint():
    patch = [RoutePoint(0.0, -3.0, at(0)), RoutePoint(0.0, -1.0, at(2))]
    kept = [RoutePoint(0.0, float(m), at(m)) for m in range(4, 11)]
    t = mk(1, 0, 10, route=kept)
    op = {"type": "manual_reposition", "raw_row": 1, "end": "start",
          "lat": 0.0, "lon": -3.0, "route": _triplets(patch + kept)}
    out = apply_manual_ops([t], [op])[0]
    assert (out.start_lat, out.start_lon) == (0.0, -3.0)
    assert out.end_lat == 0.0 and out.end_lon == 0.0  # untouched (mk()'s default)


def test_reposition_only_touches_the_targeted_row():
    kept = [RoutePoint(0.0, float(m), at(m)) for m in range(0, 6)]
    t1, t2 = mk(1, 0, 10, route=kept), mk(2, 12, 20)
    op = {"type": "manual_reposition", "raw_row": 1, "end": "end",
          "lat": 1.0, "lon": 1.0, "route": _triplets(kept)}
    out = apply_manual_ops([t1, t2], [op])
    assert [t.raw_row for t in out] == [1, 2]
    assert out[1].end_lat == 0.0 and out[1].end_lon == 0.0  # trip 2 untouched


def test_reposition_is_idempotent():
    kept = [RoutePoint(0.0, float(m), at(m)) for m in range(0, 6)]
    patch = [RoutePoint(0.0, 6.0, at(6)), RoutePoint(0.0, 9.0, at(10))]
    full_route = _triplets(kept + patch)
    op = {"type": "manual_reposition", "raw_row": 1, "end": "end",
          "lat": 0.0, "lon": 9.0, "route": full_route}
    trips = [mk(1, 0, 10, route=kept)]
    for _ in range(3):   # re-applied every fixpoint round -- must not re-append the patch
        trips = apply_manual_ops(trips, [op])
    assert len(trips[0].route) == 8
    assert (trips[0].end_lat, trips[0].end_lon) == (0.0, 9.0)
