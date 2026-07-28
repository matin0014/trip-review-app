"""Unit tests for `app.rail` (snapping to the SkyTrain track) and the
`routing.route_between` composite (access -> ride -> egress)."""

from __future__ import annotations

from app import rail, routing
from engine.geo import haversine_m


def _path_km(route):
    return sum(haversine_m(route[i][0], route[i][1], route[i + 1][0], route[i + 1][1])
               for i in range(len(route) - 1)) / 1000.0


# Real station coordinates (lat, lon).
WATERFRONT = (49.2857, -123.1119)
COMMERCIAL_BROADWAY = (49.2626, -123.0692)   # Expo/Millennium
RICHMOND_BRIGHOUSE = (49.1683, -123.1364)    # Canada Line south end
LOUGHEED = (49.2486, -122.8965)              # Millennium
KING_GEORGE = (49.1825, -122.8447)           # Expo east end
FAR_SW = (49.1132, -122.8124)                # ~8 km south of the Expo line
OKANAGAN = (49.8880, -119.4960)              # Kelowna -- no SkyTrain for 300 km


# --- rail.nearest_rail_segment (pure geometry) ------------------------------

def test_segment_snaps_both_ends_onto_the_expo_line():
    seg = rail.nearest_rail_segment(*WATERFRONT, *COMMERCIAL_BROADWAY)
    assert seg is not None
    assert seg["access_m"] < 300 and seg["egress_m"] < 300     # both clicks are on the line
    assert _path_km(seg["rail"]) > _path_km([list(WATERFRONT), list(COMMERCIAL_BROADWAY)])


def test_segment_reports_a_long_access_leg_for_a_far_start():
    # start 8 km from the line -> boarding snaps to the King George end, big access
    seg = rail.nearest_rail_segment(*FAR_SW, *WATERFRONT)
    assert 7_000 < seg["access_m"] < 9_000
    assert seg["egress_m"] < 300
    assert haversine_m(*seg["board"], *KING_GEORGE) < 500   # boards near King George


def test_segment_direction_follows_start_to_end():
    fwd = rail.nearest_rail_segment(*WATERFRONT, *LOUGHEED)["rail"]
    rev = rail.nearest_rail_segment(*LOUGHEED, *WATERFRONT)["rail"]
    assert haversine_m(*fwd[0], *WATERFRONT) < haversine_m(*fwd[0], *LOUGHEED)
    assert haversine_m(*rev[0], *LOUGHEED) < haversine_m(*rev[0], *WATERFRONT)


# --- routing.route_between (composite) --------------------------------------

def _fake_osrm(monkeypatch):
    # a 3-point straight stand-in so access/egress legs are offline + deterministic
    monkeypatch.setattr(routing, "osrm_route",
                        lambda la, lo, la2, lo2, mode=None: [[la, lo],
                                                             [(la + la2) / 2, (lo + lo2) / 2],
                                                             [la2, lo2]])


def test_composite_reaches_both_clicked_points_and_rides_the_rail(monkeypatch):
    _fake_osrm(monkeypatch)
    r = routing.route_between("SkyTrain", *FAR_SW, *WATERFRONT)
    assert r[0] == [FAR_SW[0], FAR_SW[1]]        # starts at the clicked start
    assert r[-1] == [WATERFRONT[0], WATERFRONT[1]]  # ends at the clicked end
    # the bulk is the rail ride, so the whole thing is far longer than the two legs
    assert len(r) > 100 and _path_km(r) > 25


def test_composite_skips_the_access_leg_when_already_on_the_line(monkeypatch):
    # both clicks on the line -> no OSRM access/egress, just the track
    monkeypatch.setattr(routing, "osrm_route",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no OSRM leg needed")))
    r = routing.route_between("SkyTrain", *WATERFRONT, *COMMERCIAL_BROADWAY)
    assert r and r[0] == [WATERFRONT[0], WATERFRONT[1]] and r[-1] == [COMMERCIAL_BROADWAY[0], COMMERCIAL_BROADWAY[1]]


def test_far_from_the_network_falls_back_to_plain_osrm(monkeypatch):
    called = {}
    monkeypatch.setattr(routing, "osrm_route",
                        lambda *a, **k: called.setdefault("hit", True) or [[a[0], a[1]], [a[2], a[3]]])
    # a "SkyTrain" trip in the Okanagan is not on the network -> plain OSRM, no rail
    routing.route_between("SkyTrain", *OKANAGAN, 49.89, -119.49)
    assert called.get("hit")


def test_non_skytrain_modes_use_osrm_directly(monkeypatch):
    seen = {}
    monkeypatch.setattr(routing, "osrm_route",
                        lambda la, lo, la2, lo2, mode=None: seen.setdefault("mode", mode) or [[la, lo], [la2, lo2]])
    routing.route_between("Walk", *WATERFRONT, *COMMERCIAL_BROADWAY)
    assert seen["mode"] == "Walk"    # passed straight through, no rail detour


# --- SeaBus (single line, Metro Vancouver) -----------------------------------

SEABUS_WATERFRONT = (49.28651, -123.110526)   # Vancouver terminal (line's own start point)
SEABUS_LONSDALE = (49.309322, -123.084112)    # North Vancouver terminal (line's own end point)


def test_seabus_snaps_both_ends_onto_the_line():
    seg = rail.nearest_rail_segment(*SEABUS_WATERFRONT, *SEABUS_LONSDALE, network="seabus")
    assert seg is not None
    assert seg["access_m"] < 50 and seg["egress_m"] < 50
    assert _path_km(seg["rail"]) > 1.0   # crosses the inlet, not a short hop


def test_composite_rides_the_seabus(monkeypatch):
    _fake_osrm(monkeypatch)
    r = routing.route_between("SeaBus", *SEABUS_WATERFRONT, *SEABUS_LONSDALE)
    assert r[0] == [SEABUS_WATERFRONT[0], SEABUS_WATERFRONT[1]]
    assert r[-1] == [SEABUS_LONSDALE[0], SEABUS_LONSDALE[1]]


# --- Kelowna bus (28 routes, low overlap) ------------------------------------

KELOWNA_ROUTE1_START = (49.8366, -119.48062)
KELOWNA_ROUTE1_END = (49.88726, -119.49483)
OKANAGAN_FAR = (49.75, -119.35)   # within the region but far from route 1


def test_kelowna_bus_snaps_onto_a_route():
    seg = rail.nearest_rail_segment(*KELOWNA_ROUTE1_START, *KELOWNA_ROUTE1_END, network="kelowna_bus")
    assert seg is not None
    assert seg["access_m"] < 50 and seg["egress_m"] < 50


def test_composite_rides_a_kelowna_bus(monkeypatch):
    _fake_osrm(monkeypatch)
    r = routing.route_between("Transit Bus", *KELOWNA_ROUTE1_START, *KELOWNA_ROUTE1_END)
    assert r[0] == [KELOWNA_ROUTE1_START[0], KELOWNA_ROUTE1_START[1]]
    assert r[-1] == [KELOWNA_ROUTE1_END[0], KELOWNA_ROUTE1_END[1]]
    assert len(r) > 10


def test_a_vancouver_transit_bus_trip_is_too_far_from_kelowna_and_falls_back(monkeypatch):
    # Metro Vancouver is ~300 km from Kelowna's network, so a "Transit Bus" trip
    # there must NOT get silently snapped onto a Kelowna route -- it falls back
    # to a plain OSRM street route, same as any other mode.
    called = {}
    monkeypatch.setattr(routing, "osrm_route",
                        lambda *a, **k: called.setdefault("hit", True) or [[a[0], a[1]], [a[2], a[3]]])
    routing.route_between("Transit Bus", *WATERFRONT, *COMMERCIAL_BROADWAY)
    assert called.get("hit")
