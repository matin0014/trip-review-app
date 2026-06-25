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

from app import generate, session
from engine.classify import classify_all
from engine.ids import with_ids
from engine.loader import load_trips

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

def test_manual_merge_shares_one_M_id(tmp_path, monkeypatch):
    sid = _make_session(tmp_path, monkeypatch, clean_day_rows())
    session.append_event(sid, {"type": "manual_merge", "raw_rows": [1, 2], "day_key": DAY_KEY})
    cleaned, dofile = _export(sid)
    assert tids(cleaned) == ["M1", "M1", "3"]          # both members share M<lowest row>
    assert list(dofile["status"]) == ["merged", "merged", "kept"]


def test_merging_an_already_merged_trip_extends_the_same_group(tmp_path, monkeypatch):
    sid = _make_session(tmp_path, monkeypatch, clean_day_rows())
    session.append_event(sid, {"type": "manual_merge", "raw_rows": [1, 2], "day_key": DAY_KEY})
    session.append_event(sid, {"type": "manual_merge", "raw_rows": [1, 3], "day_key": DAY_KEY})
    cleaned, _ = _export(sid)
    assert tids(cleaned) == ["M1", "M1", "M1"]         # all fold into one M1 group


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
    assert tids(cleaned) == ["1", "2", "3", "A01"]     # appended after the keeps
    added = cleaned[cleaned["trip_id"] == "A01"].iloc[0]
    assert added["mode_of_travel"] == "Bike" and added["purpose_of_travel"] == "Shopping"
    assert added["routes"] != "[]"                     # OSM path serialised into the cell
    assert "added" in set(dofile["status"])


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
    assert tids(cleaned) == ["M1", "M1"]
    assert "fragment:accept" in " ".join(str(x) for x in dofile["flags"])


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


# --- undo -----------------------------------------------------------------

def test_undo_reverts_an_operation(tmp_path, monkeypatch):
    sid = _make_session(tmp_path, monkeypatch, clean_day_rows())
    session.append_event(sid, {"type": "manual_delete", "raw_row": 2, "day_key": DAY_KEY})
    session.append_event(sid, {"type": "revert"})      # undo the delete
    cleaned, dofile = _export(sid)
    assert tids(cleaned) == ["1", "2", "3"]            # row 2 is back
    assert "removed" not in set(dofile["status"])
