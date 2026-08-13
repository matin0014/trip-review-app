"""End-to-end tests of the EXPORT FILES (cleaned.xlsx + dofile.xlsx).

These drive the whole shipping pipeline the way a reviewer's session does:
build a raw workbook, record decisions + manual edits as a decision log, then
`generate_cleaned_and_dofile` (loader -> classify -> fixpoint -> projection ->
atomic xlsx write) and read the two files back. They are self-contained (they
synthesise their own small workbook, so no real data file is needed) and assert
the trip_id grammar, dropped/added rows, column overrides and the dofile audit
columns behave as expected when the user makes trips and does operations.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app import generate, regions, routing, session
from engine.classify import classify_all
from engine.ids import with_ids
from engine.loader import load_trips
from engine.proposals import add_row


@pytest.fixture(autouse=True)
def _offline_osrm(monkeypatch):
    """Keep the export tests offline + fast: no real OSRM calls. By default the
    routing degrades to a straight line (the graceful-failure path); the one test
    that checks stub routing re-stubs a fake path for itself."""
    monkeypatch.setattr(routing, "osrm_route", lambda *a, **k: [])

DATE = "2024-11-05"
PERSON = "P1"
DAY_KEY = f"{PERSON}|{DATE}"
RAW_COLS = ["accessCode", "start_date", "start_time", "end_date", "end_time",
            "mode_of_travel", "purpose_of_travel", "start_latitude",
            "start_longitude", "end_latitude", "end_longitude", "routes"]


def _ts(hms: str) -> str:
    return f"{hms}-08:00"               # the wave2 time column carries a -08:00 offset


def _route(slat, slon, elat, elon, s_hms, e_hms) -> str:
    """A 2-point routes cell in the raw literal format parse_route round-trips."""
    return repr([[slat, slon, f"{DATE}T{_ts(s_hms)}"],
                 [elat, elon, f"{DATE}T{_ts(e_hms)}"]])


def trip(start, end, slat, slon, elat, elon, mode="Auto Driver", purpose="",
         person=PERSON):
    """One raw row dict. Defaults move ~2 km so the trip is not zero_move."""
    return {
        "accessCode": person, "start_date": DATE, "start_time": _ts(start),
        "end_date": DATE, "end_time": _ts(end), "mode_of_travel": mode,
        "purpose_of_travel": purpose, "start_latitude": slat, "start_longitude": slon,
        "end_latitude": elat, "end_longitude": elon,
        "routes": _route(slat, slon, elat, elon, start, end),
    }


# A clean, well-separated 3-trip day: each moves ~2 km, large gaps -> no flags
# fire, so manual-op outputs are deterministic without accepting anything.
def clean_day_rows():
    return [
        trip("09:00:00", "09:20:00", 49.260, -123.120, 49.278, -123.120),
        trip("10:00:00", "10:20:00", 49.278, -123.120, 49.296, -123.120),
        trip("11:00:00", "11:20:00", 49.296, -123.120, 49.314, -123.120),
    ]


def _make_session(tmp_path, monkeypatch, rows) -> str:
    """Write a raw workbook + minimal proposals into a fresh session dir."""
    monkeypatch.setattr(session, "SESSIONS_DIR", str(tmp_path))
    sid = "s"
    session.write_proposals(sid, {"raw_name": "raw.xlsx"})   # creates the dir
    raw_path = f"{session.session_dir(sid)}/raw.xlsx"
    pd.DataFrame(rows, columns=RAW_COLS).to_excel(raw_path, index=False)
    return sid


def _export(sid):
    """Run the real generator and read both output files back as DataFrames."""
    cleaned_path, dofile_path = generate.generate_cleaned_and_dofile(sid)
    return pd.read_excel(cleaned_path), pd.read_excel(dofile_path)


def tids(df) -> list:
    return [str(x) for x in df["trip_id"]]


def flag_id(sid, ftype: str) -> str:
    raw_path = f"{session.session_dir(sid)}/raw.xlsx"
    flags = with_ids(classify_all(load_trips(raw_path)))
    return next(f.flag_id for f in flags if f.flag_type == ftype)


# --- baseline -------------------------------------------------------------

def test_no_operations_keeps_every_row(tmp_path, monkeypatch):
    sid = _make_session(tmp_path, monkeypatch, clean_day_rows())
    cleaned, dofile = _export(sid)
    assert tids(cleaned) == ["1", "2", "3"]            # plain keeps, no edits
    assert len(cleaned) == 3
    assert tids(dofile) == ["1", "2", "3"]
    assert list(dofile["status"]) == ["kept", "kept", "kept"]


# --- delete ---------------------------------------------------------------

def test_manual_delete_drops_from_cleaned_keeps_R_in_dofile(tmp_path, monkeypatch):
    sid = _make_session(tmp_path, monkeypatch, clean_day_rows())
    session.append_event(sid, {"type": "manual_delete", "raw_row": 2, "day_key": DAY_KEY})
    cleaned, dofile = _export(sid)
    assert tids(cleaned) == ["1", "3"]                 # row 2 gone from the deliverable
    assert tids(dofile) == ["1", "R2", "3"]            # but audited as removed
    assert list(dofile["status"]) == ["kept", "removed", "kept"]


# --- merge ----------------------------------------------------------------

def test_manual_merge_collapses_to_one_row_in_cleaned(tmp_path, monkeypatch):
    # give the two legs DISTINCT modes so the last-leg-mode rule is actually exercised
    rows = [
        trip("09:00:00", "09:20:00", 49.260, -123.120, 49.278, -123.120, mode="Auto Driver"),
        trip("10:00:00", "10:20:00", 49.278, -123.120, 49.296, -123.120, mode="SkyTrain"),
        trip("11:00:00", "11:20:00", 49.296, -123.120, 49.314, -123.120, mode="Auto Driver"),
    ]
    sid = _make_session(tmp_path, monkeypatch, rows)
    session.append_event(sid, {"type": "manual_merge", "raw_rows": [1, 2], "day_key": DAY_KEY})
    cleaned, dofile = _export(sid)
    assert tids(cleaned) == ["M1", "3"]                # merge is ONE row in the cleaned file
    m1 = cleaned[cleaned["trip_id"] == "M1"].iloc[0]
    assert str(m1["start_time"]).startswith("09:00")   # first leg's start
    assert str(m1["end_time"]).startswith("10:20")     # extended to the last leg's end
    assert m1["mode_of_travel"] == "SkyTrain"          # LAST leg's mode, matching the viewer
    assert len(eval(m1["routes"])) == 4                # both legs' breadcrumbs concatenated
    assert list(dofile["status"]) == ["merged", "merged", "kept"]  # Do-file keeps both legs


def test_merging_an_already_merged_trip_extends_the_same_group(tmp_path, monkeypatch):
    sid = _make_session(tmp_path, monkeypatch, clean_day_rows())
    session.append_event(sid, {"type": "manual_merge", "raw_rows": [1, 2], "day_key": DAY_KEY})
    session.append_event(sid, {"type": "manual_merge", "raw_rows": [1, 3], "day_key": DAY_KEY})
    cleaned, _ = _export(sid)
    assert tids(cleaned) == ["M1"]                      # all three fold into ONE M1 row
    assert str(cleaned.iloc[0]["end_time"]).startswith("11:20")  # extended to the last leg


# --- split ----------------------------------------------------------------

def test_manual_split_yields_S_A_and_S_B_rows(tmp_path, monkeypatch):
    sid = _make_session(tmp_path, monkeypatch, clean_day_rows())
    session.append_event(sid, {"type": "manual_split", "raw_row": 1,
                               "split_ts": f"{DATE}T09:10:00", "mode_a": "Walk",
                               "mode_b": "Bus", "day_key": DAY_KEY})
    cleaned, dofile = _export(sid)
    ids = tids(cleaned)
    assert "S1-A" in ids and "S1-B" in ids            # origin kept as A, B appended
    # the kept A row's end time is truncated to the split instant (no double count)
    a = cleaned[cleaned["trip_id"] == "S1-A"].iloc[0]
    assert str(a["end_time"]).startswith("09:10")
    assert a["mode_of_travel"] == "Walk"
    b = cleaned[cleaned["trip_id"] == "S1-B"].iloc[0]
    assert b["mode_of_travel"] == "Bus"
    assert "split" in set(dofile["status"])


# --- add (with OSM-style route) -------------------------------------------

def test_manual_add_appends_routed_A_row(tmp_path, monkeypatch):
    sid = _make_session(tmp_path, monkeypatch, clean_day_rows())
    session.append_event(sid, {
        "type": "manual_add", "start_ts": f"{DATE}T12:00:00", "end_ts": f"{DATE}T12:15:00",
        "mode": "Bike", "purpose": "Shopping",
        "start_lat": 49.30, "start_lon": -123.12, "end_lat": 49.31, "end_lon": -123.11,
        "route": [[49.30, -123.12], [49.305, -123.115], [49.31, -123.11]],
        "day_key": DAY_KEY})
    cleaned, dofile = _export(sid)
    assert tids(cleaned) == ["1", "2", "3", "A01"]     # 12:00 add sorts after the 09/10/11 trips
    added = cleaned[cleaned["trip_id"] == "A01"].iloc[0]
    assert added["mode_of_travel"] == "Bike" and added["purpose_of_travel"] == "Shopping"
    assert added["routes"] != "[]"                     # OSM path serialised into the cell
    assert "added" in set(dofile["status"])


def test_add_keeps_its_person_when_the_days_only_trip_was_zero_move_removed(tmp_path, monkeypatch):
    # Regression (real session): the day's single trip is an accepted zero_move,
    # so projection removes it BEFORE the add materialises -- leaving no trip to
    # infer person from. The add must still carry the participant (from the
    # op's day_key), or it exports with a blank accessCode and falls to the
    # bottom of the file instead of sorting into the person's rows.
    stationary = trip("09:00:00", "09:20:00", 49.260, -123.120, 49.2601, -123.120,
                      mode="Walk")
    # a near-zero displacement route so classify_all flags zero_move
    stationary["routes"] = _route(49.260, -123.120, 49.2601, -123.120, "09:00:00", "09:20:00")
    sid = _make_session(tmp_path, monkeypatch, [stationary])
    session.append_event(sid, {"type": "decision",
                               "flag_id": flag_id(sid, "zero_move"), "resolution": "accept"})
    session.append_event(sid, {
        "type": "manual_add", "start_ts": f"{DATE}T10:00:00", "end_ts": f"{DATE}T10:15:00",
        "mode": "Bike", "purpose": "Shopping",
        "start_lat": 49.30, "start_lon": -123.12, "end_lat": 49.31, "end_lon": -123.11,
        "route": [[49.30, -123.12], [49.31, -123.11]], "day_key": DAY_KEY})
    cleaned, _ = _export(sid)
    added = cleaned[cleaned["trip_id"] == "A01"].iloc[0]
    assert added["accessCode"] == PERSON      # not blank/NaN
    assert pd.notna(added["accessCode"])


def test_manual_add_sorts_into_chronological_position(tmp_path, monkeypatch):
    # an add whose start falls BETWEEN existing trips must slot in by time, not pile
    # up at the bottom of the cleaned file.
    sid = _make_session(tmp_path, monkeypatch, clean_day_rows())   # trips at 09:00 / 10:00 / 11:00
    session.append_event(sid, {
        "type": "manual_add", "start_ts": f"{DATE}T09:30:00", "end_ts": f"{DATE}T09:45:00",
        "mode": "Bike", "purpose": "Errand",
        "start_lat": 49.278, "start_lon": -123.120, "end_lat": 49.28, "end_lon": -123.120,
        "route": [[49.278, -123.120], [49.28, -123.120]], "day_key": DAY_KEY})
    cleaned, _ = _export(sid)
    assert tids(cleaned) == ["1", "A01", "2", "3"]     # slotted between trip 1 and trip 2


def test_merge_logged_with_added_collapses_to_one_row(tmp_path, monkeypatch):
    # add a trip 09:30-09:50, then MERGE it with logged row 1 (09:00-09:20). The
    # merged trip must be ONE M-row anchored to row 1 (spanning to 09:50), NOT the
    # logged row plus a stray A-row for the added leg (the reported bug).
    sid = _make_session(tmp_path, monkeypatch, clean_day_rows())
    session.append_event(sid, {
        "type": "manual_add", "start_ts": f"{DATE}T09:30:00", "end_ts": f"{DATE}T09:50:00",
        "mode": "Bike", "purpose": "Errand",
        "start_lat": 49.278, "start_lon": -123.120, "end_lat": 49.30, "end_lon": -123.12,
        "route": [[49.278, -123.120], [49.30, -123.12]], "day_key": DAY_KEY})
    session.append_event(sid, {"type": "manual_merge",
                               "raw_rows": [1, add_row(1)], "day_key": DAY_KEY})
    cleaned, dofile = _export(sid)
    assert tids(cleaned) == ["M1", "2", "3"]           # ONE merged row, no stray A-row
    m1 = cleaned[cleaned["trip_id"] == "M1"].iloc[0]
    assert str(m1["start_time"]).startswith("09:00")   # first (logged) leg's start
    assert str(m1["end_time"]).startswith("09:50")     # extended over the added leg
    assert m1["mode_of_travel"] == "Bike"              # LAST leg's mode (the added leg), matching the viewer
    assert len(eval(m1["routes"])) == 4                # logged (2) + added (2) breadcrumbs
    assert "added" not in set(dofile["status"])        # the added leg folded into M1, not an A-row
    assert "merged" in set(dofile["status"])


def test_merge_two_added_trips_is_one_row(tmp_path, monkeypatch):
    # Two ADDED trips merged: exactly ONE A-row spanning both (no duplicate, and the
    # end must be the SECOND leg's, not the first's — the added+added corruption bug).
    sid = _make_session(tmp_path, monkeypatch, clean_day_rows())
    session.append_event(sid, {
        "type": "manual_add", "start_ts": f"{DATE}T13:00:00", "end_ts": f"{DATE}T13:20:00",
        "mode": "Bike", "purpose": "", "start_lat": 49.30, "start_lon": -123.12,
        "end_lat": 49.31, "end_lon": -123.11, "route": [[49.30, -123.12], [49.31, -123.11]],
        "day_key": DAY_KEY})
    session.append_event(sid, {
        "type": "manual_add", "start_ts": f"{DATE}T13:30:00", "end_ts": f"{DATE}T13:55:00",
        "mode": "Bike", "purpose": "", "start_lat": 49.31, "start_lon": -123.11,
        "end_lat": 49.33, "end_lon": -123.10, "route": [[49.31, -123.11], [49.33, -123.10]],
        "day_key": DAY_KEY})
    session.append_event(sid, {"type": "manual_merge",
                               "raw_rows": [add_row(1), add_row(2)], "day_key": DAY_KEY})
    cleaned, _ = _export(sid)
    assert tids(cleaned) == ["1", "2", "3", "A01"]     # one merged add-row, not two
    a = cleaned[cleaned["trip_id"] == "A01"].iloc[0]
    assert str(a["start_time"]).startswith("13:00")    # first added leg's start
    assert str(a["end_time"]).startswith("13:55")      # extended to the SECOND leg's end
    assert len(eval(a["routes"])) == 4                 # both legs' breadcrumbs, no duplication


# --- mode / purpose / time edits ------------------------------------------

def test_manual_mode_and_purpose_overrides_land_in_cleaned(tmp_path, monkeypatch):
    sid = _make_session(tmp_path, monkeypatch, clean_day_rows())
    session.append_event(sid, {"type": "manual_mode", "raw_row": 2, "mode": "SkyTrain", "day_key": DAY_KEY})
    session.append_event(sid, {"type": "manual_purpose", "raw_row": 2, "purpose": "Work", "day_key": DAY_KEY})
    cleaned, _ = _export(sid)
    row2 = cleaned.iloc[1]
    assert row2["mode_of_travel"] == "SkyTrain"
    assert row2["purpose_of_travel"] == "Work"
    assert tids(cleaned) == ["1", "2", "3"]            # ids unchanged by a label edit


def test_manual_time_override_rewrites_the_time_columns(tmp_path, monkeypatch):
    sid = _make_session(tmp_path, monkeypatch, clean_day_rows())
    session.append_event(sid, {"type": "manual_time", "raw_row": 1,
                               "start_ts": f"{DATE}T08:30:00", "end_ts": f"{DATE}T08:45:00",
                               "day_key": DAY_KEY})
    cleaned, _ = _export(sid)
    row1 = cleaned.iloc[0]
    assert str(row1["start_time"]).startswith("08:30")
    assert str(row1["end_time"]).startswith("08:45")


# --- accepted flag (decision, not a manual op) ----------------------------

def test_accepted_fragment_merges_into_one_M_id(tmp_path, monkeypatch):
    # two trips <200 m and <3 min apart -> short-gap fragment; accepting it merges
    rows = [
        trip("09:00:00", "09:20:00", 49.260, -123.120, 49.278, -123.120),
        trip("09:21:00", "09:40:00", 49.2781, -123.1201, 49.296, -123.120),
    ]
    sid = _make_session(tmp_path, monkeypatch, rows)
    session.append_event(sid, {"type": "decision",
                               "flag_id": flag_id(sid, "fragment"), "resolution": "accept"})
    cleaned, dofile = _export(sid)
    assert tids(cleaned) == ["M1"]                      # accepted fragment -> one cleaned row
    assert list(tids(dofile)) == ["M1", "M1"]           # Do-file still audits both legs
    assert "fragment:accept" in " ".join(str(x) for x in dofile["flags"])


def test_accepted_missing_trip_stub_is_osrm_routed(tmp_path, monkeypatch):
    # Accepting a missing_trip inserts an A-stub for the gap; its route must follow
    # the OSM street path (like a manual add), not stay a straight "[]" line.
    rows = [
        trip("09:00:00", "09:04:00", 49.260, -123.120, 49.278, -123.120),   # ~30 km/h legs
        trip("09:14:00", "09:18:00", 49.287, -123.120, 49.305, -123.120),   # ~1 km / 10 min gap
    ]
    sid = _make_session(tmp_path, monkeypatch, rows)
    fake_path = [[49.278, -123.120], [49.282, -123.120], [49.287, -123.120]]
    monkeypatch.setattr(routing, "osrm_route", lambda *a, **k: fake_path)
    session.append_event(sid, {"type": "decision",
                               "flag_id": flag_id(sid, "missing_trip"), "resolution": "accept"})
    cleaned, _ = _export(sid)
    a = cleaned[cleaned["trip_id"] == "A01"].iloc[0]
    assert a["routes"] != "[]"                          # routed, not a straight line
    assert len(eval(a["routes"])) == 3                  # the OSM path breadcrumbs


def test_accepted_fragment_of_logged_and_added_anchors_to_workbook_row(tmp_path, monkeypatch):
    # Reproduces the Sunday export bug: accepting a fragment between a LOGGED trip
    # (row 1) and a manually-ADDED trip must anchor the merge to the workbook row
    # (M1) and extend the row over the whole span (to 09:50), NOT collapse onto the
    # added leg's negative raw_row (M-2000001) with the logged leg's short end time.
    from engine.classify import classify_day, group_person_days
    from engine.proposals import apply_manual_ops

    rows = [trip("09:00:00", "09:20:00", 49.260, -123.120, 49.278, -123.120)]
    sid = _make_session(tmp_path, monkeypatch, rows)
    add_op = {
        "type": "manual_add", "start_ts": f"{DATE}T09:21:00", "end_ts": f"{DATE}T09:50:00",
        "mode": "Bike", "purpose": "Errand",
        "start_lat": 49.2781, "start_lon": -123.1201, "end_lat": 49.30, "end_lon": -123.12,
        "route": [[49.2781, -123.1201], [49.30, -123.12]], "day_key": DAY_KEY}
    session.append_event(sid, add_op)

    # fragment id must be computed with the add present (flag_id() ignores manual ops)
    raw_path = f"{session.session_dir(sid)}/raw.xlsx"
    live = apply_manual_ops(load_trips(raw_path), [add_op])
    frag = next(f.flag_id for day in group_person_days(live).values()
                for f in with_ids(classify_day(day)) if f.flag_type == "fragment")
    session.append_event(sid, {"type": "decision", "flag_id": frag, "resolution": "accept"})

    cleaned, dofile = _export(sid)
    assert tids(cleaned) == ["M1"]                      # anchored to the workbook row, not M-2000001
    m1 = cleaned[cleaned["trip_id"] == "M1"].iloc[0]
    assert str(m1["start_time"]).startswith("09:00")   # logged leg's start
    assert str(m1["end_time"]).startswith("09:50")     # extended over the added leg (was 09:20)
    assert "added" not in set(dofile["status"])         # the added leg folded in, not a stray A-row


# --- precision sheet --------------------------------------------------------

def test_precision_sheet_counts_an_accepted_fragment_as_a_tp(tmp_path, monkeypatch):
    rows = [
        trip("09:00:00", "09:20:00", 49.260, -123.120, 49.278, -123.120),
        trip("09:21:00", "09:40:00", 49.2781, -123.1201, 49.296, -123.120),
    ]
    sid = _make_session(tmp_path, monkeypatch, rows)
    session.append_event(sid, {"type": "decision",
                               "flag_id": flag_id(sid, "fragment"), "resolution": "accept"})
    _cleaned, dofile_path = generate.generate_cleaned_and_dofile(sid)
    precision = pd.read_excel(dofile_path, sheet_name="precision")
    row = precision[precision["detector"] == "fragment"].iloc[0]
    assert row["origin"] == "ingest" and row["tp"] == 1 and row["fp"] == 0
    assert row["precision"] == 1.0


def test_precision_sheet_counts_a_waived_fragment_as_an_fp(tmp_path, monkeypatch):
    rows = [
        trip("09:00:00", "09:20:00", 49.260, -123.120, 49.278, -123.120),
        trip("09:21:00", "09:40:00", 49.2781, -123.1201, 49.296, -123.120),
    ]
    sid = _make_session(tmp_path, monkeypatch, rows)
    session.append_event(sid, {"type": "decision",
                               "flag_id": flag_id(sid, "fragment"), "resolution": "waive"})
    _cleaned, dofile_path = generate.generate_cleaned_and_dofile(sid)
    precision = pd.read_excel(dofile_path, sheet_name="precision")
    row = precision[precision["detector"] == "fragment"].iloc[0]
    assert row["origin"] == "ingest" and row["tp"] == 0 and row["fp"] == 1
    assert row["precision"] == 0.0


# --- combined edits stay consistent across both files ---------------------

def test_combined_edits_cleaned_and_dofile_agree(tmp_path, monkeypatch):
    sid = _make_session(tmp_path, monkeypatch, clean_day_rows())
    session.append_event(sid, {"type": "manual_delete", "raw_row": 3, "day_key": DAY_KEY})
    session.append_event(sid, {"type": "manual_mode", "raw_row": 1, "mode": "Walk", "day_key": DAY_KEY})
    session.append_event(sid, {
        "type": "manual_add", "start_ts": f"{DATE}T13:00:00", "end_ts": f"{DATE}T13:10:00",
        "mode": "Bus", "purpose": "", "start_lat": 49.30, "start_lon": -123.12,
        "end_lat": 49.31, "end_lon": -123.11, "route": [], "day_key": DAY_KEY})
    cleaned, dofile = _export(sid)
    assert tids(cleaned) == ["1", "2", "A01"]          # row3 deleted, A01 added
    assert cleaned.iloc[0]["mode_of_travel"] == "Walk"
    # every surviving cleaned id is present in the dofile with the same value
    dofile_ids = set(tids(dofile))
    assert set(tids(cleaned)) <= dofile_ids
    assert "R3" in dofile_ids                          # the delete is audited


# --- out-of-region auto-removal (recoverable) -----------------------------

# A second person whose single day leaves the study area (a Seattle endpoint).
P2 = "P2"
P2_DAY_KEY = f"{P2}|{DATE}"


def out_of_region_rows():
    """P1's clean in-region day plus P2's day with one trip ending in Seattle."""
    return clean_day_rows() + [
        trip("09:00:00", "09:40:00", 49.260, -123.120, 47.610, -122.340, person=P2),
    ]


def test_out_of_region_day_is_dropped_from_cleaned_and_audited(tmp_path, monkeypatch):
    monkeypatch.setattr(regions, "_PATH", str(tmp_path / "no-regions.json"))  # use defaults
    sid = _make_session(tmp_path, monkeypatch, out_of_region_rows())
    cleaned, dofile = _export(sid)
    assert tids(cleaned) == ["1", "2", "3"]            # P2's row 4 auto-removed
    assert "4" in tids(dofile)                          # but retained in the audit file
    row4 = dofile[dofile["trip_id"].astype(str) == "4"].iloc[0]
    assert row4["status"] == "excluded"
    assert "out_of_region:excluded" in str(row4["flags"])


def test_recovering_an_out_of_region_day_re_includes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(regions, "_PATH", str(tmp_path / "no-regions.json"))
    sid = _make_session(tmp_path, monkeypatch, out_of_region_rows())
    session.append_event(sid, {"type": "day_include", "day_key": P2_DAY_KEY, "include": True})
    cleaned, dofile = _export(sid)
    assert tids(cleaned) == ["1", "2", "3", "4"]        # P2's day recovered into the deliverable
    assert "excluded" not in set(dofile["status"])


# --- reposition -------------------------------------------------------------

def test_reposition_survives_into_the_export(tmp_path, monkeypatch):
    # a plain kept trip's end point moved, with a real kept-breadcrumbs + patch
    # route (as the server would precompute) -- both cleaned.xlsx and dofile.xlsx
    # must show the NEW endpoint/route, not the original raw values.
    sid = _make_session(tmp_path, monkeypatch, clean_day_rows())
    new_route = [
        [49.260, -123.120, f"{DATE}T{_ts('09:00:00')}"],
        [49.278, -123.120, f"{DATE}T{_ts('09:10:00')}"],
        [49.290, -123.100, f"{DATE}T{_ts('09:20:00')}"],   # the new patched point
    ]
    session.append_event(sid, {
        "type": "manual_reposition", "raw_row": 1, "end": "end",
        "lat": 49.290, "lon": -123.100, "route": new_route, "day_key": DAY_KEY})
    cleaned, dofile = _export(sid)

    row = cleaned[cleaned["trip_id"].astype(str) == "1"].iloc[0]
    assert row["end_latitude"] == pytest.approx(49.290)
    assert row["end_longitude"] == pytest.approx(-123.100)
    assert row["start_latitude"] == pytest.approx(49.260)  # start untouched
    assert len(eval(row["routes"])) == 3                   # 2 kept + 1 patch point

    d = dofile[dofile["trip_id"].astype(str) == "1"].iloc[0]
    assert d["end_latitude"] == pytest.approx(49.290)      # audit ledger shows it too
    assert d["status"] == "kept"                           # reposition isn't a status change


# --- undo -----------------------------------------------------------------

def test_undo_reverts_an_operation(tmp_path, monkeypatch):
    sid = _make_session(tmp_path, monkeypatch, clean_day_rows())
    session.append_event(sid, {"type": "manual_delete", "raw_row": 2, "day_key": DAY_KEY})
    session.append_event(sid, {"type": "revert"})      # undo the delete
    cleaned, dofile = _export(sid)
    assert tids(cleaned) == ["1", "2", "3"]            # row 2 is back
    assert "removed" not in set(dofile["status"])


# --- zero-movement days ---------------------------------------------------

def _stationary(start, end, lat=49.260, lon=-123.120, person=PERSON, date=DATE):
    """A trip that never really moves, so classify_all flags it zero_move."""
    t = trip(start, end, lat, lon, lat + 0.0001, lon, mode="Walk", person=person)
    t["routes"] = _route(lat, lon, lat + 0.0001, lon, start, end)
    t["start_date"] = t["end_date"] = date
    return t


def _accept_all_zero_moves(sid):
    raw_path = f"{session.session_dir(sid)}/raw.xlsx"
    for f in with_ids(classify_all(load_trips(raw_path))):
        if f.flag_type == "zero_move":
            session.append_event(sid, {"type": "decision", "flag_id": f.flag_id,
                                       "resolution": "accept"})


def test_a_day_of_only_zero_move_trips_survives_as_one_Z_row(tmp_path, monkeypatch):
    # The whole day is GPS drift, so every row is removed. Without a placeholder
    # the day would vanish and be indistinguishable from a day never reported.
    sid = _make_session(tmp_path, monkeypatch,
                        [_stationary("09:00:00", "09:20:00"),
                         _stationary("14:00:00", "14:20:00")])
    _accept_all_zero_moves(sid)
    cleaned, dofile = _export(sid)

    assert tids(cleaned) == ["Z1"]                    # ONE row for the whole day
    row = cleaned.iloc[0]
    assert row["comment"] == generate.ZERO_MOVE_NOTE
    assert row["accessCode"] == PERSON                # who
    assert str(row["start_date"])[:10] == DATE        # and when: both kept
    # ...but nothing describing a journey, because none happened
    for col in ("start_time", "end_time", "mode_of_travel", "purpose_of_travel",
                "start_latitude", "end_latitude", "routes"):
        assert pd.isna(row[col]), col
    # The individual trips are still audited as removed in the ledger, but as
    # ZR<row> rather than R<row>: their removal says the whole DAY was drift, not
    # that this particular trip was junk, so an analysis of what reviewers chose
    # to remove can filter them out.
    assert set(tids(dofile)) == {"ZR1", "ZR2"}
    assert set(dofile["status"]) == {"removed"}   # still a removal, not its own status


def test_a_participant_whose_only_day_is_zero_move_is_kept_not_removed(tmp_path, monkeypatch):
    # A day with no travel is a finding, not a deletion: the person stays in the
    # dataset and must not be counted among the removed participants.
    sid = _make_session(tmp_path, monkeypatch, [_stationary("09:00:00", "09:20:00")])
    _accept_all_zero_moves(sid)
    cleaned, _ = _export(sid)
    assert tids(cleaned) == ["Z1"]
    assert cleaned.iloc[0]["accessCode"] == PERSON

    summary, rows = generate.build_removed_dfs(
        f"{session.session_dir(sid)}/raw.xlsx", session.read_events(sid), set())
    n = dict(zip(summary["Measure"], summary["Count"]))
    assert n["People removed entirely"] == 0
    assert n["Days removed (total)"] == 0
    assert rows.empty


def test_a_hand_deleted_day_is_not_reported_as_zero_movement(tmp_path, monkeypatch):
    # Deleting trips by hand says "these records are junk", which is NOT a claim
    # that the participant stayed put, so no Z row is invented for that day.
    sid = _make_session(tmp_path, monkeypatch, clean_day_rows())
    for r in (1, 2, 3):
        session.append_event(sid, {"type": "manual_delete", "raw_row": r, "day_key": DAY_KEY})
    cleaned, _ = _export(sid)
    assert not any(t.startswith("Z") for t in tids(cleaned))
    assert len(cleaned) == 0


def test_a_day_with_one_surviving_trip_gets_no_Z_row(tmp_path, monkeypatch):
    # Only a day left with NOTHING becomes a zero-movement day.
    sid = _make_session(tmp_path, monkeypatch,
                        [_stationary("09:00:00", "09:20:00"),
                         trip("14:00:00", "14:20:00", 49.260, -123.120, 49.278, -123.120)])
    _accept_all_zero_moves(sid)
    cleaned, _ = _export(sid)
    assert tids(cleaned) == ["2"]                     # the real trip, no Z row


# --- removed people / days accounting -------------------------------------

def _on(row, date):
    row = dict(row)
    row["start_date"] = row["end_date"] = date
    return row


def test_dofile_carries_the_removed_summary_and_detail_sheets(tmp_path, monkeypatch):
    monkeypatch.setattr(regions, "_PATH", str(tmp_path / "no-regions.json"))
    d1, d2 = "2024-11-05", "2024-11-06"
    rows = [_on(trip("09:00:00", "09:20:00", 49.260, -123.120, 49.278, -123.120), d1),
            _on(trip("09:00:00", "09:20:00", 49.260, -123.120, 49.278, -123.120), d2),
            _on(trip("09:00:00", "09:20:00", 49.260, -123.120, 49.278, -123.120,
                     person="P2"), d1)]
    sid = _make_session(tmp_path, monkeypatch, rows)
    # P2 loses their only day -> removed entirely. P1 loses one of two -> partial.
    session.append_event(sid, {"type": "day_include", "day_key": f"P2|{d1}", "include": False})
    session.append_event(sid, {"type": "day_include", "day_key": f"{PERSON}|{d2}", "include": False})

    _, dofile_path = generate.generate_cleaned_and_dofile(sid)
    sheets = pd.read_excel(dofile_path, sheet_name=None)
    assert "removed summary" in sheets and "removed rows" in sheets

    n = dict(zip(sheets["removed summary"]["Measure"], sheets["removed summary"]["Count"]))
    assert n["People removed entirely"] == 1          # P2
    assert n["People who lost some days"] == 1        # P1
    assert n["Days removed (total)"] == 2
    assert n["  ...removed by the reviewer"] == 2
    assert n["Trips removed with those days"] == 2

    detail = sheets["removed rows"]
    assert set(detail["person"]) == {"P2", PERSON}
    assert set(detail["reason"]) == {"removed by reviewer"}
    # the fully-removed participant is marked as such, so they can be told apart
    assert detail[detail["person"] == "P2"]["person_fully_removed"].all()
    assert not detail[detail["person"] == PERSON]["person_fully_removed"].any()
    # and their original survey data is preserved verbatim for reinstatement
    assert "start_latitude" in detail.columns
    assert detail.iloc[0]["start_latitude"] == pytest.approx(49.260)


def test_removed_sheets_are_empty_when_nothing_was_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(regions, "_PATH", str(tmp_path / "no-regions.json"))
    sid = _make_session(tmp_path, monkeypatch, clean_day_rows())
    _, dofile_path = generate.generate_cleaned_and_dofile(sid)
    sheets = pd.read_excel(dofile_path, sheet_name=None)
    n = dict(zip(sheets["removed summary"]["Measure"], sheets["removed summary"]["Count"]))
    assert n["People removed entirely"] == 0
    assert n["Days removed (total)"] == 0
    assert sheets["removed rows"].empty


# --- rows the fixpoint dropped must not reappear in the cleaned file --------
#
# Regression, found by reconciling the in-app start-times chart (which counts
# `live` trips) against the export: they disagreed by 185 rows across the four
# wave-2 splits. Accepting a merge and then removing ONE member takes the whole
# group out of `live` -- the reviewer's screen shows no such trip -- but the
# export's `removed` set only names the member that was explicitly removed, so
# the survivors fell through to the plain-keep branch and were exported with
# their original times and coordinates, indistinguishable from untouched trips.
# Three different removals trigger it; all three are pinned here.

def _fragment_pair():
    """Two trips <200 m and <3 min apart, so a short-gap `fragment` fires."""
    return [
        trip("09:00:00", "09:20:00", 49.260, -123.120, 49.278, -123.120),
        trip("09:21:00", "09:40:00", 49.2781, -123.1201, 49.296, -123.120),
    ]


def _live_rows(sid, day_key=DAY_KEY):
    """The raw rows the reviewer's screen still accounts for."""
    from app import decisions, state
    events = session.read_events(sid)
    res = decisions.resolutions(events)
    accepted = {f for f, r in res.items() if r in decisions.APPLY_RESOLUTIONS}
    ops = [o for o in decisions.manual_ops(events) if o.get("day_key") == day_key]
    state.clear(sid)
    live, _ = decisions.fixpoint(state.day_trips(sid, day_key), accepted, ops=ops)
    rows = set()
    for t in live:
        rows |= {t.raw_row} | set(t.absorbed)
    state.clear(sid)
    return rows


def test_merge_member_deleted_does_not_resurrect_the_other_half(tmp_path, monkeypatch):
    # Accept "these two are one journey", then delete it. On screen that is ONE
    # merged trip being deleted, so neither half may survive the export.
    sid = _make_session(tmp_path, monkeypatch, _fragment_pair())
    session.append_event(sid, {"type": "decision",
                               "flag_id": flag_id(sid, "fragment"), "resolution": "accept"})
    session.append_event(sid, {"type": "manual_delete", "raw_row": 1, "day_key": DAY_KEY})

    assert _live_rows(sid) == set()                 # the reviewer sees nothing left
    cleaned, dofile = _export(sid)
    assert tids(cleaned) == []                      # ...so the deliverable has nothing
    assert set(tids(dofile)) == {"R1", "R2"}        # both audited as removed


def test_merge_member_split_does_not_resurrect_the_other_half(tmp_path, monkeypatch):
    # Splitting a member re-shapes the group; the export must follow `live`
    # rather than exporting the untouched partner as an ordinary trip.
    sid = _make_session(tmp_path, monkeypatch, _fragment_pair())
    session.append_event(sid, {"type": "decision",
                               "flag_id": flag_id(sid, "fragment"), "resolution": "accept"})
    session.append_event(sid, {"type": "manual_split", "raw_row": 1, "day_key": DAY_KEY,
                               "split_ts": f"{DATE}T09:10:00", "mode_a": "Walk",
                               "mode_b": "Walk"})

    live = _live_rows(sid)
    cleaned, _ = _export(sid)
    plain = {int(t) for t in tids(cleaned) if str(t).isdigit()}
    assert plain <= live, f"exported {plain - live} which the fixpoint had dropped"


def test_no_cleaned_row_is_ever_absent_from_the_live_view(tmp_path, monkeypatch):
    # The general invariant the three cases above are instances of: every
    # workbook row in the cleaned file must be one the reviewer's screen shows.
    sid = _make_session(tmp_path, monkeypatch, _fragment_pair() + [
        trip("11:00:00", "11:20:00", 49.296, -123.120, 49.314, -123.120)])
    session.append_event(sid, {"type": "decision",
                               "flag_id": flag_id(sid, "fragment"), "resolution": "accept"})
    session.append_event(sid, {"type": "manual_delete", "raw_row": 2, "day_key": DAY_KEY})

    live = _live_rows(sid)
    cleaned, _ = _export(sid)
    for t in tids(cleaned):
        if str(t).isdigit():
            assert int(t) in live
        elif str(t).startswith("M"):
            assert int(str(t)[1:]) in live
