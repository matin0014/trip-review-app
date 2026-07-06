"""End-to-end tests of the resume flow: upload dedupe by content hash + the
duplicate popup contract, and the /api/sessions listing the landing page reads.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest

from app import server, session

DATE = "2024-11-05"
RAW_COLS = ["accessCode", "start_date", "start_time", "end_date", "end_time",
            "mode_of_travel", "purpose_of_travel", "start_latitude",
            "start_longitude", "end_latitude", "end_longitude", "routes"]


def _ts(hms): return f"{hms}-08:00"


def _route(slat, slon, elat, elon, s, e):
    return repr([[slat, slon, f"{DATE}T{_ts(s)}"], [elat, elon, f"{DATE}T{_ts(e)}"]])


def trip(s, e, slat, slon, elat, elon):
    return {"accessCode": "P1", "start_date": DATE, "start_time": _ts(s),
            "end_date": DATE, "end_time": _ts(e), "mode_of_travel": "Auto Driver",
            "purpose_of_travel": "", "start_latitude": slat, "start_longitude": slon,
            "end_latitude": elat, "end_longitude": elon,
            "routes": _route(slat, slon, elat, elon, s, e)}


def workbook_bytes():
    rows = [trip("09:00:00", "09:20:00", 49.260, -123.120, 49.278, -123.120),
            trip("10:00:00", "10:20:00", 49.278, -123.120, 49.296, -123.120)]
    buf = io.BytesIO()
    pd.DataFrame(rows, columns=RAW_COLS).to_excel(buf, index=False)
    return buf.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSIONS_DIR", str(tmp_path))
    return server.create_app().test_client()


def _upload(client, data, force_new=False):
    payload = {"file": (io.BytesIO(data), "raw.xlsx")}
    if force_new:
        payload["force_new"] = "1"
    return client.post("/upload", data=payload, content_type="multipart/form-data")


def test_first_upload_creates_a_session(client):
    data = workbook_bytes()
    body = _upload(client, data).get_json()
    assert "sid" in body and body["redirect"].endswith(body["sid"])
    assert session.exists(body["sid"])


def test_reupload_same_file_offers_resume_not_a_new_session(client):
    data = workbook_bytes()
    first = _upload(client, data).get_json()["sid"]
    dup = _upload(client, data).get_json()
    assert dup["duplicate"] is True and dup["existing"] == first
    assert len(session.list_sessions()) == 1            # no duplicate session forked


def test_force_new_forks_a_second_session(client):
    data = workbook_bytes()
    first = _upload(client, data).get_json()["sid"]
    second = _upload(client, data, force_new=True).get_json()["sid"]
    assert second != first
    assert {s["sid"] for s in session.list_sessions()} == {first, second}


def test_different_file_does_not_collide(client):
    a = _upload(client, workbook_bytes()).get_json()
    other = workbook_bytes()[:-1] + b"x"                # perturb the bytes -> different hash
    b = _upload(client, other).get_json()
    assert "sid" in a and "sid" in b and "duplicate" not in b


def test_api_sessions_lists_uploaded_sessions(client):
    _upload(client, workbook_bytes())
    listed = client.get("/api/sessions").get_json()
    assert len(listed) == 1
    assert listed[0]["raw_name"] == "raw.xlsx" and listed[0]["counts"]["person_days"] == 1


def test_delete_session_removes_it_from_the_list(client):
    sid = _upload(client, workbook_bytes()).get_json()["sid"]
    assert client.post(f"/api/{sid}/delete").get_json() == {"deleted": sid}
    assert not session.exists(sid)
    assert client.get("/api/sessions").get_json() == []


def test_delete_unknown_session_is_404(client):
    assert client.post("/api/nope/delete").status_code == 404
