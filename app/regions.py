"""Study-area region boxes: the single persisted source of truth.

Both the map overlay and the out-of-region day/trip counts read these boxes, so
editing + locking them in the visualizer keeps the drawing and the analysis in
agreement. App-level on purpose (it does file I/O and is mutable config); the
engine's own `loader._REGION_BOXES` (used to drop bad home/work geocodes) is a
separate, fixed engine constant by design, because `engine/` stays pure and never
reads app config.

A box is ``{"name", "sw": [lat_lo, lon_lo], "ne": [lat_hi, lon_hi], "color"}``.
A point is "outside" when it is in NONE of the boxes; a person-day is counted when
any of its trips has a START or END endpoint outside every box.
"""

from __future__ import annotations

import json
import os

_PATH = os.environ.get("TRIP_REGIONS_FILE", "regions.json")

DEFAULT_REGIONS = [
    {"name": "Vancouver area",   "sw": [49.0, -123.29], "ne": [49.39, -122.18], "color": "#1558d6"},
    {"name": "Central Okanagan", "sw": [49.73, -119.79], "ne": [50.51, -119.15], "color": "#d9480f"},
]


def load_regions() -> list:
    """The locked-in boxes, or a copy of the defaults when none are saved yet."""
    try:
        with open(_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list) and data:
            return [_clean(r) for r in data]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return [dict(r) for r in DEFAULT_REGIONS]


def save_regions(regions: list) -> list:
    """Persist boxes atomically (temp + replace); returns the cleaned boxes."""
    clean = [_clean(r) for r in regions]
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(clean, fh, indent=2)
    os.replace(tmp, _PATH)
    return clean


def _clean(r: dict) -> dict:
    """Coerce one box to the canonical shape (floats, lat<=lat, lon<=lon)."""
    sw_lat, ne_lat = sorted((float(r["sw"][0]), float(r["ne"][0])))
    sw_lon, ne_lon = sorted((float(r["sw"][1]), float(r["ne"][1])))
    return {
        "name": str(r.get("name", "region")),
        "sw": [sw_lat, sw_lon], "ne": [ne_lat, ne_lon],
        "color": str(r.get("color", "#1558d6")),
    }


def _inside(box: dict, lat: float, lon: float) -> bool:
    return (box["sw"][0] <= lat <= box["ne"][0]
            and box["sw"][1] <= lon <= box["ne"][1])


def outside_all(regions: list, lat: float, lon: float) -> bool:
    """True when the point lies in none of the boxes."""
    return not any(_inside(b, lat, lon) for b in regions)


def stats(days: dict, regions: list) -> dict:
    """Count person-days (and their trips) with a START/END endpoint outside every
    box. `days` is ``{day_key: [Trip, ...]}`` (e.g. from `app.state.all_days`)."""
    out_days = out_trips = total_trips = 0
    for day_trips in days.values():
        total_trips += len(day_trips)
        hit = any(outside_all(regions, t.start_lat, t.start_lon)
                  or outside_all(regions, t.end_lat, t.end_lon) for t in day_trips)
        if hit:
            out_days += 1
            out_trips += len(day_trips)
    return {"days": out_days, "trips": out_trips,
            "total_days": len(days), "total_trips": total_trips}
