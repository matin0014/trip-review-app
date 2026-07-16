"""Integration tests for GET /api/<sid>/start_times and the export's chart PNGs:
the endpoint serves both variants of the raw-vs-live-cleaned distribution, and
generate_bundle ships the same data as two PNGs inside the export zip."""

from __future__ import annotations

import zipfile

import pandas as pd
import pytest

from app import baseline, generate, regions, routing, server, session, state
from engine.classify import classify_all
from engine.loader import load_trips

DATE = "2024-11-05"          # a Tuesday
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


def rows():
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


def test_start_times_shape_and_counts(client):
    c, sid = client
    d = c.get(f"/api/{sid}/start_times").get_json()
    for variant in ("all", "weekday"):
        v = d[variant]
        assert len(v["raw"]) == 24 and len(v["cleaned"]) == 24
    assert d["all"]["n_raw"] == 2
    assert d["all"]["raw"][9] == 1 and d["all"]["raw"][11] == 1
    assert d["weekday"]["n_raw"] == 2            # the Tuesday trips count as weekday
    assert d["all"]["raw"] == d["all"]["cleaned"]  # nothing cleaned yet


def test_start_times_reflect_a_manual_delete_live(client):
    c, sid = client
    c.post(f"/api/{sid}/manual_delete", json={"raw_row": 1, "day_key": DAY_KEY})
    d = c.get(f"/api/{sid}/start_times").get_json()
    assert d["all"]["n_raw"] == 2                 # raw is the untouched baseline
    assert d["all"]["n_cleaned"] == 1
    assert d["all"]["cleaned"][9] == 0            # the deleted 09:00 trip is gone


def test_start_times_unknown_session_404(client):
    c, _ = client
    assert c.get("/api/nope/start_times").status_code == 404


def test_export_zip_contains_the_two_chart_pngs(client):
    _, sid = client
    out = generate.generate_bundle(sid)
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert {"cleaned.xlsx", "dofile.xlsx",
                "start_times.png", "start_times_weekday.png"} <= names
        assert z.getinfo("start_times.png").file_size > 0
        assert z.getinfo("start_times_weekday.png").file_size > 0
