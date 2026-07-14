"""Integration tests for the manual-add time-conflict check: POST
/api/<sid>/manual_add must reject a new trip that overlaps an existing one on
the same person-day, via the real Flask endpoint (not the engine directly)."""

from __future__ import annotations

import pandas as pd
import pytest

from app import baseline, routing, server, session, state
from engine.classify import classify_all
from engine.loader import load_trips

DATE = "2024-11-05"
DAY_KEY = f"P1|{DATE}"
RAW_COLS = ["accessCode", "start_date", "start_time", "end_date", "end_time",
            "mode_of_travel", "purpose_of_travel", "start_latitude",
            "start_longitude", "end_latitude", "end_longitude", "routes"]


def _ts(hms: str) -> str:
    return f"{hms}-08:00"


def _route(slat, slon, elat, elon, s_hms, e_hms) -> str:
    return repr([[slat, slon, f"{DATE}T{_ts(s_hms)}"], [elat, elon, f"{DATE}T{_ts(e_hms)}"]])


def trip(start, end, slat=49.26, slon=-123.12, elat=49.28, elon=-123.12):
    return {
        "accessCode": "P1", "start_date": DATE, "start_time": _ts(start),
        "end_date": DATE, "end_time": _ts(end), "mode_of_travel": "Auto Driver",
        "purpose_of_travel": "", "start_latitude": slat, "start_longitude": slon,
        "end_latitude": elat, "end_longitude": elon,
        "routes": _route(slat, slon, elat, elon, start, end),
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSIONS_DIR", str(tmp_path))
    monkeypatch.setattr(routing, "osrm_route", lambda *a, **k: [])  # never hit the network
    state.clear()
    sid = "s"
    session.write_proposals(sid, {"raw_name": "raw.xlsx"})
    raw_path = f"{session.session_dir(sid)}/raw.xlsx"
    rows = [trip("09:00:00", "09:20:00"), trip("10:00:00", "10:20:00")]
    pd.DataFrame(rows, columns=RAW_COLS).to_excel(raw_path, index=False)
    trips = load_trips(raw_path)
    session.write_proposals(sid, baseline.build_baseline(trips, classify_all(trips), "raw.xlsx"))
    yield server.create_app().test_client(), sid
    state.clear()


def _add(c, sid, start, end):
    return c.post(f"/api/{sid}/manual_add", json={
        "day_key": DAY_KEY, "start_time": start, "end_time": end,
        "mode": "Walk", "purpose": "",
        "start_lat": 49.27, "start_lon": -123.12, "end_lat": 49.29, "end_lon": -123.11})


def test_overlapping_add_is_rejected(client):
    c, sid = client
    r = _add(c, sid, "09:10:00", "09:30:00")  # overlaps the 09:00-09:20 trip
    assert r.status_code == 400
    assert "09:00-09:20" in r.get_data(as_text=True)


def test_add_fully_inside_an_existing_trip_is_rejected(client):
    c, sid = client
    r = _add(c, sid, "10:05:00", "10:10:00")  # fully inside 10:00-10:20
    assert r.status_code == 400


def test_add_spanning_an_existing_trip_is_rejected(client):
    c, sid = client
    r = _add(c, sid, "08:50:00", "09:30:00")  # engulfs 09:00-09:20
    assert r.status_code == 400


def test_back_to_back_add_is_allowed(client):
    c, sid = client
    # starts exactly when the 09:00-09:20 trip ends -- touching, not overlapping
    r = _add(c, sid, "09:20:00", "09:50:00")
    assert r.status_code == 200


def test_add_in_a_free_gap_is_allowed(client):
    c, sid = client
    r = _add(c, sid, "12:00:00", "12:15:00")  # well clear of both existing trips
    assert r.status_code == 200
