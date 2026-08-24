"""Integration tests for the distributions ZIP: the pure builder functions
(session-live-state and arbitrary-DataFrame cases) plus the three Flask
endpoints that expose them. Mirrors test_start_times_api.py's style -- plain
function calls where possible, the Flask test client for the endpoints, no
coverage of launch.py's Api (untested pywebview glue, same as save_export)."""

from __future__ import annotations

import io

import pandas as pd
import pytest

from app import baseline, distributions, generate, regions, routing, server, session, state
from engine.classify import classify_all
from engine.loader import load_trips

DATE = "2024-11-05"          # a Tuesday
PERSON = "P1"
RAW_COLS = ["accessCode", "start_date", "start_time", "end_date", "end_time",
            "mode_of_travel", "purpose_of_travel", "start_latitude",
            "start_longitude", "end_latitude", "end_longitude", "routes"]
CLEANED_COLS = ["trip_id", "accessCode", "start_date", "start_time", "end_date",
                "end_time", "mode_of_travel", "purpose_of_travel"]


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    monkeypatch.setattr(routing, "osrm_route", lambda *a, **k: [])
    monkeypatch.setattr(regions, "_PATH", str(tmp_path / "no-regions.json"))


def _ts(hms: str) -> str:
    return f"{hms}-08:00"


def raw_rows():
    return [
        {"accessCode": PERSON, "start_date": DATE, "start_time": _ts("09:00:00"),
         "end_date": DATE, "end_time": _ts("09:20:00"), "mode_of_travel": "Walk",
         "purpose_of_travel": "", "start_latitude": 49.260, "start_longitude": -123.120,
         "end_latitude": 49.278, "end_longitude": -123.120, "routes": "[]"},
        {"accessCode": PERSON, "start_date": DATE, "start_time": _ts("11:00:00"),
         "end_date": DATE, "end_time": _ts("11:20:00"), "mode_of_travel": "Bus",
         "purpose_of_travel": "", "start_latitude": 49.278, "start_longitude": -123.120,
         "end_latitude": 49.296, "end_longitude": -123.120, "routes": "[]"},
    ]


def cleaned_rows():
    return [
        {"trip_id": "1", "accessCode": PERSON, "start_date": DATE,
         "start_time": "09:00:00-08:00", "end_date": DATE, "end_time": "09:20:00-08:00",
         "mode_of_travel": "Walk", "purpose_of_travel": "Work"},
        {"trip_id": "2", "accessCode": PERSON, "start_date": DATE,
         "start_time": "11:00:00-08:00", "end_date": DATE, "end_time": "11:20:00-08:00",
         "mode_of_travel": "Transit Bus", "purpose_of_travel": "Shopping"},
    ]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSIONS_DIR", str(tmp_path))
    state.clear()
    sid = "s"
    session.write_proposals(sid, {"raw_name": "raw.xlsx"})
    raw_path = f"{session.session_dir(sid)}/raw.xlsx"
    pd.DataFrame(raw_rows(), columns=RAW_COLS).to_excel(raw_path, index=False)
    trips = load_trips(raw_path)
    session.write_proposals(sid, baseline.build_baseline(trips, classify_all(trips), "raw.xlsx"))
    yield server.create_app().test_client(), sid
    state.clear()


# every chart the builder is expected to write, so a chart silently dropping
# out of the set fails here rather than shipping a short archive
EXPECTED_PNGS = {
    "start_times.png", "start_times_weekday.png", "start_times_by_weekday.png",
    "activity_duration.png", "activity_by_purpose_episode.png",
    "activity_by_purpose.png",
    "days_per_participant.png", "modes.png",
}


def _assert_zip(data: bytes, min_size: int = 5000) -> None:
    import zipfile

    assert data[:2] == b"PK"
    assert len(data) > min_size
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        assert names == EXPECTED_PNGS, names
        # a zero-byte member would still satisfy a name check
        for info in zf.infolist():
            assert info.file_size > 1000, info.filename


def test_build_distributions_zip_from_dataframe(tmp_path):
    df = pd.DataFrame(cleaned_rows(), columns=CLEANED_COLS)
    out = str(tmp_path / "dist.zip")
    result = distributions.build_distributions_zip(df, out)
    assert result == out
    _assert_zip(open(out, "rb").read())


def test_build_distributions_zip_missing_columns(tmp_path):
    df = pd.DataFrame(cleaned_rows(), columns=CLEANED_COLS).drop(columns=["trip_id"])
    with pytest.raises(ValueError, match="missing columns"):
        distributions.build_distributions_zip(df, str(tmp_path / "dist.zip"))


def test_build_distributions_zip_for_session(client, tmp_path):
    _, sid = client
    out = str(tmp_path / "dist.zip")
    result = distributions.build_distributions_zip_for_session(sid, out)
    assert result == out
    _assert_zip(open(out, "rb").read())


def test_api_distributions_endpoint(client):
    c, sid = client
    r = c.get(f"/api/{sid}/distributions")
    assert r.status_code == 200
    assert r.content_type.startswith("application/zip")
    _assert_zip(r.data)


def test_api_distributions_unknown_session_404(client):
    c, _ = client
    assert c.get("/api/nope/distributions").status_code == 404


def _upload_bytes(rows, columns=CLEANED_COLS) -> bytes:
    buf = io.BytesIO()
    pd.DataFrame(rows, columns=columns).to_excel(buf, index=False)
    buf.seek(0)
    return buf.read()


def test_api_distributions_from_file_endpoint(client):
    c, _ = client
    data = {"file": (io.BytesIO(_upload_bytes(cleaned_rows())), "cleaned.xlsx")}
    r = c.post("/api/distributions/from_file", data=data,
              content_type="multipart/form-data")
    assert r.status_code == 200
    assert r.content_type.startswith("application/zip")
    _assert_zip(r.data)


def test_api_distributions_from_file_build_endpoint(client):
    c, _ = client
    data = {"file": (io.BytesIO(_upload_bytes(cleaned_rows())), "cleaned.xlsx")}
    r = c.post("/api/distributions/from_file/build", data=data,
              content_type="multipart/form-data")
    assert r.status_code == 200
    path = r.get_json()["path"]
    import os
    assert os.path.isfile(path)
    _assert_zip(open(path, "rb").read())


def test_api_distributions_from_file_bad_columns(client):
    c, _ = client
    bad_rows = [{k: v for k, v in row.items() if k != "trip_id"} for row in cleaned_rows()]
    bad_cols = [c for c in CLEANED_COLS if c != "trip_id"]
    data = {"file": (io.BytesIO(_upload_bytes(bad_rows, columns=bad_cols)), "bad.xlsx")}
    r = c.post("/api/distributions/from_file", data=data,
              content_type="multipart/form-data")
    assert r.status_code == 400
