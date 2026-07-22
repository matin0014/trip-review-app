"""Integration tests for per-trip comments: POST /api/<sid>/comment records a
note, /live-day serves it back for the open day, and the export carries it in a
`comment` column of BOTH cleaned.xlsx and dofile.xlsx (empty when there's none).
"""

from __future__ import annotations

import pandas as pd
import pytest

from app import baseline, generate, regions, routing, server, session, state
from engine.classify import classify_all
from engine.loader import load_trips
from engine.proposals import add_row

DATE = "2024-11-05"
PERSON = "P1"
DAY_KEY = f"{PERSON}|{DATE}"
RAW_COLS = ["accessCode", "start_date", "start_time", "end_date", "end_time",
            "mode_of_travel", "purpose_of_travel", "start_latitude",
            "start_longitude", "end_latitude", "end_longitude", "routes"]


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    monkeypatch.setattr(routing, "osrm_route", lambda *a, **k: [])
    monkeypatch.setattr(regions, "_PATH", str(tmp_path / "no-regions.json"))


def _ts(hms: str) -> str:
    return f"{hms}-08:00"


def _route(slat, slon, elat, elon, s_hms, e_hms) -> str:
    return repr([[slat, slon, f"{DATE}T{_ts(s_hms)}"],
                 [elat, elon, f"{DATE}T{_ts(e_hms)}"]])


def trip(start, end, slat, slon, elat, elon):
    return {
        "accessCode": PERSON, "start_date": DATE, "start_time": _ts(start),
        "end_date": DATE, "end_time": _ts(end), "mode_of_travel": "Auto Driver",
        "purpose_of_travel": "", "start_latitude": slat, "start_longitude": slon,
        "end_latitude": elat, "end_longitude": elon,
        "routes": _route(slat, slon, elat, elon, start, end),
    }


# three well-separated trips: no flags fire, so comment behaviour is isolated
def rows():
    return [
        trip("09:00:00", "09:20:00", 49.260, -123.120, 49.278, -123.120),
        trip("10:00:00", "10:20:00", 49.278, -123.120, 49.296, -123.120),
        trip("11:00:00", "11:20:00", 49.296, -123.120, 49.314, -123.120),
    ]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSIONS_DIR", str(tmp_path))
    state.clear()
    sid = "s"
    session.write_proposals(sid, {"raw_name": "raw.xlsx"})
    raw_path = f"{session.session_dir(sid)}/raw.xlsx"
    pd.DataFrame(rows(), columns=RAW_COLS).to_excel(raw_path, index=False)
    trips = load_trips(raw_path)
    session.write_proposals(sid, baseline.build_baseline(trips, classify_all(trips), "raw.xlsx"))
    yield server.create_app().test_client(), sid
    state.clear()


def _export(sid):
    cleaned_path, dofile_path = generate.generate_cleaned_and_dofile(sid)
    return pd.read_excel(cleaned_path), pd.read_excel(dofile_path)


def _row(df, trip_id):
    return df[df["trip_id"].astype(str) == trip_id].iloc[0]


def _blank(v) -> bool:
    """A row with no note writes an EMPTY Excel cell, which pandas reads back as
    NaN (an empty string does not survive the xlsx round-trip)."""
    return pd.isna(v) or str(v) == ""


# --- endpoint ---------------------------------------------------------------

def test_live_day_has_no_comments_before_any_are_added(client):
    c, sid = client
    assert c.get(f"/api/{sid}/live-day", query_string={"key": DAY_KEY}).get_json()["comments"] == {}


def test_comment_is_saved_and_served_back_for_the_day(client):
    c, sid = client
    r = c.post(f"/api/{sid}/comment",
               json={"day_key": DAY_KEY, "raw_row": 2, "comment": "GPS looks noisy here"})
    assert r.status_code == 200
    d = c.get(f"/api/{sid}/live-day", query_string={"key": DAY_KEY}).get_json()
    assert d["comments"] == {"2": "GPS looks noisy here"}


def test_editing_a_comment_replaces_it_and_empty_clears_it(client):
    c, sid = client
    c.post(f"/api/{sid}/comment", json={"day_key": DAY_KEY, "raw_row": 2, "comment": "first"})
    c.post(f"/api/{sid}/comment", json={"day_key": DAY_KEY, "raw_row": 2, "comment": "second"})
    d = c.get(f"/api/{sid}/live-day", query_string={"key": DAY_KEY}).get_json()
    assert d["comments"] == {"2": "second"}
    c.post(f"/api/{sid}/comment", json={"day_key": DAY_KEY, "raw_row": 2, "comment": ""})
    d = c.get(f"/api/{sid}/live-day", query_string={"key": DAY_KEY}).get_json()
    assert d["comments"] == {}


def test_comment_rejects_a_bad_body(client):
    c, sid = client
    assert c.post(f"/api/{sid}/comment", json={"day_key": DAY_KEY}).status_code == 400
    assert c.post(f"/api/{sid}/comment",
                  json={"day_key": DAY_KEY, "raw_row": 1, "comment": 5}).status_code == 400


def test_comment_unknown_session_404(client):
    c, _ = client
    r = c.post("/api/nope/comment", json={"day_key": DAY_KEY, "raw_row": 1, "comment": "x"})
    assert r.status_code == 404


# --- export -----------------------------------------------------------------

def test_export_has_a_comment_column_empty_when_nothing_is_noted(client):
    _, sid = client
    cleaned, dofile = _export(sid)
    assert "comment" in cleaned.columns and "comment" in dofile.columns
    assert all(_blank(v) for v in cleaned["comment"])        # every cell blank
    assert all(_blank(v) for v in dofile["comment"])


def test_commented_row_carries_its_note_into_both_export_files(client):
    c, sid = client
    c.post(f"/api/{sid}/comment", json={"day_key": DAY_KEY, "raw_row": 2, "comment": "check GPS"})
    cleaned, dofile = _export(sid)
    assert _row(cleaned, "2")["comment"] == "check GPS"
    assert _blank(_row(cleaned, "1")["comment"])             # untouched rows stay empty
    assert _row(dofile, "2")["comment"] == "check GPS"


def test_comment_follows_a_manually_added_trip(client):
    # an added trip's raw_row is negative and per-day; its note must resolve too
    c, sid = client
    c.post(f"/api/{sid}/manual_add", json={
        "day_key": DAY_KEY, "start_time": "13:00", "end_time": "13:15",
        "mode": "Bike", "purpose": "Shopping",
        "start_lat": 49.30, "start_lon": -123.12, "end_lat": 49.31, "end_lon": -123.11})
    c.post(f"/api/{sid}/comment",
           json={"day_key": DAY_KEY, "raw_row": add_row(1), "comment": "added from diary"})
    cleaned, _ = _export(sid)
    assert _row(cleaned, "A01")["comment"] == "added from diary"


def test_merged_row_keeps_every_members_note(client):
    # a merge collapses to ONE cleaned row, so notes on the dropped legs must be
    # carried onto the kept row rather than silently lost
    c, sid = client
    c.post(f"/api/{sid}/comment", json={"day_key": DAY_KEY, "raw_row": 1, "comment": "leg one"})
    c.post(f"/api/{sid}/comment", json={"day_key": DAY_KEY, "raw_row": 2, "comment": "leg two"})
    c.post(f"/api/{sid}/manual_merge", json={"day_key": DAY_KEY, "raw_rows": [1, 2]})
    cleaned, dofile = _export(sid)
    assert _row(cleaned, "M1")["comment"] == "leg one | leg two"
    # the ledger keeps both legs, so each shows its own note
    assert _row(dofile, "M1")["comment"] == "leg one"


def test_comment_survives_a_deleted_row_into_the_dofile(client):
    c, sid = client
    c.post(f"/api/{sid}/comment", json={"day_key": DAY_KEY, "raw_row": 3, "comment": "bogus trip"})
    c.post(f"/api/{sid}/manual_delete", json={"day_key": DAY_KEY, "raw_row": 3})
    cleaned, dofile = _export(sid)
    assert "3" not in [str(t) for t in cleaned["trip_id"]]    # dropped from the deliverable
    assert _row(dofile, "R3")["comment"] == "bogus trip"      # but auditable with its note
