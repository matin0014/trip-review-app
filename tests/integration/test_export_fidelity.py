"""Export-fidelity integration tests: every hand edit made in the app must be
faithfully reflected in cleaned.xlsx, including the derived/format details.

These pin down three real bugs found in a reviewer's export (2026-07-15):
  * a repositioned START point was silently lost when the trip was also merged
    (the merge overwrite only rewrote the end/route, never the start);
  * the raw ``time_duration`` column went stale whenever a row's start/end was
    overwritten (merge span, split truncation, manual time edit);
  * overwritten time cells were written bare (``10:20:00``) while untouched raw
    cells keep fractional seconds + a UTC offset (``10:20:00.999000-08:00``),
    leaving one column with two formats.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app import generate, routing, session


@pytest.fixture(autouse=True)
def _offline_osrm(monkeypatch):
    monkeypatch.setattr(routing, "osrm_route", lambda *a, **k: [])


DATE = "2024-11-05"
PERSON = "P1"
DAY_KEY = f"{PERSON}|{DATE}"
# Same shape as the wave2 workbook, WITH its time_duration column (the other
# integration suites omit it, so its staleness went unnoticed).
RAW_COLS = ["accessCode", "start_date", "start_time", "end_date", "end_time",
            "time_duration", "mode_of_travel", "purpose_of_travel",
            "start_latitude", "start_longitude", "end_latitude",
            "end_longitude", "routes"]


def _ts(hms: str) -> str:
    return f"{hms}-08:00"               # the wave2 time columns carry a -08:00 offset


def _route(slat, slon, elat, elon, s_hms, e_hms) -> str:
    return repr([[slat, slon, f"{DATE}T{_ts(s_hms)}"],
                 [elat, elon, f"{DATE}T{_ts(e_hms)}"]])


def trip(start, end, slat, slon, elat, elon, dur):
    return {
        "accessCode": PERSON, "start_date": DATE, "start_time": _ts(start),
        "end_date": DATE, "end_time": _ts(end), "time_duration": dur,
        "mode_of_travel": "Auto Driver", "purpose_of_travel": "",
        "start_latitude": slat, "start_longitude": slon,
        "end_latitude": elat, "end_longitude": elon,
        "routes": _route(slat, slon, elat, elon, start, end),
    }


# A clean, well-separated 3-trip day (no flags fire), each trip 20 min / ~2 km.
def rows():
    return [
        trip("09:00:00", "09:20:00", 49.260, -123.120, 49.278, -123.120, "0:20:00"),
        trip("10:00:00", "10:20:00", 49.278, -123.120, 49.296, -123.120, "0:20:00"),
        trip("11:00:00", "11:20:00", 49.296, -123.120, 49.314, -123.120, "0:20:00"),
    ]


def _make_session(tmp_path, monkeypatch) -> str:
    monkeypatch.setattr(session, "SESSIONS_DIR", str(tmp_path))
    sid = "s"
    session.write_proposals(sid, {"raw_name": "raw.xlsx"})
    raw_path = f"{session.session_dir(sid)}/raw.xlsx"
    pd.DataFrame(rows(), columns=RAW_COLS).to_excel(raw_path, index=False)
    return sid


def _cleaned(sid):
    cleaned_path, _ = generate.generate_cleaned_and_dofile(sid)
    return pd.read_excel(cleaned_path)


def _row(df, trip_id: str):
    return df[df["trip_id"].astype(str) == trip_id].iloc[0]


def _merge(sid, raw_rows):
    session.append_event(sid, {"type": "manual_merge", "raw_rows": raw_rows,
                               "day_key": DAY_KEY})


def _reposition_start_of_row_1(sid):
    """Move trip 1's start to (49.255, -123.125), as the app's endpoint would:
    the op carries the complete final route precomputed server-side."""
    new_route = [[49.255, -123.125, f"{DATE}T{_ts('09:00:00')}"],
                 [49.278, -123.120, f"{DATE}T{_ts('09:20:00')}"]]
    session.append_event(sid, {
        "type": "manual_reposition", "raw_row": 1, "end": "start",
        "lat": 49.255, "lon": -123.125, "route": new_route, "day_key": DAY_KEY})


# --- bug 1: reposition + merge --------------------------------------------

def test_repositioned_start_survives_a_merge_into_the_cleaned_file(tmp_path, monkeypatch):
    # the exact sequence from the real session: fix a trip's start point, then
    # merge it with the next trip -- the M-row must keep the corrected start.
    sid = _make_session(tmp_path, monkeypatch)
    _reposition_start_of_row_1(sid)
    _merge(sid, [1, 2])
    m1 = _row(_cleaned(sid), "M1")

    assert m1["start_latitude"] == pytest.approx(49.255)    # the corrected point,
    assert m1["start_longitude"] == pytest.approx(-123.125)  # not the raw GPS one
    assert m1["end_latitude"] == pytest.approx(49.296)       # merged end = leg 2's


# --- bug 2: stale time_duration --------------------------------------------

def test_merge_recomputes_the_time_duration_column(tmp_path, monkeypatch):
    # the merged row spans 09:00 -> 10:20; its duration cell must say so, not
    # keep the first leg's stale 0:20:00.
    sid = _make_session(tmp_path, monkeypatch)
    _merge(sid, [1, 2])
    m1 = _row(_cleaned(sid), "M1")
    assert str(m1["time_duration"]) == "1:20:00"


def test_manual_time_recomputes_the_time_duration_column(tmp_path, monkeypatch):
    sid = _make_session(tmp_path, monkeypatch)
    session.append_event(sid, {"type": "manual_time", "raw_row": 2,
                               "start_ts": f"{DATE}T10:00:00",
                               "end_ts": f"{DATE}T10:45:00", "day_key": DAY_KEY})
    row2 = _row(_cleaned(sid), "2")
    assert str(row2["time_duration"]) == "0:45:00"


def test_untouched_rows_keep_their_original_time_duration(tmp_path, monkeypatch):
    sid = _make_session(tmp_path, monkeypatch)
    _merge(sid, [1, 2])
    row3 = _row(_cleaned(sid), "3")
    assert str(row3["time_duration"]) == "0:20:00"          # verbatim raw value


# --- bug 3: mixed time formats ---------------------------------------------

def test_merge_overwritten_end_time_keeps_the_raw_offset_format(tmp_path, monkeypatch):
    # untouched cells look like "10:20:00-08:00"; the merged row's overwritten
    # end_time must match that style, not switch to a bare "10:20:00".
    sid = _make_session(tmp_path, monkeypatch)
    _merge(sid, [1, 2])
    m1 = _row(_cleaned(sid), "M1")
    assert str(m1["end_time"]) == "10:20:00-08:00"


def test_manual_time_keeps_the_raw_offset_format(tmp_path, monkeypatch):
    # a manual time edit is typed offset-less in the UI; the cell it overwrites
    # had "-08:00", so the rewritten value must carry the same offset.
    sid = _make_session(tmp_path, monkeypatch)
    session.append_event(sid, {"type": "manual_time", "raw_row": 2,
                               "start_ts": f"{DATE}T10:00:00",
                               "end_ts": f"{DATE}T10:45:00", "day_key": DAY_KEY})
    row2 = _row(_cleaned(sid), "2")
    assert str(row2["start_time"]) == "10:00:00-08:00"
    assert str(row2["end_time"]) == "10:45:00-08:00"


# --- bug 4: dofile A/Q rows piling up at the bottom --------------------------

def test_dofile_slots_an_added_row_into_chronological_position(tmp_path, monkeypatch):
    # the cleaned file sorts synthesized rows into their day; the dofile must
    # too, not dump every A/Q row after the last raw row of the whole file.
    sid = _make_session(tmp_path, monkeypatch)
    session.append_event(sid, {
        "type": "manual_add", "start_ts": f"{DATE}T09:30:00",
        "end_ts": f"{DATE}T09:45:00", "mode": "Bike", "purpose": "Errand",
        "start_lat": 49.278, "start_lon": -123.120,
        "end_lat": 49.280, "end_lon": -123.120,
        "route": [[49.278, -123.120], [49.280, -123.120]], "day_key": DAY_KEY})
    _, dofile = generate.generate_cleaned_and_dofile(sid)
    dofile = pd.read_excel(dofile)
    assert [str(t) for t in dofile["trip_id"]] == ["1", "A01", "2", "3"]
