"""Integration tests for the no-purpose tools: POST /api/<sid>/remove_no_purpose
deletes every blank-purpose trip on a day (each individually undoable)."""

from __future__ import annotations

import pandas as pd
import pytest

from app import baseline, regions, routing, server, session, state
from engine.classify import classify_all
from engine.loader import load_trips

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


def _route(slat, slon, elat, elon, s, e) -> str:
    return repr([[slat, slon, f"{DATE}T{_ts(s)}"], [elat, elon, f"{DATE}T{_ts(e)}"]])


def trip(s, e, slon, elon, purpose):
    return {"accessCode": PERSON, "start_date": DATE, "start_time": _ts(s),
            "end_date": DATE, "end_time": _ts(e), "mode_of_travel": "Walk",
            "purpose_of_travel": purpose, "start_latitude": 49.26, "start_longitude": slon,
            "end_latitude": 49.26, "end_longitude": elon,
            "routes": _route(49.26, slon, 49.26, elon, s, e)}


# one real trip + two no-purpose trips, well separated so no flags fire
def rows():
    return [
        trip("09:00:00", "09:20:00", -123.120, -123.100, "Work"),
        trip("10:00:00", "10:20:00", -123.100, -123.080, ""),
        trip("11:00:00", "11:20:00", -123.080, -123.060, ""),
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


def _purposes(c, sid):
    d = c.get(f"/api/{sid}/live-day", query_string={"key": DAY_KEY}).get_json()
    return [t["purpose"] for t in d["trips"]]


def test_remove_no_purpose_drops_only_blank_purpose_trips(client):
    c, sid = client
    r = c.post(f"/api/{sid}/remove_no_purpose", json={"day_key": DAY_KEY})
    assert r.get_json()["removed"] == 2
    assert _purposes(c, sid) == ["Work"]        # the one real trip survives


def test_each_removal_is_individually_undoable(client):
    c, sid = client
    c.post(f"/api/{sid}/remove_no_purpose", json={"day_key": DAY_KEY})
    c.post(f"/api/{sid}/undo")                   # one revert brings back one trip
    assert len(_purposes(c, sid)) == 2


def test_giving_a_purpose_first_spares_that_trip(client):
    c, sid = client
    # relabel trip 3 (raw_row 3) -> it is no longer "no purpose"
    c.post(f"/api/{sid}/manual_purpose", json={"raw_row": 3, "purpose": "Shopping", "day_key": DAY_KEY})
    r = c.post(f"/api/{sid}/remove_no_purpose", json={"day_key": DAY_KEY})
    assert r.get_json()["removed"] == 1         # only trip 2 (still blank) goes
    assert sorted(_purposes(c, sid)) == ["Shopping", "Work"]


def test_remove_no_purpose_when_none_exist_is_a_noop(client):
    c, sid = client
    c.post(f"/api/{sid}/remove_no_purpose", json={"day_key": DAY_KEY})   # clear the two
    r = c.post(f"/api/{sid}/remove_no_purpose", json={"day_key": DAY_KEY})
    assert r.get_json()["removed"] == 0


def test_remove_no_purpose_bad_body_and_unknown_session(client):
    c, sid = client
    assert c.post(f"/api/{sid}/remove_no_purpose", json={}).status_code == 400
    assert c.post("/api/nope/remove_no_purpose", json={"day_key": DAY_KEY}).status_code == 404
