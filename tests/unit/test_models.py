"""Unit tests for `engine.models` — the domain data holders."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from engine.models import RoutePoint, Trip


def test_routepoint_stores_fields() -> None:
    ts = datetime(2026, 5, 12, 8, 1, tzinfo=timezone.utc)
    p = RoutePoint(40.0, -3.0, ts)
    assert (p.lat, p.lon, p.ts) == (40.0, -3.0, ts)


def test_routepoint_is_immutable() -> None:
    p = RoutePoint(40.0, -3.0, datetime(2026, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.lat = 41.0  # type: ignore[misc]


def test_trip_defaults_to_empty_route() -> None:
    t = _make_trip()
    assert t.route == []


def test_trip_routes_are_independent_between_instances() -> None:
    # field(default_factory=list) must not share one list across instances.
    a, b = _make_trip(), _make_trip()
    a.route.append(RoutePoint(0.0, 0.0, datetime(2026, 1, 1, tzinfo=timezone.utc)))
    assert b.route == []


def _make_trip() -> Trip:
    t0 = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 5, 12, 8, 15, tzinfo=timezone.utc)
    return Trip(
        raw_row=142,
        person="P01",
        mode="car",
        purpose="",
        start_ts=t0,
        end_ts=t1,
        start_lat=55.68,
        start_lon=12.57,
        end_lat=55.71,
        end_lon=12.59,
    )
