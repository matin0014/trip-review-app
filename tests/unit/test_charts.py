"""Unit tests for `app.charts` -- start-time binning + raw-vs-live-cleaned series."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import charts, regions
from engine.geo import EARTH_RADIUS_M
from engine.models import RoutePoint, Trip

PST = timezone(timedelta(hours=-8))
TUE = datetime(2024, 11, 5, 9, 0, 0, tzinfo=PST)    # a Tuesday
SAT = datetime(2024, 11, 9, 9, 0, 0, tzinfo=PST)    # a Saturday
ONE_DEGREE_M = EARTH_RADIUS_M * 3.141592653589793 / 180.0


@pytest.fixture(autouse=True)
def _default_regions(tmp_path, monkeypatch):
    # no regions file -> built-in defaults (the synthetic Vancouver trips below
    # are in-region, so nothing is auto-excluded unless a test removes a day)
    monkeypatch.setattr(regions, "_PATH", str(tmp_path / "no-regions.json"))


def dlon(m):
    return m / ONE_DEGREE_M


def trip(raw, base, smin, emin, slon_m=0.0, elon_m=2000.0, person="p"):
    """One well-separated ~2 km Vancouver trip; smin/emin are minutes from base."""
    s, e = base + timedelta(minutes=smin), base + timedelta(minutes=emin)
    return Trip(raw_row=raw, person=person, mode="Auto Driver", purpose="",
                start_ts=s, end_ts=e,
                start_lat=49.26, start_lon=-123.12 + dlon(slon_m),
                end_lat=49.26, end_lon=-123.12 + dlon(elon_m),
                route=[RoutePoint(49.26, -123.12 + dlon(slon_m), s),
                       RoutePoint(49.26, -123.12 + dlon(elon_m), e)])


def day_key(t):
    return f"{t.person}|{t.start_ts.date()}"


# --- rounded_hour -----------------------------------------------------------

def test_rounded_hour_rounds_down_before_the_half_hour():
    assert charts.rounded_hour(datetime(2024, 11, 5, 9, 29)) == 9


def test_rounded_hour_rounds_up_from_the_half_hour():
    assert charts.rounded_hour(datetime(2024, 11, 5, 9, 30)) == 10


def test_rounded_hour_wraps_2330_to_midnight():
    assert charts.rounded_hour(datetime(2024, 11, 5, 23, 30)) == 0


# --- start_time_series ------------------------------------------------------

def _series(days, events=()):
    return charts.start_time_series(days, list(events))


def test_empty_session_is_all_zero_bins():
    s = _series({})
    assert s["all"]["raw"] == [0] * 24 and s["all"]["cleaned"] == [0] * 24
    assert s["all"]["n_raw"] == 0 and s["weekday"]["n_cleaned"] == 0


def test_bins_land_on_the_rounded_hour():
    # 09:00 -> bin 9; a 10:29 start rounds DOWN into bin 10 (not 11)
    t1, t2 = trip(1, TUE, 0, 20), trip(2, TUE, 89, 110)   # 09:00 and 10:29
    s = _series({day_key(t1): [t1, t2]})
    assert s["all"]["raw"][9] == 1 and s["all"]["raw"][10] == 1
    assert s["all"]["n_raw"] == 2
    assert s["all"]["raw"] == s["all"]["cleaned"]          # nothing cleaned away


def test_manual_delete_leaves_raw_but_drops_from_cleaned():
    t1, t2 = trip(1, TUE, 0, 20), trip(2, TUE, 120, 140)
    key = day_key(t1)
    ev = [{"type": "manual_delete", "raw_row": 1, "day_key": key}]
    s = _series({key: [t1, t2]}, ev)
    assert s["all"]["n_raw"] == 2
    assert s["all"]["n_cleaned"] == 1
    assert s["all"]["cleaned"][9] == 0                     # the 09:00 trip is gone
    assert s["all"]["cleaned"][11] == 1                    # the 11:00 trip remains


def test_weekday_variant_excludes_weekend_trips():
    wk, we = trip(1, TUE, 0, 20), trip(2, SAT, 0, 20, person="q")
    s = _series({day_key(wk): [wk], day_key(we): [we]})
    assert s["all"]["n_raw"] == 2
    assert s["weekday"]["n_raw"] == 1                      # Saturday filtered out
    assert s["weekday"]["raw"][9] == 1


def test_hand_removed_day_stays_in_raw_but_not_cleaned():
    t1 = trip(1, TUE, 0, 20)
    key = day_key(t1)
    ev = [{"type": "day_include", "day_key": key, "include": False}]
    s = _series({key: [t1]}, ev)
    assert s["all"]["n_raw"] == 1                          # raw is the untouched baseline
    assert s["all"]["n_cleaned"] == 0                      # excluded day fully dropped


def test_manual_add_counts_in_cleaned_only():
    t1 = trip(1, TUE, 0, 20)
    key = day_key(t1)
    ev = [{"type": "manual_add", "day_key": key,
           "start_ts": "2024-11-05T13:00:00", "end_ts": "2024-11-05T13:10:00",
           "mode": "Bus", "purpose": "", "route": [],
           "start_lat": 49.26, "start_lon": -123.12,
           "end_lat": 49.27, "end_lon": -123.12}]
    s = _series({key: [t1]}, ev)
    assert s["all"]["n_raw"] == 1
    assert s["all"]["n_cleaned"] == 2
    assert s["all"]["cleaned"][13] == 1                    # the added 13:00 trip
