"""Integration tests against real raw data files.

Run only when explicitly selected (the xlsx is not committed):

    pytest -m integration
"""

from __future__ import annotations

import os

import pytest

from engine.loader import LoadReport, load_trips

pytestmark = pytest.mark.integration

WAVE2_RAW = os.path.join("data", "app_data_wave2_raw.xlsx")


@pytest.mark.skipif(not os.path.exists(WAVE2_RAW), reason="raw data file not present")
def test_loads_wave2_raw() -> None:
    report = LoadReport()
    trips = load_trips(WAVE2_RAW, report)

    # Every row parsed into a usable trip (no missing coords / bad timestamps).
    assert report.total_rows == 9258
    assert report.loaded == 9258
    assert report.skipped == 0
    assert len(trips) == 9258

    # 173 distinct participants (accessCode), not the 11 phone models.
    persons = {t.person for t in trips}
    assert len(persons) == 173

    # Spot-check the first row is fully built and timezone-aware.
    first = trips[0]
    assert first.raw_row == 1
    assert first.mode == "Bike"
    assert first.start_ts.tzinfo is not None
    assert first.start_ts < first.end_ts

    # Modes are normalized: no zero-width characters survive.
    assert all("​" not in t.mode for t in trips)

    # is_manual signal carries through purpose (5231 manually-labelled trips).
    manual = sum(1 for t in trips if t.purpose)
    assert manual == 5231
