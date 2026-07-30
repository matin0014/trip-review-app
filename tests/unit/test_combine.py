"""Unit tests for `app.combine`: stitching reviewers' exports back into one dataset.

These pin the two ways a split-then-recombine workflow silently corrupts data --
matching rows on `trip_id` (a per-split row position, so it collides) and
double-counting a participant who ended up in two splits -- plus the arithmetic
that has to be redone rather than concatenated (summary counts, precision).
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.combine import (CombineError, combine_cleaned, combine_dofile,
                         find_overlaps, resolve_overlaps)


def cleaned(rows):
    """rows = [(wave_row, accessCode, trip_id)]"""
    return pd.DataFrame(rows, columns=["wave_row", "accessCode", "trip_id"])


# --- the trip_id trap -----------------------------------------------------

def test_colliding_trip_ids_across_splits_are_not_treated_as_the_same_trip():
    # "130" is row 130 of EACH reviewer's own file, so it means two different
    # trips. Both must survive; matching on trip_id would have merged them.
    a = cleaned([(511, "p1", "130")])
    b = cleaned([(2992, "p2", "130")])
    out = combine_cleaned({"A": a, "B": b})
    assert len(out) == 2
    assert sorted(out["wave_row"]) == [511, 2992]
    assert sorted(out["trip_id"]) == ["130", "130"]


def test_the_same_wave_row_in_two_splits_is_refused():
    # A wave_row identifies one row of the ORIGINAL wave file, so it can only
    # belong to one participant. Seeing it twice under different people means the
    # inputs are not slices of the same wave (stale or mismatched split files),
    # which would put one raw trip into the dataset twice. The usual duplicate --
    # the same participant in two splits -- is caught earlier by the overlap
    # check; this is the backstop for everything else.
    a = cleaned([(511, "p1", "1")])
    b = cleaned([(511, "p2", "77")])
    with pytest.raises(CombineError, match="cleaned twice"):
        combine_cleaned({"A": a, "B": b})


def test_missing_wave_row_column_is_refused():
    df = pd.DataFrame([{"accessCode": "p1", "trip_id": "1"}])
    with pytest.raises(CombineError, match="wave_row"):
        combine_cleaned({"A": df})


# --- overlapping participants ---------------------------------------------

def test_overlapping_participant_is_detected():
    a = cleaned([(1, "shared", "1"), (2, "only_a", "2")])
    b = cleaned([(3, "shared", "1")])
    assert find_overlaps({"A": a, "B": b}) == {"shared": ["A", "B"]}


def test_unresolved_overlap_refuses_to_combine():
    a = cleaned([(1, "shared", "1")])
    b = cleaned([(2, "shared", "1")])
    with pytest.raises(CombineError, match="double-count"):
        combine_cleaned({"A": a, "B": b})


def test_owner_keeps_one_splits_version_and_drops_the_other():
    # B is the reviewer who actually cleaned "shared", so A's untouched copy goes.
    a = cleaned([(1, "shared", "1"), (2, "only_a", "2")])
    b = cleaned([(3, "shared", "9")])
    out = combine_cleaned({"A": a, "B": b}, owners={"shared": "B"})
    assert sorted(out["wave_row"]) == [2, 3]
    assert set(out[out["accessCode"] == "shared"]["source_split"]) == {"B"}
    assert set(out[out["accessCode"] == "only_a"]["source_split"]) == {"A"}


def test_owner_naming_a_split_that_is_not_being_combined_is_refused():
    a = cleaned([(1, "shared", "1")])
    b = cleaned([(2, "shared", "1")])
    with pytest.raises(CombineError, match="not among the splits"):
        combine_cleaned({"A": a, "B": b}, owners={"shared": "C"})


def test_ownership_applies_even_when_the_owner_exported_no_rows_for_them():
    # Regression (real data): Shafie owned yoxz-yea-wy but had removed ALL of
    # that participant's days, so his export had zero rows for them. They then
    # appeared in only ONE export -- Yerdana's untouched copy -- so overlap
    # detection could not see them, and 60 unreviewed rows reached the dataset.
    # Ownership must win regardless of whether the owner exported anything.
    owner_empty = cleaned([(9, "someone_else", "1")])
    other = cleaned([(1, "owned", "1"), (2, "owned", "2"), (3, "mine", "3")])
    out = combine_cleaned({"Owner": owner_empty, "Other": other},
                          owners={"owned": "Owner"})
    assert "owned" not in set(out["accessCode"])       # not silently kept
    assert sorted(out["wave_row"]) == [3, 9]


def test_a_participant_owned_by_a_split_that_removed_them_is_reported():
    from app.combine import owned_but_absent
    owner_empty = cleaned([(9, "someone_else", "1")])
    other = cleaned([(1, "owned", "1")])
    assert owned_but_absent({"Owner": owner_empty, "Other": other},
                            {"owned": "Owner"}) == ["owned"]


def test_disjoint_splits_need_no_owners():
    a = cleaned([(1, "p1", "1")])
    b = cleaned([(2, "p2", "1")])
    assert find_overlaps({"A": a, "B": b}) == {}
    assert len(combine_cleaned({"A": a, "B": b})) == 2


def test_every_row_records_which_split_it_came_from():
    out = combine_cleaned({"A": cleaned([(1, "p1", "1")]),
                           "B": cleaned([(2, "p2", "1")])})
    assert dict(zip(out["wave_row"], out["source_split"])) == {1: "A", 2: "B"}


# --- do-file sheets --------------------------------------------------------

def _sheets(ledger, summary=None, precision=None, removed_rows=None):
    out = {"dofile": ledger}
    if summary is not None:
        out["removed summary"] = summary
    if precision is not None:
        out["precision"] = precision
    if removed_rows is not None:
        out["removed rows"] = removed_rows
    return out


def summary(pairs):
    return pd.DataFrame([{"Measure": m, "Count": c, "What it means": "x"}
                         for m, c in pairs])


def test_removed_summary_counts_add_up():
    a = _sheets(cleaned([(1, "p1", "1")]),
                summary=[("People removed entirely", 1), ("Days removed (total)", 4)])
    b = _sheets(cleaned([(2, "p2", "1")]),
                summary=[("People removed entirely", 2), ("Days removed (total)", 7)])
    a["removed summary"] = summary(a["removed summary"])
    b["removed summary"] = summary(b["removed summary"])
    out = combine_dofile({"A": a, "B": b})
    got = dict(zip(out["removed summary"]["Measure"], out["removed summary"]["Count"]))
    assert got == {"People removed entirely": 3, "Days removed (total)": 11}


def test_precision_is_pooled_from_counts_not_averaged():
    # A: 1/1 = 100%. B: 1/9 ~ 11%. The average of the RATES is ~56%, which no
    # participant experienced; the true pooled precision is 2/10 = 20%.
    prec = lambda tp, fp: pd.DataFrame(
        [{"detector": "fragment", "origin": "ingest", "tp": tp, "fp": fp,
          "precision": tp / (tp + fp)}])
    a = _sheets(cleaned([(1, "p1", "1")]), precision=prec(1, 0))
    b = _sheets(cleaned([(2, "p2", "1")]), precision=prec(1, 8))
    out = combine_dofile({"A": a, "B": b})
    row = out["precision"].iloc[0]
    assert (row["tp"], row["fp"]) == (2, 8)
    assert row["precision"] == pytest.approx(0.2)


def test_precision_is_none_when_nothing_was_ever_decided():
    prec = pd.DataFrame([{"detector": "fragment", "origin": "ingest", "tp": 0,
                          "fp": 0, "precision": None}])
    out = combine_dofile({"A": _sheets(cleaned([(1, "p1", "1")]), precision=prec)})
    assert out["precision"].iloc[0]["precision"] is None


def test_removed_rows_of_a_dropped_participant_do_not_survive():
    # If A's copy of "shared" is dropped in favour of B's, A's record of removing
    # that participant's days must go too, or the day would be counted as
    # removed while B also reports it as cleaned.
    rr = lambda person: pd.DataFrame([{"person": person, "day_key": f"{person}|d",
                                       "reason": "removed by reviewer"}])
    a = _sheets(cleaned([(1, "shared", "1")]), removed_rows=rr("shared"))
    b = _sheets(cleaned([(2, "shared", "1")]), removed_rows=rr("other"))
    out = combine_dofile({"A": a, "B": b}, owners={"shared": "B"})
    assert set(out["removed rows"]["person"]) == {"other"}


def test_dofile_rows_record_their_source_split():
    out = combine_dofile({"A": _sheets(cleaned([(1, "p1", "1")])),
                          "B": _sheets(cleaned([(2, "p2", "1")]))})
    assert set(out["dofile"]["source_split"]) == {"A", "B"}


def test_empty_splits_are_skipped_not_crashed_on():
    out = combine_cleaned({"A": cleaned([(1, "p1", "1")]),
                           "B": cleaned([])})
    assert len(out) == 1


# --- end to end over real session folders ----------------------------------

def _mini_session(root, sid, rows, date="2024-11-05"):
    """A minimal on-disk session: raw workbook + proposals, no decisions."""
    import os
    from app import baseline, session
    from engine.classify import classify_all
    from engine.loader import load_trips

    prev = session.SESSIONS_DIR
    session.SESSIONS_DIR = str(root)
    try:
        session.write_proposals(sid, {"raw_name": "raw.xlsx"})
        raw = os.path.join(session.session_dir(sid), "raw.xlsx")
        pd.DataFrame(rows).to_excel(raw, index=False)
        trips = load_trips(raw)
        session.write_proposals(sid, baseline.build_baseline(trips, classify_all(trips),
                                                             "raw.xlsx"))
        return os.path.join(str(root), sid)
    finally:
        session.SESSIONS_DIR = prev


def _raw_rows(person, wave_rows, date="2024-11-05"):
    def ts(h):
        return f"{h}-08:00"
    out = []
    for i, wr in enumerate(wave_rows):
        s, e = f"{9 + i:02d}:00:00", f"{9 + i:02d}:20:00"
        slat, elat = 49.26 + i * 0.02, 49.28 + i * 0.02
        out.append({
            "wave_row": wr, "accessCode": person,
            "start_date": date, "start_time": ts(s), "end_date": date, "end_time": ts(e),
            "mode_of_travel": "Auto Driver", "purpose_of_travel": "Work",
            "start_latitude": slat, "start_longitude": -123.12,
            "end_latitude": elat, "end_longitude": -123.12,
            "routes": repr([[slat, -123.12, f"{date}T{ts(s)}"],
                            [elat, -123.12, f"{date}T{ts(e)}"]]),
        })
    return out


def test_combine_sessions_writes_both_files_and_leaves_sources_untouched(tmp_path, monkeypatch):
    from app import combine, regions, routing
    monkeypatch.setattr(routing, "osrm_route", lambda *a, **k: [])
    monkeypatch.setattr(regions, "_PATH", str(tmp_path / "no-regions.json"))

    src = tmp_path / "sessions"
    a = _mini_session(src, "aaaa", _raw_rows("p1", [1, 2]))
    b = _mini_session(src, "bbbb", _raw_rows("p2", [3, 4]))
    before = {p: sorted(__import__("os").listdir(p)) for p in (a, b)}

    cleaned_path, dofile_path = combine.combine_sessions(
        {"A": a, "B": b}, out_dir=str(tmp_path / "final"),
        work_dir=str(tmp_path / "work"))

    cleaned = pd.read_excel(cleaned_path)
    assert sorted(cleaned["wave_row"]) == [1, 2, 3, 4]     # every split represented
    assert set(cleaned["source_split"]) == {"A", "B"}
    sheets = pd.read_excel(dofile_path, sheet_name=None)
    assert {"dofile", "precision", "removed summary", "removed rows"} <= set(sheets)

    # the reviewers' own folders must be exactly as we found them: generating
    # writes cleaned.xlsx/dofile.xlsx into a session dir, so this runs on copies
    after = {p: sorted(__import__("os").listdir(p)) for p in (a, b)}
    assert before == after
    assert "cleaned.xlsx" not in after[a]


def test_combine_sessions_refuses_an_unresolved_overlap(tmp_path, monkeypatch):
    from app import combine, regions, routing
    monkeypatch.setattr(routing, "osrm_route", lambda *a, **k: [])
    monkeypatch.setattr(regions, "_PATH", str(tmp_path / "no-regions.json"))
    src = tmp_path / "sessions"
    a = _mini_session(src, "aaaa", _raw_rows("shared", [1, 2]))
    b = _mini_session(src, "bbbb", _raw_rows("shared", [3, 4]))
    with pytest.raises(combine.CombineError, match="double-count"):
        combine.combine_sessions({"A": a, "B": b}, out_dir=str(tmp_path / "final"),
                                 work_dir=str(tmp_path / "work"))
