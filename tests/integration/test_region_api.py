"""End-to-end tests of the out-of-region day index + recover endpoint.

Build a real session (raw workbook + baseline proposals, the way `/upload` does),
then drive the Flask test client: `/api/<sid>/days` must mark the out-of-region
day as excluded, and `POST /api/<sid>/day_include` must recover it.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app import baseline, regions, server, session, state
from engine.classify import classify_all
from engine.loader import load_trips

DATE = "2024-11-05"
RAW_COLS = ["accessCode", "start_date", "start_time", "end_date", "end_time",
            "mode_of_travel", "purpose_of_travel", "start_latitude",
            "start_longitude", "end_latitude", "end_longitude", "routes"]


def _ts(hms: str) -> str:
    return f"{hms}-08:00"


def _route(slat, slon, elat, elon, s_hms, e_hms) -> str:
    return repr([[slat, slon, f"{DATE}T{_ts(s_hms)}"],
                 [elat, elon, f"{DATE}T{_ts(e_hms)}"]])


def trip(start, end, slat, slon, elat, elon, person):
    return {
        "accessCode": person, "start_date": DATE, "start_time": _ts(start),
        "end_date": DATE, "end_time": _ts(end), "mode_of_travel": "Auto Driver",
        "purpose_of_travel": "", "start_latitude": slat, "start_longitude": slon,
        "end_latitude": elat, "end_longitude": elon,
        "routes": _route(slat, slon, elat, elon, start, end),
    }


# P1 stays inside Vancouver; P2's only trip ends in Seattle -> out of region.
def rows():
    return [
        trip("09:00:00", "09:20:00", 49.260, -123.120, 49.278, -123.120, "P1"),
        trip("09:00:00", "09:40:00", 49.260, -123.120, 47.610, -122.340, "P2"),
    ]


P2_KEY = f"P2|{DATE}"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSIONS_DIR", str(tmp_path))
    monkeypatch.setattr(regions, "_PATH", str(tmp_path / "no-regions.json"))  # use defaults
    state.clear()
    sid = "s"
    session.write_proposals(sid, {"raw_name": "raw.xlsx"})  # creates the dir
    raw_path = f"{session.session_dir(sid)}/raw.xlsx"
    pd.DataFrame(rows(), columns=RAW_COLS).to_excel(raw_path, index=False)
    trips = load_trips(raw_path)
    data = baseline.build_baseline(trips, classify_all(trips), "raw.xlsx")
    session.write_proposals(sid, data)
    yield server.create_app().test_client(), sid
    state.clear()


def _day(payload, key):
    return next(d for d in payload["days"] if d["key"] == key)


def test_days_marks_out_of_region_day_excluded(client):
    c, sid = client
    payload = c.get(f"/api/{sid}/days").get_json()
    assert payload["n_excluded"] == 1
    p2 = _day(payload, P2_KEY)
    assert p2["out_of_region"] is True and p2["excluded"] is True
    p1 = _day(payload, f"P1|{DATE}")
    assert p1["out_of_region"] is False and p1["excluded"] is False


def test_day_include_recovers_the_day(client):
    c, sid = client
    c.post(f"/api/{sid}/day_include", json={"day_key": P2_KEY, "include": True})
    payload = c.get(f"/api/{sid}/days").get_json()
    assert payload["n_excluded"] == 0
    p2 = _day(payload, P2_KEY)
    assert p2["out_of_region"] is True and p2["excluded"] is False  # still flagged, but kept


def test_day_include_can_hand_remove_an_in_region_day(client):
    c, sid = client
    c.post(f"/api/{sid}/day_include", json={"day_key": f"P1|{DATE}", "include": False})
    payload = c.get(f"/api/{sid}/days").get_json()
    assert payload["n_excluded"] == 2  # the auto P2 + the hand-removed P1
    p1 = _day(payload, f"P1|{DATE}")
    assert p1["out_of_region"] is False and p1["excluded"] is True


def test_day_include_rejects_bad_body(client):
    c, sid = client
    assert c.post(f"/api/{sid}/day_include", json={"day_key": P2_KEY}).status_code == 400
    assert c.post(f"/api/{sid}/day_include",
                  json={"day_key": P2_KEY, "include": "yes"}).status_code == 400
