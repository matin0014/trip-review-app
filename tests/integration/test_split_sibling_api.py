"""Integration tests for `splitSibling` in the live-day API: a split trip's two
halves must each point at the other, so Merge mode can select both together
instead of a reviewer silently stranding one half outside a merge.

Regression: `manual_merge` referencing a row that had been split only pulled in
whichever piece still carried that literal raw_row (the split's B-piece gets a
DIFFERENT raw_row), leaving the other half unmerged with no indication in the UI
that there even were two separately-selectable pieces to begin with. The engine
merges correctly when given both raw_rows; the fix is telling the client which
rows are split siblings of each other.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app import baseline, regions, routing, server, session, state
from engine.classify import classify_all
from engine.loader import load_trips
from engine.proposals import split_b_row

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


def trip(s, e, slon, elon, purpose="Work"):
    return {"accessCode": PERSON, "start_date": DATE, "start_time": _ts(s),
            "end_date": DATE, "end_time": _ts(e), "mode_of_travel": "Walk",
            "purpose_of_travel": purpose, "start_latitude": 49.26, "start_longitude": slon,
            "end_latitude": 49.26, "end_longitude": elon,
            "routes": _route(49.26, slon, 49.26, elon, s, e)}


# a neighbour trip (A) + a trip we'll split (B), well separated so no flags fire
def rows():
    return [
        trip("09:00:00", "09:20:00", -123.140, -123.120),   # A: raw_row 1
        trip("10:00:00", "10:20:00", -123.100, -123.080),   # B: raw_row 2, will be split
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


def _live(c, sid):
    return c.get(f"/api/{sid}/live-day", query_string={"key": DAY_KEY}).get_json()["trips"]


def _by_id(trips, tid):
    return next(t for t in trips if t["id"] == tid)


def test_unsplit_trips_have_no_sibling(client):
    c, sid = client
    trips = _live(c, sid)
    assert all(t["splitSibling"] is None for t in trips)


def test_split_halves_point_at_each_other(client):
    c, sid = client
    c.post(f"/api/{sid}/manual_split", json={"raw_row": 2, "day_key": DAY_KEY,
                                              "split_time": "10:10", "mode_a": "Walk", "mode_b": "Walk"})
    trips = _live(c, sid)
    b_row = split_b_row(2)
    a_half = _by_id(trips, 2)
    b_half = _by_id(trips, b_row)
    assert a_half["splitSibling"] == b_row
    assert b_half["splitSibling"] == 2
    assert _by_id(trips, 1)["splitSibling"] is None   # the untouched neighbour


def test_merging_both_split_halves_with_the_neighbour_covers_the_full_span(client):
    # This is the actual bug scenario: split B, then manually merge A with BOTH
    # of B's halves (as the UI now does automatically when either is selected).
    # The engine already handles this correctly given the right raw_rows -- this
    # pins that the full merge (not just one half) produces the complete span.
    c, sid = client
    c.post(f"/api/{sid}/manual_split", json={"raw_row": 2, "day_key": DAY_KEY,
                                              "split_time": "10:10", "mode_a": "Walk", "mode_b": "Walk"})
    b_row = split_b_row(2)
    c.post(f"/api/{sid}/manual_merge", json={"raw_rows": [1, 2, b_row], "day_key": DAY_KEY})
    trips = _live(c, sid)
    assert len(trips) == 1
    merged = trips[0]
    assert merged["start"] == "09:00" and merged["end"] == "10:20"   # full span, no stranded half


def test_merging_only_one_split_half_would_strand_the_other(client):
    # Documents the failure mode this feature prevents: if a merge references
    # only ONE of the two split raw_rows, the other half is left out entirely.
    c, sid = client
    c.post(f"/api/{sid}/manual_split", json={"raw_row": 2, "day_key": DAY_KEY,
                                              "split_time": "10:10", "mode_a": "Walk", "mode_b": "Walk"})
    c.post(f"/api/{sid}/manual_merge", json={"raw_rows": [1, 2], "day_key": DAY_KEY})  # missing b_row
    trips = _live(c, sid)
    ids = {t["id"] for t in trips}
    assert split_b_row(2) in ids                 # the un-selected half survives, unmerged
    merged = _by_id(trips, 1)
    assert merged["end"] == "10:10"               # stops at the split point, not 10:20


def test_sibling_clears_once_both_halves_are_merged_together(client):
    # once merged, only one raw_row survives -- there is no more "other half" to
    # point at, so splitSibling must go back to None (not point at a dead row)
    c, sid = client
    c.post(f"/api/{sid}/manual_split", json={"raw_row": 2, "day_key": DAY_KEY,
                                              "split_time": "10:10", "mode_a": "Walk", "mode_b": "Walk"})
    b_row = split_b_row(2)
    c.post(f"/api/{sid}/manual_merge", json={"raw_rows": [2, b_row], "day_key": DAY_KEY})
    trips = _live(c, sid)
    assert len(trips) == 2   # trip 1 (A, untouched) + the re-merged trip 2
    assert all(t["splitSibling"] is None for t in trips)
