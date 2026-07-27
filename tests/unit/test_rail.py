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
