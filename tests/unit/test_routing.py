"""Unit tests for `app.routing` -- OSRM route/distance lookup and its cache."""

from __future__ import annotations

import json

import pytest

from app import routing


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _osrm_payload(coords_lonlat, distance_m):
    return {"routes": [{"geometry": {"coordinates": coords_lonlat}, "distance": distance_m}]}


@pytest.fixture(autouse=True)
def _clear_cache():
    routing._CACHE.clear()
    yield
    routing._CACHE.clear()


def test_osrm_route_returns_lat_lon_from_lon_lat(monkeypatch):
    calls = []

    def fake_urlopen(url, timeout):
        calls.append(url)
        return _FakeResponse(_osrm_payload([[-123.0, 49.0], [-123.1, 49.1]], 500.0))

    monkeypatch.setattr(routing.urllib.request, "urlopen", fake_urlopen)
    coords = routing.osrm_route(49.0, -123.0, 49.1, -123.1, "Walk")
    assert coords == [[49.0, -123.0], [49.1, -123.1]]  # swapped back to [lat, lon]
    assert len(calls) == 1


def test_osrm_distance_m_shares_the_route_cache_no_extra_network_call(monkeypatch):
    calls = []

    def fake_urlopen(url, timeout):
        calls.append(url)
        return _FakeResponse(_osrm_payload([[-123.0, 49.0], [-123.1, 49.1]], 1234.5))

    monkeypatch.setattr(routing.urllib.request, "urlopen", fake_urlopen)
    coords = routing.osrm_route(49.0, -123.0, 49.1, -123.1, "Bike")
    dist = routing.osrm_distance_m(49.0, -123.0, 49.1, -123.1, "Bike")
    assert coords  # route succeeded
    assert dist == 1234.5
    assert len(calls) == 1  # the second call hit the cache, not the network


def test_osrm_distance_m_none_on_failure(monkeypatch):
    def fake_urlopen(url, timeout):
        raise OSError("network down")

    monkeypatch.setattr(routing.urllib.request, "urlopen", fake_urlopen)
    assert routing.osrm_route(49.0, -123.0, 49.1, -123.1, "Walk") == []
    assert routing.osrm_distance_m(49.0, -123.0, 49.1, -123.1, "Walk") is None


def test_straight_line_distance_m_matches_haversine():
    from engine.geo import haversine_m
    d = routing.straight_line_distance_m(49.0, -123.0, 49.1, -123.1)
    assert d == haversine_m(49.0, -123.0, 49.1, -123.1)
    assert d > 0
