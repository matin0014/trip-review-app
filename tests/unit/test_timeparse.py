"""Unit tests for `engine.timeparse` — robust ISO timestamp parsing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine.timeparse import combine_date_time, parse_ts

PST = timezone(timedelta(hours=-8))


def test_parse_route_triplet_form() -> None:
    # The full ISO instant carried in route breadcrumbs.
    dt = parse_ts("2024-11-05T18:02:48.999000-08:00")
    assert dt == datetime(2024, 11, 5, 18, 2, 48, 999000, tzinfo=PST)
    assert dt.tzinfo is not None  # always aware


def test_combine_split_columns() -> None:
    # The row columns: date and time live apart; the time carries the offset.
    dt = combine_date_time("2024-11-05", "18:01:15.999000-08:00")
    assert dt == datetime(2024, 11, 5, 18, 1, 15, 999000, tzinfo=PST)


def test_combine_matches_parse_of_joined_string() -> None:
    a = combine_date_time("2024-11-14", "09:36:43.761000-08:00")
    b = parse_ts("2024-11-14T09:36:43.761000-08:00")
    assert a == b


def test_surrounding_whitespace_tolerated() -> None:
    assert parse_ts("  2024-11-05T18:02:48.999000-08:00  ") == datetime(
        2024, 11, 5, 18, 2, 48, 999000, tzinfo=PST
    )


def test_repairs_trailing_z() -> None:
    # 3.9's fromisoformat rejects a bare 'Z'; we normalise it to +00:00.
    dt = parse_ts("2024-11-05T18:02:48Z")
    assert dt == datetime(2024, 11, 5, 18, 2, 48, tzinfo=timezone.utc)


def test_repairs_odd_fractional_precision() -> None:
    # Milliseconds (3 digits) are fine; an odd count (e.g. 4) is padded to micros.
    assert parse_ts("2024-11-05T18:02:48.5-08:00") == datetime(
        2024, 11, 5, 18, 2, 48, 500000, tzinfo=PST
    )
    assert parse_ts("2024-11-05T18:02:48.1234-08:00") == datetime(
        2024, 11, 5, 18, 2, 48, 123400, tzinfo=PST
    )


@pytest.mark.parametrize("bad", ["", "   ", "not-a-timestamp", None, 12345])
def test_rejects_unparseable(bad: object) -> None:
    with pytest.raises(ValueError):
        parse_ts(bad)  # type: ignore[arg-type]
