"""Integration tests for the reviewer's manual "cleaned" checkbox: POST
/api/<sid>/day_cleaned toggles it, and GET /api/<sid>/days surfaces the
per-day flag back (the person-level "all done" checkmark is computed
client-side from these per-day flags, so it isn't covered here)."""

from __future__ import annotations

import pandas as pd
import pytest

from app import baseline, regions, server, session, state
from engine.classify import classify_all
from engine.loader import load_trips

DATE = "2024-11-05"
OTHER_DATE = "2024-11-06"
RAW_COLS = ["accessCode", "start_date", "start_time", "end_date", "end_time",
            "mode_of_travel", "purpose_of_travel", "start_latitude",
            "start_longitude", "end_latitude", "end_longitude", "routes"]


def _ts(hms: str) -> str:
    return f"{hms}-08:00"


def _route(slat, slon, elat, elon, s_hms, e_hms) -> str:
    return repr([[slat, slon, f"{DATE}T{_ts(s_hms)}"],
                 [elat, elon, f"{DATE}T{_ts(e_hms)}"]])


def trip(date, start, end, slat, slon, elat, elon, person):
    return {
        "accessCode": person, "start_date": date, "start_time": _ts(start),
        "end_date": date, "end_time": _ts(end), "mode_of_travel": "Auto Driver",
        "purpose_of_travel": "", "start_latitude": slat, "start_longitude": slon,
        "end_latitude": elat, "end_longitude": elon,
        "routes": _route(slat, slon, elat, elon, start, end),
    }


# P1 has two days; P2 has one -- lets tests check both "one of several days"
# and "the person's only day" cleaned-toggle behavior.
def rows():
    return [
        trip(DATE, "09:00:00", "09:20:00", 49.260, -123.120, 49.278, -123.120, "P1"),
        trip(OTHER_DATE, "09:00:00", "09:20:00", 49.260, -123.120, 49.278, -123.120, "P1"),
        trip(DATE, "09:00:00", "09:20:00", 49.260, -123.120, 49.278, -123.120, "P2"),
    ]


P1_DAY1 = f"P1|{DATE}"
P1_DAY2 = f"P1|{OTHER_DATE}"
P2_DAY1 = f"P2|{DATE}"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSIONS_DIR", str(tmp_path))
    monkeypatch.setattr(regions, "_PATH", str(tmp_path / "no-regions.json"))
    state.clear()
    sid = "s"
    session.write_proposals(sid, {"raw_name": "raw.xlsx"})
    raw_path = f"{session.session_dir(sid)}/raw.xlsx"
    pd.DataFrame(rows(), columns=RAW_COLS).to_excel(raw_path, index=False)
    trips = load_trips(raw_path)
    session.write_proposals(sid, baseline.build_baseline(trips, classify_all(trips), "raw.xlsx"))
    yield server.create_app().test_client(), sid
    state.clear()


def _day(payload, key):
    return next(d for d in payload["days"] if d["key"] == key)


def test_days_default_to_not_cleaned(client):
    c, sid = client
    payload = c.get(f"/api/{sid}/days").get_json()
    assert all(d["cleaned"] is False for d in payload["days"])


def test_checking_a_day_marks_only_that_day_cleaned(client):
    c, sid = client
    c.post(f"/api/{sid}/day_cleaned", json={"day_key": P1_DAY1, "cleaned": True})
    payload = c.get(f"/api/{sid}/days").get_json()
    assert _day(payload, P1_DAY1)["cleaned"] is True
    assert _day(payload, P1_DAY2)["cleaned"] is False   # the person's OTHER day untouched
    assert _day(payload, P2_DAY1)["cleaned"] is False


def test_unchecking_reverts_it(client):
    c, sid = client
    c.post(f"/api/{sid}/day_cleaned", json={"day_key": P2_DAY1, "cleaned": True})
    c.post(f"/api/{sid}/day_cleaned", json={"day_key": P2_DAY1, "cleaned": False})
    payload = c.get(f"/api/{sid}/days").get_json()
    assert _day(payload, P2_DAY1)["cleaned"] is False


def test_day_cleaned_rejects_bad_body(client):
    c, sid = client
    assert c.post(f"/api/{sid}/day_cleaned", json={"day_key": P1_DAY1}).status_code == 400
    assert c.post(f"/api/{sid}/day_cleaned",
                  json={"day_key": P1_DAY1, "cleaned": "yes"}).status_code == 400


def test_day_cleaned_unknown_session_404(client):
    c, _ = client
    r = c.post("/api/nope/day_cleaned", json={"day_key": P1_DAY1, "cleaned": True})
    assert r.status_code == 404
