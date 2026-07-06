"""Unit tests for app.regions: the persisted study-area boxes + out-of-region stats."""

from __future__ import annotations

from types import SimpleNamespace

from app import regions

# Two boxes ~ the defaults: Vancouver + Central Okanagan.
VAN = {"name": "Van", "sw": [48.9, -123.5], "ne": [49.5, -122.3], "color": "#1558d6"}
OKA = {"name": "Oka", "sw": [49.6, -119.9], "ne": [50.5, -118.9], "color": "#d9480f"}
BOXES = [VAN, OKA]


def t(slat, slon, elat, elon):
    return SimpleNamespace(start_lat=slat, start_lon=slon, end_lat=elat, end_lon=elon)


def test_outside_all_respects_every_box():
    assert not regions.outside_all(BOXES, 49.28, -123.12)   # in Vancouver
    assert not regions.outside_all(BOXES, 49.88, -119.50)   # in Central Okanagan
    assert regions.outside_all(BOXES, 47.61, -122.34)       # Seattle -> outside both


def test_stats_counts_endpoint_outside_days_and_their_trips():
    days = {
        "p|d1": [t(49.28, -123.12, 49.30, -123.10),         # both endpoints in Van
                 t(49.30, -123.10, 49.26, -123.05)],         # in Van
        "p|d2": [t(49.28, -123.12, 47.61, -122.34),          # ends in Seattle -> outside
                 t(47.61, -122.34, 49.28, -123.12)],         # starts in Seattle
        "q|d1": [t(49.88, -119.50, 50.05, -119.41)],         # Okanagan, in-region
    }
    s = regions.stats(days, BOXES)
    assert s["total_days"] == 3 and s["total_trips"] == 5
    assert s["days"] == 1                                    # only p|d2 qualifies
    assert s["trips"] == 2                                   # both of p|d2's trips removed


def test_out_of_region_days_returns_the_offending_keys():
    days = {
        "p|d1": [t(49.28, -123.12, 49.30, -123.10)],         # in Van
        "p|d2": [t(49.28, -123.12, 47.61, -122.34)],          # ends in Seattle -> out
        "q|d1": [t(49.88, -119.50, 50.05, -119.41)],          # Okanagan, in-region
        "q|d2": [t(47.61, -122.34, 49.28, -123.12)],          # starts in Seattle -> out
    }
    assert regions.out_of_region_days(days, BOXES) == {"p|d2", "q|d2"}


def test_clean_sorts_swapped_bounds_and_coerces_floats():
    raw = {"name": "x", "sw": ["49.5", "-122.3"], "ne": ["48.9", "-123.5"]}  # corners swapped
    c = regions._clean(raw)
    assert c["sw"] == [48.9, -123.5] and c["ne"] == [49.5, -122.3]


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(regions, "_PATH", str(tmp_path / "regions.json"))
    saved = regions.save_regions(BOXES)
    assert regions.load_regions() == saved


def test_load_falls_back_to_defaults_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(regions, "_PATH", str(tmp_path / "missing.json"))
    assert regions.load_regions() == regions.DEFAULT_REGIONS
