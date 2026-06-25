"""Extract home/work/school map buffers from coordinate columns in a workbook."""

from __future__ import annotations

import math

import pandas as pd

RADIUS_M = 300
PLACE_SPECS = (
    ("home", "Home", "streetAddress", "home_latitude", "home_longitude", "#2ca25f"),
    ("work", "Work", "workAddress", "work_latitude", "work_longitude", "#756bb1"),
    ("school", "School", "schoolAddress", "school_latitude", "school_longitude", "#de8f05"),
)


def extract_places(path: str) -> dict:
    """Return {person: [place, ...]} for rows with coordinate columns.

    The upload can still be a raw workbook without these columns; in that case we
    return an empty mapping and the UI simply has no buffers to draw.
    """
    df = pd.read_excel(path)
    if "accessCode" not in df.columns:
        return {}
    if not any(lat in df.columns and lon in df.columns for *_x, lat, lon, _c in PLACE_SPECS):
        return {}

    places_by_person: dict = {}
    seen: set = set()
    for row in df.itertuples(index=False):
        cells = row._asdict()
        person = _text(cells.get("accessCode")) or "(unknown)"
        for kind, label, address_col, lat_col, lon_col, color in PLACE_SPECS:
            lat, lon = _num(cells.get(lat_col)), _num(cells.get(lon_col))
            if lat is None or lon is None:
                continue
            address = _text(cells.get(address_col))
            key = (person, kind, round(lat, 6), round(lon, 6), address.casefold())
            if key in seen:
                continue
            seen.add(key)
            places_by_person.setdefault(person, []).append({
                "type": kind,
                "label": label,
                "address": address,
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "radius_m": RADIUS_M,
                "color": color,
            })
    return places_by_person


def _num(value: object):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) else out


def _text(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text
