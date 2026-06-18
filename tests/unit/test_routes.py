"""Unit tests for `engine.routes` — triplet parsing + sub-second dedup."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.params import DEFAULT
from engine.routes import parse_route

PST = timezone(timedelta(hours=-8))


def test_parses_single_triplet() -> None:
    cell = "[[49.2702319884071, -123.04148381791731, '2024-11-05T18:02:48.999000-08:00']]"
    pts = parse_route(cell)
    assert len(pts) == 1
    assert pts[0].lat == 49.2702319884071
    assert pts[0].lon == -123.04148381791731
    assert pts[0].ts == datetime(2024, 11, 5, 18, 2, 48, 999000, tzinfo=PST)


def test_parses_multiple_triplets_in_order() -> None:
    cell = (
        "[[49.0, -123.0, '2024-11-05T18:00:00-08:00'], "
        "[49.1, -123.1, '2024-11-05T18:00:05-08:00'], "
        "[49.2, -123.2, '2024-11-05T18:00:10-08:00']]"
    )
    pts = parse_route(cell)
    assert [p.lat for p in pts] == [49.0, 49.1, 49.2]


def test_empty_list_cell_returns_empty() -> None:
    assert parse_route("[]") == []


def test_unparseable_cell_returns_empty() -> None:
    # The truncated-at-32767 shape: a list cut off mid-triplet.
    truncated = "[[49.26817001004356, -123.15055994035701, '2024-11-21T18:28:38-08:00'], ["
    assert parse_route(truncated) == []


def test_non_string_cell_returns_empty() -> None:
    assert parse_route(None) == []
    assert parse_route(float("nan")) == []


def test_sub_second_burst_deduped() -> None:
    # Three points inside one second collapse to the first; the >=1s point stays.
    cell = (
        "[[49.0, -123.0, '2024-11-05T18:00:00.000000-08:00'], "
        "[49.0, -123.0, '2024-11-05T18:00:00.400000-08:00'], "
        "[49.0, -123.0, '2024-11-05T18:00:00.900000-08:00'], "
        "[49.1, -123.1, '2024-11-05T18:00:01.500000-08:00']]"
    )
    pts = parse_route(cell)
    assert len(pts) == 2
    assert pts[0].ts == datetime(2024, 11, 5, 18, 0, 0, tzinfo=PST)
    assert pts[1].ts == datetime(2024, 11, 5, 18, 0, 1, 500000, tzinfo=PST)


def test_dedup_threshold_uses_last_kept_point() -> None:
    # Points 0.6s apart each, but all within ~1s of the LAST KEPT one drop until
    # the running gap from the kept point reaches the threshold.
    cell = (
        "[[49.0, -123.0, '2024-11-05T18:00:00.000000-08:00'], "
        "[49.0, -123.0, '2024-11-05T18:00:00.600000-08:00'], "
        "[49.0, -123.0, '2024-11-05T18:00:01.200000-08:00']]"
    )
    pts = parse_route(cell)
    # 0.000 kept; 0.600 dropped (<1s); 1.200 kept (>=1s from 0.000).
    assert [p.ts.microsecond for p in pts] == [0, 200000]


def test_malformed_triplet_skipped() -> None:
    cell = (
        "[[49.0, -123.0, '2024-11-05T18:00:00-08:00'], "
        "[49.1, -123.1], "  # too short -> skipped
        "[49.2, -123.2, '2024-11-05T18:00:10-08:00']]"
    )
    pts = parse_route(cell)
    assert [p.lat for p in pts] == [49.0, 49.2]


def test_params_threshold_respected() -> None:
    # With a 0s threshold nothing is deduped.
    from dataclasses import replace

    cell = (
        "[[49.0, -123.0, '2024-11-05T18:00:00.000000-08:00'], "
        "[49.0, -123.0, '2024-11-05T18:00:00.400000-08:00']]"
    )
    assert len(parse_route(cell, replace(DEFAULT, dedup_dt_s=0.0))) == 2
