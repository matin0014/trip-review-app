"""Unit tests for `engine.proposals` 2e — apply-all projection + Do-file."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.models import Flag, PairEvidence, RoutePoint, Trip
from engine.proposals import apply_all, build_dofile

PST = timezone(timedelta(hours=-8))
BASE = datetime(2024, 11, 5, 9, 0, 0, tzinfo=PST)


def mk(row, smin, emin, *, slon=0.0, elon=0.0, person="A", purpose="", route=None):
    return Trip(
        raw_row=row, person=person, mode=f"m{row}", purpose=purpose,
        start_ts=BASE + timedelta(minutes=smin), end_ts=BASE + timedelta(minutes=emin),
        start_lat=0.0, start_lon=slon, end_lat=0.0, end_lon=elon,
        route=route if route is not None else [RoutePoint(0.0, slon, BASE + timedelta(minutes=smin))],
    )


def frag(a, b):
    return Flag(flag_type="fragment", left=(a,), right=(b,), group=min(a, b))


def test_fragment_merges_pair_into_one_trip() -> None:
    trips = [mk(1, 0, 10, elon=0.01), mk(2, 12, 20, slon=0.01, elon=0.02)]
    out = apply_all(trips, [frag(1, 2)])
    assert len(out) == 1
    m = out[0]
    assert m.raw_row == 1                      # group root = lowest row
    assert m.start_ts == trips[0].start_ts     # A's start
    assert m.end_ts == trips[1].end_ts         # B's end
    assert len(m.route) == 3                    # routes plus A endpoint stitch
    assert (m.route[1].lat, m.route[1].lon, m.route[1].ts) == (
        trips[0].end_lat, trips[0].end_lon, trips[0].end_ts)


def test_fragments_merge_transitively() -> None:
    trips = [mk(1, 0, 10), mk(2, 12, 20), mk(3, 22, 30)]
    out = apply_all(trips, [frag(1, 2), frag(2, 3)])
    assert len(out) == 1
    assert out[0].start_ts == trips[0].start_ts and out[0].end_ts == trips[2].end_ts


def test_zero_move_removed() -> None:
    trips = [mk(1, 0, 10), mk(2, 12, 20)]
    out = apply_all(trips, [Flag(flag_type="zero_move", trip=2)])
    assert [t.raw_row for t in out] == [1]


def test_zero_move_precedence_blocks_merge() -> None:
    # Trip 2 is both a fragment member and zero_move -> removed; 1 stays alone.
    trips = [mk(1, 0, 10), mk(2, 12, 20)]
    flags = [frag(1, 2), Flag(flag_type="zero_move", trip=2)]
    out = apply_all(trips, flags)
    assert len(out) == 1 and out[0].raw_row == 1
    assert out[0].end_ts == trips[0].end_ts     # NOT merged with 2


def test_missing_trip_inserts_stub() -> None:
    trips = [mk(1, 0, 10, elon=0.01), mk(2, 20, 30, slon=0.05)]
    flag = Flag(flag_type="missing_trip", left=(1,), right=(2,))
    out = apply_all(trips, [flag])
    assert len(out) == 3                         # stub added
    stub = [t for t in out if t.raw_row < 0][0]
    assert stub.start_ts == trips[0].end_ts and stub.end_ts == trips[1].start_ts
    assert stub.start_lon == trips[0].end_lon and stub.end_lon == trips[1].start_lon
    assert stub.route == []
    # the stub sorts into chronological place between the two trips
    assert [t.raw_row for t in out] == [1, -1, 2]


def test_flag_only_changes_no_geometry() -> None:
    trips = [mk(1, 0, 10), mk(2, 20, 30)]
    for ft in ("missing_activity", "mode_change"):
        out = apply_all(trips, [Flag(flag_type=ft, left=(1,), right=(2,))])
        assert [t.raw_row for t in out] == [1, 2]


def test_build_dofile_structure() -> None:
    trips = [mk(1, 0, 10, purpose="Work"), mk(2, 12, 20)]
    ev = PairEvidence(gap_distance_m=300, gap_time_min=2, v_a_kmh=30, v_b_kmh=30,
                      t_min=0.6, buffer_min=3.06, residual_min=1.4, slack_b_min=1.4)
    flags = [Flag(flag_type="fragment", left=(1,), right=(2,), group=1, evidence=ev)]
    dofile = build_dofile(trips, flags)

    assert set(dofile) == {"flags", "rows"}
    (fid, fobj), = dofile["flags"].items()
    assert fobj["flag_type"] == "fragment" and fobj["evidence"]["gap_distance_m"] == 300
    # both member rows link back to the flag
    assert dofile["rows"][1]["flag_ids"] == [fid]
    assert dofile["rows"][2]["flag_ids"] == [fid]
    assert dofile["rows"][1]["is_manual"] is True and dofile["rows"][2]["is_manual"] is False
    assert dofile["rows"][1]["duration_min"] == 10.0
