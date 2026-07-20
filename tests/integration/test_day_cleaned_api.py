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
# and "the person's only day" cleaned-toggle behavior. P3's day has a genuine
# fragment flag (its two legs are <=200m / <3min apart), for the pending-flag
# gate: an untouched gap that small is neither long enough nor far enough to
# read as a real activity, so classify_day surfaces it as a likely one-drive-
# split-in-two rather than silently treating it as a clean, resolved day.
def rows():
    return [
        trip(DATE, "09:00:00", "09:20:00", 49.260, -123.120, 49.278, -123.120, "P1"),
        trip(OTHER_DATE, "09:00:00", "09:20:00", 49.260, -123.120, 49.278, -123.120, "P1"),
        trip(DATE, "09:00:00", "09:20:00", 49.260, -123.120, 49.278, -123.120, "P2"),
        trip(DATE, "09:00:00", "09:10:00", 49.260, -123.120, 49.270, -123.120, "P3"),
        trip(DATE, "09:11:00", "09:20:00", 49.270, -123.120, 49.290, -123.120, "P3"),
    ]


P1_DAY1 = f"P1|{DATE}"
P1_DAY2 = f"P1|{OTHER_DATE}"
P2_DAY1 = f"P2|{DATE}"
P3_DAY1 = f"P3|{DATE}"


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


# --- gate: flags must be resolved before a day can be checked off -----------

def test_checking_off_a_day_with_a_pending_flag_is_refused(client):
    c, sid = client
    r = c.post(f"/api/{sid}/day_cleaned", json={"day_key": P3_DAY1, "cleaned": True})
    assert r.status_code == 400
    payload = c.get(f"/api/{sid}/days").get_json()
    assert _day(payload, P3_DAY1)["cleaned"] is False   # refused, not silently recorded


def test_unchecking_is_never_blocked_by_pending_flags(client):
    c, sid = client
    r = c.post(f"/api/{sid}/day_cleaned", json={"day_key": P3_DAY1, "cleaned": False})
    assert r.status_code == 200


def test_checking_off_succeeds_once_the_flag_is_resolved(client):
    c, sid = client
    flag_id = next(f["id"] for f in c.get(f"/api/{sid}/live-day",
                                          query_string={"key": P3_DAY1}).get_json()["flags"])
    c.post(f"/api/{sid}/decision", json={"flag_id": flag_id, "resolution": "waive"})
    r = c.post(f"/api/{sid}/day_cleaned", json={"day_key": P3_DAY1, "cleaned": True})
    assert r.status_code == 200
    payload = c.get(f"/api/{sid}/days").get_json()
    assert _day(payload, P3_DAY1)["cleaned"] is True
