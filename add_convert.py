"""Append home/work/school coordinates to a workbook.

Usage:
    python add_convert.py [input_xlsx]

The script reads address columns from the input workbook, adds coordinate columns,
and writes a new workbook beside the input. Home coordinates are parsed from
`streetAddressCoordinate` when available; otherwise addresses are geocoded with
ArcGIS's public geocoder. Geocode results are cached locally.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
from typing import Optional

import pandas as pd

DEFAULT_INPUT = (
    r"P:\UiTR\Shared Folder\1. Current Student\Matin\Smartphone app data"
    r"\trip-data-refinement\Socio Demographics\Wave2 data with SD.xlsx"
)
CACHE_PATH = os.path.join("analysis", "output", "arcgis_address_geocode_cache.json")
ARCGIS_URL = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"
SKIP_ADDRESS_WORDS = (
    "n/a",
    "home",
    "different location",
    "sites vary",
    "varies by day",
    "all around",
)


def main() -> None:
    input_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INPUT
    output_path = _output_path(input_path)

    df = pd.read_excel(input_path)
    cache = _read_cache(CACHE_PATH)

    df["home_latitude"] = None
    df["home_longitude"] = None
    df["work_latitude"] = None
    df["work_longitude"] = None
    df["school_latitude"] = None
    df["school_longitude"] = None

    home_hits = 0
    for idx, row in df.iterrows():
        home = _home_latlon(row)
        if home is None:
            home = _geocode(_home_address(row), cache)
        if home is not None:
            df.at[idx, "home_latitude"] = home[0]
            df.at[idx, "home_longitude"] = home[1]
            home_hits += 1

        work = _geocode(_clean_address(row.get("workAddress")), cache)
        if work is not None:
            df.at[idx, "work_latitude"] = work[0]
            df.at[idx, "work_longitude"] = work[1]

        school = _geocode(_clean_address(row.get("schoolAddress")), cache)
        if school is not None:
            df.at[idx, "school_latitude"] = school[0]
            df.at[idx, "school_longitude"] = school[1]

    _write_cache(CACHE_PATH, cache)
    df.to_excel(output_path, index=False)

    print(f"read: {input_path}")
    print(f"rows: {len(df):,}")
    print(f"home coordinates: {home_hits:,}")
    print(f"work coordinates: {df['work_latitude'].notna().sum():,}")
    print(f"school coordinates: {df['school_latitude'].notna().sum():,}")
    print(f"wrote: {output_path}")
    print(f"cache: {CACHE_PATH}")


def _output_path(path: str) -> str:
    root, ext = os.path.splitext(path)
    return f"{root}_coordinates{ext}"


def _home_latlon(row: pd.Series) -> Optional[tuple[float, float]]:
    coord = _text(row.get("streetAddressCoordinate"))
    if not coord:
        return None
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)", coord)
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


def _home_address(row: pd.Series) -> str:
    street = _clean_address(row.get("streetAddress"))
    if not street:
        return ""
    city = _text(row.get("city"))
    postal = _text(row.get("postalCode"))
    parts = [street]
    if city and city.casefold() not in street.casefold():
        parts.append(city)
    if postal and postal.casefold() not in street.casefold():
        parts.append(postal)
    if "canada" not in street.casefold():
        parts.append("BC, Canada")
    return ", ".join(parts)


def _clean_address(value: object) -> str:
    text = _text(value)
    if not text:
        return ""
    text = next((part.strip() for part in text.split("|") if part.strip()), "")
    text = re.sub(r"\s+", " ", text).strip(" ,")
    if not text or text.casefold() in SKIP_ADDRESS_WORDS:
        return ""
    if any(word in text.casefold() for word in SKIP_ADDRESS_WORDS):
        return ""
    if text and "canada" not in text.casefold():
        text = f"{text}, BC, Canada"
    return text


def _geocode(address: str, cache: dict) -> Optional[tuple[float, float]]:
    if not address:
        return None
    if address in cache:
        value = cache[address]
        return None if value is None else (value["lat"], value["lon"])

    query = urllib.parse.urlencode({"SingleLine": address, "f": "json", "maxLocations": 1})
    req = urllib.request.Request(f"{ARCGIS_URL}?{query}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"geocode failed: {address} ({exc})")
        return None

    candidates = data.get("candidates", [])
    if not candidates:
        print(f"geocode not found: {address}")
        cache[address] = None
        return None

    loc = candidates[0]["location"]
    latlon = {"lat": float(loc["y"]), "lon": float(loc["x"])}
    cache[address] = latlon
    return latlon["lat"], latlon["lon"]


def _read_cache(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _write_cache(path: str, cache: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _text(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


if __name__ == "__main__":
    main()
