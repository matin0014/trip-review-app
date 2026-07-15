"""Integration tests for the day's Edited list and POST /api/<sid>/undo_event:
the live-day API surfaces every manual op with its log serial number, and
undo_event removes ONE targeted edit while leaving later edits in effect --
verified through the real Flask endpoints, not the engine directly."""

from __future__ import annotations

import pandas as pd
import pytest

from app import baseline, routing, server, session, state
from engine.classify import classify_all
from engine.loader import load_trips

DATE = "2024-11-05"
PERSON = "P1"
DAY_KEY = f"{PERSON}|{DATE}"
RAW_COLS = ["accessCode", "start_date", "start_time", "end_date", "end_time",
            "mode_of_travel", "purpose_of_travel", "start_latitude",
            "start_longitude", "end_latitude", "end_longitude", "routes"]


def _ts(hms: str) -> str:
    return f"{hms}-08:00"


def rows():
    return [
        {"accessCode": PERSON, "start_date": DATE, "start_time": _ts("09:00:00"),
         "end_date": DATE, "end_time": _ts("09:20:00"), "mode_of_travel": "Walk",
         "purpose_of_travel": "", "start_latitude": 49.260, "start_longitude": -123.120,
         "end_latitude": 49.270, "end_longitude": -123.120, "routes": "[]"},
        {"accessCode": PERSON, "start_date": DATE, "start_time": _ts("10:00:00"),
         "end_date": DATE, "end_time": _ts("10:20:00"), "mode_of_travel": "Bus",
         "purpose_of_travel": "", "start_latitude": 49.280, "start_longitude": -123.120,
         "end_latitude": 49.290, "end_longitude": -123.120, "routes": "[]"},
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


def _live_day(c, sid):
    return c.get(f"/api/{sid}/live-day", query_string={"key": DAY_KEY}).get_json()


def test_live_day_lists_manual_edits_with_seq_and_label(client):
    c, sid = client
    c.post(f"/api/{sid}/manual_mode", json={"raw_row": 1, "mode": "Bike", "day_key": DAY_KEY})
    c.post(f"/api/{sid}/manual_delete", json={"raw_row": 2, "day_key": DAY_KEY})

    edits = _live_day(c, sid)["edits"]
    assert [e["type"] for e in edits] == ["manual_mode", "manual_delete"]
    assert edits[0]["seq"] == 1 and edits[0]["label"] == "Changed row 1 mode to Bike"
    assert edits[1]["seq"] == 2 and edits[1]["label"] == "Deleted trip row 2"


def test_undo_event_removes_one_targeted_edit_only(client):
    c, sid = client
    c.post(f"/api/{sid}/manual_mode", json={"raw_row": 1, "mode": "Bike", "day_key": DAY_KEY})
    c.post(f"/api/{sid}/manual_delete", json={"raw_row": 2, "day_key": DAY_KEY})

    r = c.post(f"/api/{sid}/undo_event", json={"seq": 1})   # undo the OLDER edit
    assert r.status_code == 200

    d = _live_day(c, sid)
    assert [e["type"] for e in d["edits"]] == ["manual_delete"]   # only the newer one remains
    ids = {t["id"] for t in d["trips"]}
    assert 2 not in ids   # the delete is still in effect
    row1 = next(t for t in d["trips"] if t["id"] == 1)
    assert row1["mode"] == "Walk"   # the mode change was undone


def test_undo_event_missing_seq_is_400(client):
    c, sid = client
    r = c.post(f"/api/{sid}/undo_event", json={})
    assert r.status_code == 400


def test_reposition_shows_in_edits_and_both_undo_paths_remove_it(client, monkeypatch):
    c, sid = client
    monkeypatch.setattr(routing, "osrm_route", lambda *a, **k: [])

    r = c.post(f"/api/{sid}/manual_reposition", json={
        "raw_row": 1, "day_key": DAY_KEY, "end": "start", "lat": 49.250, "lon": -123.130})
    assert r.status_code == 200

    d = _live_day(c, sid)
    assert [e["type"] for e in d["edits"]] == ["manual_reposition"]
    assert d["edits"][0]["label"] == "Moved row 1's start point"
    row1 = next(t for t in d["trips"] if t["id"] == 1)
    assert row1["s"] == [49.25, -123.13]

    # the plain toolbar Undo (LIFO) still pops it
    c.post(f"/api/{sid}/undo")
    d = _live_day(c, sid)
    assert d["edits"] == []
    row1 = next(t for t in d["trips"] if t["id"] == 1)
    assert row1["s"] == [49.26, -123.12]   # back to the original start point

    # redo it, then remove it via the targeted undo_event instead
    c.post(f"/api/{sid}/redo")
    seq = _live_day(c, sid)["edits"][0]["seq"]
    c.post(f"/api/{sid}/undo_event", json={"seq": seq})
    d = _live_day(c, sid)
    assert d["edits"] == []
    row1 = next(t for t in d["trips"] if t["id"] == 1)
    assert row1["s"] == [49.26, -123.12]
