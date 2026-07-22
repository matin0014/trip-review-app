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


# --- fill_route_gaps: street path across a merge seam ------------------------

from datetime import datetime, timedelta, timezone   # noqa: E402

from engine.geo import EARTH_RADIUS_M  # noqa: E402
from engine.models import RoutePoint, Trip  # noqa: E402

_PST = timezone(timedelta(hours=-8))
_BASE = datetime(2024, 11, 5, 9, 0, tzinfo=_PST)
_ONE_DEG_M = EARTH_RADIUS_M * 3.141592653589793 / 180.0


def _dlon(m):
    return m / _ONE_DEG_M


def _trip(pairs, absorbed=frozenset()):
    """A trip from ``[(metres_east, minute), ...]`` breadcrumbs, so a test can set
    a seam's distance AND its time gap independently."""
    route = [RoutePoint(49.26, -123.12 + _dlon(x), _BASE + timedelta(minutes=m))
             for x, m in pairs]
    return Trip(raw_row=1, person="p", mode="Walk", purpose="",
                start_ts=route[0].ts, end_ts=route[-1].ts,
                start_lat=49.26, start_lon=route[0].lon,
                end_lat=49.26, end_lon=route[-1].lon,
                route=route, absorbed=absorbed)


def _fake_osrm(monkeypatch, n_mid=4):
    """Stand in for OSRM with a straight-ish path of n_mid intermediate points."""
    def fake(lat1, lon1, lat2, lon2, mode=None):
        step = (lon2 - lon1) / (n_mid + 1)
        return ([[lat1, lon1]]
                + [[lat1, lon1 + step * (i + 1)] for i in range(n_mid)]
                + [[lat2, lon2]])
    monkeypatch.setattr(routing, "osrm_route", fake)


def test_fill_route_gaps_splices_a_path_across_the_seam(monkeypatch):
    _fake_osrm(monkeypatch)
    # leg A ends at 2 min / 80 m; leg B starts at 30 min / 3400 m -- far apart AND
    # 28 minutes of unlogged travel, the case worth routing
    merged = _trip([(0, 0), (40, 1), (80, 2), (3400, 30), (3440, 31)],
                   absorbed=frozenset({1, 2}))
    out = routing.fill_route_gaps(merged)
    assert len(out.route) == len(merged.route) + 4          # 4 routed points spliced in
    assert out.route[0].lon == merged.route[0].lon          # real fixes preserved
    assert out.route[-1].lon == merged.route[-1].lon
    assert all(a.ts <= b.ts for a, b in zip(out.route, out.route[1:]))   # still ordered


def test_fill_route_gaps_ignores_an_unmerged_trip(monkeypatch):
    _fake_osrm(monkeypatch)
    plain = _trip([(0, 0), (40, 1), (3400, 30)])           # a real gap, but not merged
    assert routing.fill_route_gaps(plain).route == plain.route


def test_fill_route_gaps_leaves_small_seams_alone(monkeypatch):
    _fake_osrm(monkeypatch)
    # a fragment merge's seam is metres of GPS jitter -- routing it would be noise
    merged = _trip([(0, 0), (40, 1), (80, 30), (120, 31)], absorbed=frozenset({1, 2}))
    assert routing.fill_route_gaps(merged).route == merged.route


def test_fill_route_gaps_ignores_a_far_jump_that_is_seconds_apart(monkeypatch):
    # 3 km between two fixes 30 s apart is sparse sampling / tower jitter, not an
    # unlogged journey -- 94% of >200 m jumps in the real wave data look like this.
    _fake_osrm(monkeypatch)
    merged = _trip([(0, 0), (3000, 0.5), (3040, 30)], absorbed=frozenset({1, 2}))
    assert routing.fill_route_gaps(merged).route == merged.route


def test_split_then_merge_restores_the_route_exactly(monkeypatch):
    # A split partitions a route by timestamp, so re-merging the pieces must give
    # back the original -- even when the trip contains a big internal jump. The
    # time test is what guarantees it (the seam is one sampling interval).
    _fake_osrm(monkeypatch)
    original = _trip([(0, 0), (30, 0.5), (3060, 1), (3090, 1.5)],
                     absorbed=frozenset({1, 2}))
    assert routing.fill_route_gaps(original).route == original.route


def test_fill_route_gaps_skips_gps_ping_pong(monkeypatch):
    # More large seams than a 2-leg merge can have (1) -> a phone alternating
    # between a real fix and a tower fix, so fabricate nothing.
    _fake_osrm(monkeypatch)
    noisy = _trip([(0, 0), (4000, 10), (40, 20), (4040, 30), (80, 40)],
                  absorbed=frozenset({1, 2}))
    assert routing.fill_route_gaps(noisy).route == noisy.route


def test_fill_route_gaps_degrades_when_osrm_fails(monkeypatch):
    monkeypatch.setattr(routing, "osrm_route", lambda *a, **k: [])
    merged = _trip([(0, 0), (40, 1), (3400, 30)], absorbed=frozenset({1, 2}))
    assert routing.fill_route_gaps(merged).route == merged.route   # straight line stands


def test_straight_line_distance_m_matches_haversine():
    from engine.geo import haversine_m
    d = routing.straight_line_distance_m(49.0, -123.0, 49.1, -123.1)
    assert d == haversine_m(49.0, -123.0, 49.1, -123.1)
    assert d > 0
