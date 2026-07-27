"""Unit tests for `app.rail` -- snapping a SkyTrain trip to the real track."""

from __future__ import annotations

from app import rail
from engine.geo import haversine_m


def _path_km(route):
    return sum(haversine_m(route[i][0], route[i][1], route[i + 1][0], route[i + 1][1])
               for i in range(len(route) - 1)) / 1000.0


# Real station coordinates (lat, lon).
WATERFRONT = (49.2857, -123.1119)
COMMERCIAL_BROADWAY = (49.2626, -123.0692)   # Expo/Millennium
RICHMOND_BRIGHOUSE = (49.1683, -123.1364)    # Canada Line south end
LOUGHEED = (49.2486, -122.8965)              # Millennium
VCC_CLARK = (49.2657, -123.0788)             # Millennium west end


def test_snap_follows_the_expo_line_and_is_longer_than_straight():
    r = rail.snap_route(*WATERFRONT, *COMMERCIAL_BROADWAY)
    assert r, "should snap onto the Expo line"
    straight = haversine_m(*WATERFRONT, *COMMERCIAL_BROADWAY) / 1000.0
    # the track curves, so the along-line distance exceeds the straight line
    assert _path_km(r) > straight
    assert r[0] == [WATERFRONT[0], WATERFRONT[1]]        # reaches the clicked start
    assert r[-1] == [COMMERCIAL_BROADWAY[0], COMMERCIAL_BROADWAY[1]]  # and the clicked end


def test_snap_picks_the_canada_line_for_a_canada_line_trip():
    r = rail.snap_route(*WATERFRONT, *RICHMOND_BRIGHOUSE)
    assert r
    # Waterfront -> Richmond-Brighouse is nearly the whole 14.3 km Canada Line
    assert 13.0 < _path_km(r) < 15.5


def test_snap_direction_is_start_to_end():
    fwd = rail.snap_route(*VCC_CLARK, *LOUGHEED)
    rev = rail.snap_route(*LOUGHEED, *VCC_CLARK)
    assert fwd[0] == [VCC_CLARK[0], VCC_CLARK[1]] and fwd[-1] == [LOUGHEED[0], LOUGHEED[1]]
    assert rev[0] == [LOUGHEED[0], LOUGHEED[1]] and rev[-1] == [VCC_CLARK[0], VCC_CLARK[1]]
    assert abs(_path_km(fwd) - _path_km(rev)) < 0.05   # same track either way


def test_no_snap_when_both_points_are_far_from_any_line():
    # deep in the Fraser Valley, nowhere near the rail network
    assert rail.snap_route(49.10, -122.30, 49.11, -122.31) == []


def test_route_between_dispatches_skytrain_to_rail(monkeypatch):
    from app import routing
    # if OSRM were called for SkyTrain it would blow up here; it must not be
    monkeypatch.setattr(routing, "osrm_route",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("used OSRM")))
    r = routing.route_between("SkyTrain", *WATERFRONT, *COMMERCIAL_BROADWAY)
    assert r and len(r) > 2


def test_route_between_falls_back_to_osrm_off_network(monkeypatch):
    from app import routing
    called = {}
    monkeypatch.setattr(routing, "osrm_route",
                        lambda *a, **k: called.setdefault("hit", True) or [[49.1, -122.3]])
    # SkyTrain mode but a click far from any line -> no snap -> OSRM
    routing.route_between("SkyTrain", 49.10, -122.30, 49.11, -122.31)
    assert called.get("hit")
