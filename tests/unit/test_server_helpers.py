"""Unit tests for pure helpers in `app.server`."""

from __future__ import annotations

from app import session
from app.server import _combine_dt, _next_add_seq


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
