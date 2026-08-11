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
    *_, mode_overrides, _times, _purp, _merge, _pos = clean_day(trips, set(), day_ops=[op])
    assert mode_overrides == {2: "Bike"}


def test_manual_mode_on_merged_trip_relabels_all_members():
    # accept the fragment so rows 1 and 2 share an M-id, then relabel the group
    trips = conflict_day()
    ops = [{"type": "manual_mode", "raw_row": 1, "mode": "Bike", "day_key": "p|2024-11-05"}]
    *_, mode_overrides, _times, _purp, _merge, _pos = clean_day(trips, {fid(trips, "fragment")}, day_ops=ops)
    assert mode_overrides == {1: "Bike", 2: "Bike"}   # both members, option (b)


def test_manual_purpose_override_for_a_kept_trip():
    trips = [leg(1, 30, 0, 5, 0.0, 0.0), leg(2, 30, 7, 12, 100.0, 150.0)]
    op = {"type": "manual_purpose", "raw_row": 2, "purpose": "Work", "day_key": "p|2024-11-05"}
    *_, purpose_overrides, _merge, _pos = clean_day(trips, set(), day_ops=[op])
    assert purpose_overrides == {2: "Work"}


def test_manual_purpose_on_merged_trip_relabels_all_members():
    trips = conflict_day()
    ops = [{"type": "manual_purpose", "raw_row": 1, "purpose": "Work", "day_key": "p|2024-11-05"}]
    *_, purpose_overrides, _merge, _pos = clean_day(trips, {fid(trips, "fragment")}, day_ops=ops)
    assert purpose_overrides == {1: "Work", 2: "Work"}   # both members, option (b)


def test_no_decisions_all_keeps():
    trips = [leg(1, 30, 0, 5, 0.0, 0.0), leg(2, 30, 7, 12, 100.0, 150.0)]  # contiguous, no flags
    rows, stubs, *_ = clean_day(trips, set())
    assert rows == [(1, "1"), (2, "2")] and stubs == []


def test_accept_fragment_shares_merge_id():
    trips = conflict_day()
    rows, stubs, *_ = clean_day(trips, {fid(trips, "fragment")})
    assert rows == [(1, "M1"), (2, "M1")] and stubs == []


def test_manual_merge_does_not_split_an_accepted_fragment_group_sharing_a_row():
    # Regression: an accepted fragment merges (1,2), and a manual_merge references
    # row 2 (with row 3). The fragment already consumed row 2, so the LIVE view is
    # {1,2} merged + row 3 standalone (the manual_merge no-ops on the gone row).
    # The export used to re-derive grouping independently: it pulled row 2 into a
    # SECOND M-group with row 3, splitting 1 and 2 apart (1->M1, 2->M2). It must now
    # read the grouping straight from the live trips, keeping 1 and 2 as one M-id.
    trips = conflict_day() + [leg(3, 5, 19, 24, 1050.0, 1100.0)]
    ops = [{"type": "manual_merge", "raw_rows": [2, 3], "day_key": "p|2024-11-05"}]
    rows, stubs, *_ = clean_day(trips, {fid(conflict_day(), "fragment")}, day_ops=ops)
    ids = dict(rows)
    assert ids[1] == ids[2] == "M1"     # 1 and 2 stay one group, never split into M1 + M2
    assert ids[3] == "3"                # row 3 standalone (its merge target was consumed)
    assert stubs == []


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
    rows, extras, _surf, overrides, _modes, _times, _purp, _merge, _pos = clean_day(trips, set(), day_ops=[op])

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


def test_manual_time_override_for_a_kept_trip():
    trips = [leg(1, 30, 0, 5, 0.0, 0.0), leg(2, 30, 7, 12, 100.0, 150.0)]
    op = {"type": "manual_time", "raw_row": 2, "day_key": "p|2024-11-05",
          "start_ts": "2024-11-05T08:30:00", "end_ts": "2024-11-05T08:45:00"}
    *_, time_overrides, _purp, _merge, _pos = clean_day(trips, set(), day_ops=[op])
    assert time_overrides == {2: ("2024-11-05T08:30:00", "2024-11-05T08:45:00")}


def test_split_then_merging_the_a_piece_collapses_into_the_merge():
    # Regression: split a trip, then merge its A piece with the PREVIOUS trip. The
    # row is both a split origin and a merge member; split used to win, so the row
    # kept an S<row>-A id, escaped the merge collapse (which only drops M-prefixed
    # rows) AND stayed untruncated (its live trip no longer exists standalone) --
    # the cleaned file carried the A piece twice, once inside M and once full-length.
    trips = [leg(1, 30, 0, 5, 0.0, 0.0),
             leg(2, 30, 20, 25, 2000.0, 2100.0),
             leg(3, 30, 40, 65, 4000.0, 6000.0)]
    ops = [{"type": "manual_split", "raw_row": 3, "day_key": "p|2024-11-05",
            "split_ts": "2024-11-05T12:46:00", "mode_a": "Walk", "mode_b": "Walk"},
           {"type": "manual_merge", "raw_rows": [2, 3], "day_key": "p|2024-11-05"}]
    rows, extras, *_ = clean_day(trips, set(), day_ops=ops)
    ids = dict(rows)
    assert ids[2] == "M2" and ids[3] == "M2"   # row 3 joins the merge, not its own S row
    assert "S3-A" not in ids.values()          # never a stray full-length A row
    assert [k for k, _ref, _t in extras] == ["split"]   # the B piece still stands alone


def test_extra_ids_labels_split_b():
    b = leg(99, 30, 0, 5, 0.0, 0.0)
    (trip_id, kind, _trip), = _extra_ids([("split", 7, b)])
    assert trip_id == "S7-B" and kind == "split"


# --- the cleaned file may only contain rows the fixpoint kept ---------------
#
# Regression: `clean_day` re-derives its `removed` set from two known reasons
# (an accepted zero_move, a manual delete), but the fixpoint drops rows for
# other reasons too -- notably an accepted merge whose partner was then removed,
# which takes the WHOLE group out of `live`. The survivors used to fall through
# to the plain-keep branch and reach the cleaned file with their original times
# and coordinates, looking like ordinary untouched trips (185 such rows across
# the four wave-2 splits). `live` is the authority on what survived.

def _live_rows(trips, accepted, ops=None):
    from app.decisions import fixpoint
    live, _ = fixpoint(trips, accepted, ops=ops or [])
    out = set()
    for t in live:
        out |= {t.raw_row} | set(t.absorbed)
    return out


def _kept_workbook_rows(rows):
    """Rows clean_day puts in the cleaned file (M-ids included, they are real
    workbook rows folded into a merge)."""
    return {r for r, _tid in rows}


def test_zero_moved_merge_member_does_not_drag_its_partner_into_the_file():
    # conflict_day: fragment(1,2) + zero_move(2). Accepting BOTH makes the
    # fixpoint merge them, re-classify the merged trip, and (still barely moving)
    # remove it under the same content-hashed zero_move id. Neither row survives,
    # so neither may be exported.
    trips = conflict_day()
    accepted = {fid(trips, "fragment"), fid(trips, "zero_move")}
    live = _live_rows(trips, accepted)
    rows, _extras, *_ = clean_day(trips, accepted)
    assert _kept_workbook_rows(rows) <= live


def test_every_exported_row_is_one_the_fixpoint_kept():
    # The general invariant, over each subset of decisions that can be made on a
    # day where a fragment and a zero_move overlap.
    import itertools
    trips = conflict_day()
    ids = {"fragment": fid(trips, "fragment"), "zero_move": fid(trips, "zero_move")}
    for n in range(len(ids) + 1):
        for combo in itertools.combinations(sorted(ids.values()), n):
            accepted = set(combo)
            live = _live_rows(trips, accepted)
            rows, _extras, *_ = clean_day(trips, accepted)
            leaked = _kept_workbook_rows(rows) - live
            assert not leaked, f"accepted={accepted} leaked rows {leaked}"


def test_deleting_a_merged_trip_removes_every_member_from_the_file():
    # Deleting what the screen shows as ONE merged trip must not leave its other
    # half behind: the op names only the group's root row.
    trips = conflict_day()
    accepted = {fid(trips, "fragment")}
    ops = [{"type": "manual_delete", "raw_row": 1}]
    live = _live_rows(trips, accepted, ops)
    rows, _extras, *_ = clean_day(trips, accepted, day_ops=ops)
    assert _kept_workbook_rows(rows) <= live
    assert _kept_workbook_rows(rows) == set()      # the whole merged trip is gone


# --- splitting a MERGED trip must not lose its members ----------------------

def test_splitting_a_merged_trip_keeps_every_member_row():
    # The fixpoint applies accepted merges BEFORE manual ops, so accepting a
    # fragment on (1, 2) and then splitting hands `_apply_split` a trip that
    # already carries both rows. Building the halves without `absorbed` dropped
    # that membership: row 2 then belonged to no live trip, so the export either
    # emitted it with stale pre-merge values or (once the export started trusting
    # `live`) dropped it entirely. 58 rows across the four wave-2 splits.
    trips = conflict_day()
    accepted = {fid(trips, "fragment")}
    split_at = (BASE + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")
    ops = [{"type": "manual_split", "raw_row": 1, "split_ts": split_at}]

    live = _live_rows(trips, accepted, ops)
    assert {1, 2} <= live, f"workbook rows missing from the live view: {{1, 2}} - {live}"

    rows, extras, *_ = clean_day(trips, accepted, day_ops=ops)
    exported = _kept_workbook_rows(rows)
    assert exported == {1, 2}          # both rows reach the cleaned file
    assert any(kind == "split" for kind, _ref, _t in extras)   # ...and the B piece


def test_the_split_halves_of_a_merged_trip_still_cover_its_whole_span():
    trips = conflict_day()
    accepted = {fid(trips, "fragment")}
    split_at = (BASE + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")
    from app.decisions import fixpoint
    merged, _ = fixpoint(trips, accepted)
    live, _ = fixpoint(trips, accepted,
                       ops=[{"type": "manual_split", "raw_row": 1, "split_ts": split_at}])
    assert min(t.start_ts for t in live) == min(t.start_ts for t in merged)
    assert max(t.end_ts for t in live) == max(t.end_ts for t in merged)
