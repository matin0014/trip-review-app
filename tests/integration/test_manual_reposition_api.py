"""Integration tests for POST /api/<sid>/manual_reposition: moving a trip's
start or end point through the real Flask endpoint, verified via the real
live-day API (not the engine directly) -- confirms the whole path end to end:
kept breadcrumbs preserved, a fresh OSRM patch fills the gap, only the
targeted endpoint's coordinates change."""

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


def _route_cell(points) -> str:
    """points = [(lat, lon, "HH:MM:SS"), ...]."""
    return repr([[lat, lon, f"{DATE}T{_ts(hms)}"] for lat, lon, hms in points])


# One trip, raw_row 1, with a 3-point real route spanning its declared window.
ROUTE_PTS = [(49.260, -123.120, "09:00:00"),
             (49.265, -123.120, "09:10:00"),
             (49.270, -123.120, "09:20:00")]


def rows():
    return [{
        "accessCode": PERSON, "start_date": DATE, "start_time": _ts("09:00:00"),
        "end_date": DATE, "end_time": _ts("09:20:00"), "mode_of_travel": "Walk",
        "purpose_of_travel": "", "start_latitude": 49.260, "start_longitude": -123.120,
        "end_latitude": 49.270, "end_longitude": -123.120,
        "routes": _route_cell(ROUTE_PTS),
    }]


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


def _live_trip(c, sid, raw_row=1):
    payload = c.get(f"/api/{sid}/live-day", query_string={"key": DAY_KEY}).get_json()
    return next(t for t in payload["trips"] if t["id"] == raw_row)


def test_reposition_end_keeps_breadcrumbs_and_patches_the_gap(client, monkeypatch):
    c, sid = client
    monkeypatch.setattr(routing, "osrm_route", lambda *a, **k: [[49.270, -123.120], [49.280, -123.110]])
    r = c.post(f"/api/{sid}/manual_reposition", json={
        "raw_row": 1, "day_key": DAY_KEY, "end": "end", "lat": 49.280, "lon": -123.110})
    assert r.status_code == 200

    t = _live_trip(c, sid)
    assert t["e"] == [49.28, -123.11]           # declared end point moved
    assert t["s"] == [49.26, -123.12]            # start untouched
    # 3 kept breadcrumbs + 1 new patch point (the 2-coord patch's leading point
    # duplicates the last kept breadcrumb and is dropped) = 4
    assert t["npts"] == 4
    assert t["route"][-1] == [49.28, -123.11]    # route now reaches the new point
    assert t["route"][0] == [49.26, -123.12]     # original start breadcrumb intact


def test_reposition_start_keeps_breadcrumbs_and_patches_the_gap(client, monkeypatch):
    c, sid = client
    monkeypatch.setattr(routing, "osrm_route", lambda *a, **k: [[49.250, -123.130], [49.260, -123.120]])
    r = c.post(f"/api/{sid}/manual_reposition", json={
        "raw_row": 1, "day_key": DAY_KEY, "end": "start", "lat": 49.250, "lon": -123.130})
    assert r.status_code == 200

    t = _live_trip(c, sid)
    assert t["s"] == [49.25, -123.13]
    assert t["e"] == [49.27, -123.12]             # end untouched
    assert t["npts"] == 4
    assert t["route"][0] == [49.25, -123.13]
    assert t["route"][-1] == [49.27, -123.12]     # original end breadcrumb intact


def test_reposition_degrades_gracefully_when_osrm_fails(client, monkeypatch):
    c, sid = client
    monkeypatch.setattr(routing, "osrm_route", lambda *a, **k: [])  # network down
    r = c.post(f"/api/{sid}/manual_reposition", json={
        "raw_row": 1, "day_key": DAY_KEY, "end": "end", "lat": 49.300, "lon": -123.100})
    assert r.status_code == 200
    t = _live_trip(c, sid)
    assert t["e"] == [49.3, -123.1]   # endpoint still moves
    assert t["npts"] == 3             # but no patch breadcrumbs added (OSRM failed)


def test_reposition_unknown_trip_is_404(client, monkeypatch):
    c, sid = client
    monkeypatch.setattr(routing, "osrm_route", lambda *a, **k: [])
    r = c.post(f"/api/{sid}/manual_reposition", json={
        "raw_row": 999, "day_key": DAY_KEY, "end": "end", "lat": 49.3, "lon": -123.1})
    assert r.status_code == 404


def test_reposition_missing_fields_is_400(client):
    c, sid = client
    r = c.post(f"/api/{sid}/manual_reposition", json={"raw_row": 1, "day_key": DAY_KEY})
    assert r.status_code == 400
