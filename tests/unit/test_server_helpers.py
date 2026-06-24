"""Unit tests for pure helpers in `app.server`."""

from __future__ import annotations

from app.server import _combine_dt


def test_combine_dt_pads_seconds_when_only_hh_mm():
    assert _combine_dt("p|2024-11-05", "08:30") == "2024-11-05T08:30:00"


def test_combine_dt_keeps_seconds_when_given():
    assert _combine_dt("p|2024-11-05", "08:30:15") == "2024-11-05T08:30:15"


def test_combine_dt_uses_date_after_the_pipe():
    # person ids can contain digits/dashes; only the part after '|' is the date
    assert _combine_dt("abc-12|2024-12-31", "23:59") == "2024-12-31T23:59:00"
