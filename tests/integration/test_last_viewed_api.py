"""Integration tests for "where the reviewer left off": opening a day via
GET /api/<sid>/live-day records it, and /api/<sid>/days surfaces it back so
the client can auto-resume there on the next launch."""

from __future__ import annotations

import pandas as pd
import pytest

from app import baseline, regions, routing, server, session, state
from engine.classify import classify_all
from engine.loader import load_trips

DATE = "2024-11-05"
PERSON = "P1"
DAY_KEY = f"{PERSON}|{DATE}"
OTHER_DATE = "2024-11-06"
OTHER_DAY_KEY = f"{PERSON}|{OTHER_DATE}"
RAW_COLS = ["accessCode", "start_date", "start_time", "end_date", "end_time",
            "mode_of_travel", "purpose_of_travel", "start_latitude",
            "start_longitude", "end_latitude", "end_longitude", "routes"]


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    monkeypatch.setattr(routing, "osrm_route", lambda *a, **k: [])
    monkeypatch.setattr(regions, "_PATH", str(tmp_path / "no-regions.json"))


def _ts(hms: str) -> str:
    return f"{hms}-08:00"


def rows():
    return [
        {"accessCode": PERSON, "start_date": DATE, "start_time": _ts("09:00:00"),
         "end_date": DATE, "end_time": _ts("09:20:00"), "mode_of_travel": "Walk",
         "purpose_of_travel": "", "start_latitude": 49.260, "start_longitude": -123.120,
         "end_latitude": 49.278, "end_longitude": -123.120, "routes": "[]"},
        {"accessCode": PERSON, "start_date": OTHER_DATE, "start_time": _ts("09:00:00"),
         "end_date": OTHER_DATE, "end_time": _ts("09:20:00"), "mode_of_travel": "Walk",
         "purpose_of_travel": "", "start_latitude": 49.260, "start_longitude": -123.120,
         "end_latitude": 49.278, "end_longitude": -123.120, "routes": "[]"},
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


def test_days_last_viewed_is_none_before_any_day_is_opened(client):
    c, sid = client
    d = c.get(f"/api/{sid}/days").get_json()
    assert d["last_viewed"] is None


def test_opening_a_day_records_it_as_last_viewed(client):
    c, sid = client
    c.get(f"/api/{sid}/live-day", query_string={"key": DAY_KEY})
    d = c.get(f"/api/{sid}/days").get_json()
    assert d["last_viewed"]["day_key"] == DAY_KEY


def test_opening_a_later_day_overwrites_the_last_viewed_one(client):
    c, sid = client
    c.get(f"/api/{sid}/live-day", query_string={"key": DAY_KEY})
    c.get(f"/api/{sid}/live-day", query_string={"key": OTHER_DAY_KEY})
    d = c.get(f"/api/{sid}/days").get_json()
    assert d["last_viewed"]["day_key"] == OTHER_DAY_KEY


def test_sessions_list_surfaces_last_viewed_too(client):
    c, sid = client
    c.get(f"/api/{sid}/live-day", query_string={"key": DAY_KEY})
    listed = c.get("/api/sessions").get_json()
    row = next(s for s in listed if s["sid"] == sid)
    assert row["last_viewed"]["day_key"] == DAY_KEY
