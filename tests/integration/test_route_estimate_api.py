"""Integration test for GET /api/route_estimate -- the manual-add end-time
suggestion endpoint. No session needed (pure calculation given two points +
a mode); network calls are stubbed so the test never hits real OSRM."""

from __future__ import annotations

from app import routing, server


def _client(monkeypatch):
    monkeypatch.setattr(routing, "osrm_distance_m", lambda *a, **k: None)
    monkeypatch.setattr(routing, "straight_line_distance_m", lambda *a, **k: 0.0)
    return server.create_app().test_client()


def test_uses_routed_distance_when_osrm_succeeds(monkeypatch):
    monkeypatch.setattr(routing, "osrm_distance_m", lambda *a, **k: 5500.0)
    c = server.create_app().test_client()
    r = c.get("/api/route_estimate", query_string={
        "mode": "Walk", "start_lat": 49.0, "start_lon": -123.0,
        "end_lat": 49.01, "end_lon": -123.0})
    assert r.status_code == 200
    body = r.get_json()
    assert body["routed"] is True
    assert body["distance_m"] == 5500.0
    assert body["duration_min"] == 60.0  # 5.5 km at Walk's 5.5 km/h midpoint


def test_falls_back_to_straight_line_when_osrm_fails(monkeypatch):
    monkeypatch.setattr(routing, "osrm_distance_m", lambda *a, **k: None)
    monkeypatch.setattr(routing, "straight_line_distance_m", lambda *a, **k: 1000.0)
    c = server.create_app().test_client()
    r = c.get("/api/route_estimate", query_string={
        "mode": "Walk", "start_lat": 49.0, "start_lon": -123.0,
        "end_lat": 49.01, "end_lon": -123.0})
    body = r.get_json()
    assert body["routed"] is False
    assert body["distance_m"] == 1000.0


def test_duration_none_for_a_mode_with_no_reference_speed(monkeypatch):
    monkeypatch.setattr(routing, "osrm_distance_m", lambda *a, **k: 1000.0)
    c = server.create_app().test_client()
    r = c.get("/api/route_estimate", query_string={
        "mode": "Unknown", "start_lat": 49.0, "start_lon": -123.0,
        "end_lat": 49.01, "end_lon": -123.0})
    assert r.get_json()["duration_min"] is None


def test_missing_coordinates_is_a_400(monkeypatch):
    c = _client(monkeypatch)
    r = c.get("/api/route_estimate", query_string={"mode": "Walk"})
    assert r.status_code == 400
