"""Unit tests for pure helpers in `app.server`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.server import _combine_dt, _morning_commute_flag, _purpose_place_flags
from engine.models import Trip


def test_combine_dt_pads_seconds_when_only_hh_mm():
    assert _combine_dt("p|2024-11-05", "08:30") == "2024-11-05T08:30:00"


def test_combine_dt_keeps_seconds_when_given():
    assert _combine_dt("p|2024-11-05", "08:30:15") == "2024-11-05T08:30:15"


def test_combine_dt_uses_date_after_the_pipe():
    # person ids can contain digits/dashes; only the part after '|' is the date
    assert _combine_dt("abc-12|2024-12-31", "23:59") == "2024-12-31T23:59:00"


def _trip(start_hour: int):
    base = datetime(2024, 11, 5, start_hour, 0, tzinfo=timezone(timedelta(hours=-8)))
    return Trip(
        raw_row=1, person="A1", mode="Walk", purpose="",
        start_ts=base, end_ts=base + timedelta(minutes=10),
        start_lat=49.0, start_lon=-123.0, end_lat=49.1, end_lon=-123.1,
    )


def _place_trip(raw_row=1, purpose="", end_lat=49.2, end_lon=-123.2):
    base = datetime(2024, 11, 5, 11, 0, tzinfo=timezone(timedelta(hours=-8)))
    return Trip(
        raw_row=raw_row, person="A1", mode="Walk", purpose=purpose,
        start_ts=base, end_ts=base + timedelta(minutes=10),
        start_lat=49.0, start_lon=-123.0, end_lat=end_lat, end_lon=end_lon,
    )


def _data(arrangement: str):
    return {"participant_info": {"A1": [{"key": "workArrangements", "value": arrangement}]}}


def _day(date="2024-11-05"):
    return {
        "person": "A1",
        "date": date,
        "places": [
            {"type": "home", "lat": 49.0, "lon": -123.0},
            {"type": "work", "lat": 49.2, "lon": -123.2},
        ],
    }


def test_morning_commute_flag_for_usual_commuter_without_pre_10_trip():
    flag = _morning_commute_flag("A1|2024-11-05", _data("Everyday travel to work"), _day(), [_trip(11)])

    assert flag is not None
    assert flag["type"] == "morning_commute"
    assert flag["ev"]["start_time"] == "08:30"
    assert flag["ev"]["start"] == [49.0, -123.0]
    assert flag["ev"]["end"] == [49.2, -123.2]


def test_morning_commute_flag_skips_when_morning_trip_exists():
    flag = _morning_commute_flag("A1|2024-11-05", _data("Mostly travel to work"), _day(), [_trip(9)])

    assert flag is None


def test_morning_commute_flag_skips_non_commuter_work_arrangement():
    flag = _morning_commute_flag("A1|2024-11-05", _data("Work from home"), _day(), [_trip(11)])

    assert flag is None


def test_morning_commute_flag_skips_weekends():
    flag = _morning_commute_flag("A1|2024-11-09", _data("Everyday travel to work"),
                                 _day("2024-11-09"), [_trip(11)])

    assert flag is None


def test_purpose_place_flags_suggest_work_for_blank_purpose_near_work():
    flags = _purpose_place_flags("A1|2024-11-05", _day(), [_place_trip()])

    assert len(flags) == 1
    assert flags[0]["type"] == "purpose_work"
    assert flags[0]["trip_id"] == 1
    assert flags[0]["purpose"] == "Work"


def test_purpose_place_flags_suggest_home_for_blank_purpose_near_home():
    flags = _purpose_place_flags("A1|2024-11-05", _day(), [_place_trip(end_lat=49.0, end_lon=-123.0)])

    assert len(flags) == 1
    assert flags[0]["type"] == "purpose_home"
    assert flags[0]["purpose"] == "Home"


def test_purpose_place_flags_skip_when_purpose_present_or_outside_buffer():
    flags = _purpose_place_flags("A1|2024-11-05", _day(), [
        _place_trip(raw_row=1, purpose="Shop"),
        _place_trip(raw_row=2, end_lat=50.0, end_lon=-123.0),
    ])

    assert flags == []
