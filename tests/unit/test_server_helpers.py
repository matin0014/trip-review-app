"""Unit tests for pure helpers in `app.server`."""

from __future__ import annotations

import pytest

from app import session
from app.server import _combine_dt, _edit_label, _edits_for_day, _estimate_duration_min, _next_add_seq


def test_combine_dt_pads_seconds_when_only_hh_mm():
    assert _combine_dt("p|2024-11-05", "08:30") == "2024-11-05T08:30:00"


def test_combine_dt_keeps_seconds_when_given():
    assert _combine_dt("p|2024-11-05", "08:30:15") == "2024-11-05T08:30:15"


def test_combine_dt_uses_date_after_the_pipe():
    # person ids can contain digits/dashes; only the part after '|' is the date
    assert _combine_dt("abc-12|2024-12-31", "23:59") == "2024-12-31T23:59:00"


# --- _next_add_seq ----------------------------------------------------------

DAY = "p|2024-11-10"
OTHER_DAY = "p|2024-11-11"


def _add_event(day_key=DAY):
    return {"type": "manual_add", "day_key": day_key}


def test_next_add_seq_starts_at_one(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSIONS_DIR", str(tmp_path))
    session.write_proposals("s", {"raw_name": "raw.xlsx"})
    assert _next_add_seq("s", DAY) == 1


def test_next_add_seq_increments_per_add_ever_logged(tmp_path, monkeypatch):
    # counts EVERY manual_add ever logged for the day, not just currently active
    # ones -- a number must never be reused, even after a revert (the bug this
    # sequencing exists to prevent: a later add silently inheriting an earlier,
    # reverted add's raw_row and, with it, stale edits still aimed at that row).
    monkeypatch.setattr(session, "SESSIONS_DIR", str(tmp_path))
    session.write_proposals("s", {"raw_name": "raw.xlsx"})
    session.append_event("s", _add_event())
    assert _next_add_seq("s", DAY) == 2
    session.append_event("s", {"type": "revert"})   # revert the add; log still has it
    assert _next_add_seq("s", DAY) == 2              # NOT reset back to 1
    session.append_event("s", _add_event())
    assert _next_add_seq("s", DAY) == 3


def test_next_add_seq_is_per_day(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSIONS_DIR", str(tmp_path))
    session.write_proposals("s", {"raw_name": "raw.xlsx"})
    session.append_event("s", _add_event(DAY))
    session.append_event("s", _add_event(DAY))
    assert _next_add_seq("s", OTHER_DAY) == 1        # a different day starts fresh
    assert _next_add_seq("s", DAY) == 3


# --- _estimate_duration_min ---------------------------------------------------

def test_estimate_duration_uses_the_midpoint_of_the_mode_reference_range():
    # Walk's reference range is (5, 6) km/h -> midpoint 5.5 km/h; 5500 m at
    # 5.5 km/h takes exactly 60 minutes.
    assert _estimate_duration_min("Walk", 5500) == pytest.approx(60.0)


def test_estimate_duration_differs_by_mode_for_the_same_distance():
    # a mode with a canonical FAST reference speed (SkyTrain) takes far less
    # time to cover the same distance than a mode with a slow one (Walk) --
    # this is the whole point of using the mode-specific table, not one speed.
    walk = _estimate_duration_min("Walk", 10_000)
    skytrain = _estimate_duration_min("SkyTrain", 10_000)
    assert walk > skytrain


def test_estimate_duration_none_for_a_mode_with_no_reference_speed():
    assert _estimate_duration_min("Unknown", 1000) is None


def test_estimate_duration_none_for_zero_distance():
    assert _estimate_duration_min("Walk", 0) is None


# --- _edit_label / _edits_for_day --------------------------------------------

def test_edit_label_covers_every_manual_op_type():
    assert _edit_label({"type": "manual_delete", "raw_row": 3}) == "Deleted trip row 3"
    assert _edit_label({"type": "manual_merge", "raw_rows": [1, 2]}) == "Merged rows 1, 2"
    assert _edit_label({"type": "manual_split", "raw_row": 1,
                         "split_ts": "2024-11-05T12:00:50"}) == "Split row 1 at 12:00"
    assert _edit_label({"type": "manual_add", "start_ts": "2024-11-05T13:00:00",
                         "end_ts": "2024-11-05T13:10:00"}) == "Added trip 13:00-13:10"
    assert _edit_label({"type": "manual_mode", "raw_row": 2, "mode": "Bike"}) == "Changed row 2 mode to Bike"
    assert _edit_label({"type": "manual_purpose", "raw_row": 2, "purpose": "Work"}) == \
        "Changed row 2 purpose to Work"
    assert _edit_label({"type": "manual_purpose", "raw_row": 2, "purpose": ""}) == \
        "Changed row 2 purpose to blank"
    assert _edit_label({"type": "manual_time", "raw_row": 2, "start_ts": "2024-11-05T08:30:00",
                         "end_ts": "2024-11-05T08:45:00"}) == "Changed row 2 time to 08:30-08:45"
    assert _edit_label({"type": "manual_reposition", "raw_row": 2, "end": "start"}) == \
        "Moved row 2's start point"
    assert _edit_label({"type": "something_new"}) == "something_new"


def test_edits_for_day_only_includes_the_requested_day_and_carries_seq():
    events = [
        {"seq": 1, "type": "manual_delete", "raw_row": 1, "day_key": "p|2024-11-05"},
        {"seq": 2, "type": "manual_delete", "raw_row": 2, "day_key": "p|2024-11-06"},
    ]
    edits = _edits_for_day(events, "p|2024-11-05")
    assert len(edits) == 1
    assert edits[0]["seq"] == 1 and edits[0]["type"] == "manual_delete"
    assert edits[0]["label"] == "Deleted trip row 1"
