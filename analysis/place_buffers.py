"""Create a Leaflet map of home/work/school 300 m buffers.

Usage:
    python analysis/place_buffers.py [coordinates_xlsx]

The input workbook should include the coordinate columns produced by
`add_convert.py`: home/work/school latitude and longitude. The output is a
standalone HTML map under analysis/output/.
"""

from __future__ import annotations

import html
import json
import math
import os
import sys
from typing import Optional

import pandas as pd

DEFAULT_INPUT = (
    r"P:\UiTR\Shared Folder\1. Current Student\Matin\Smartphone app data"
    r"\trip-data-refinement\Socio Demographics\Wave2 data with SD_coordinates.xlsx"
)
OUT = os.path.join("analysis", "output", "place_buffers_map.html")
RADIUS_M = 300
PLACE_SPECS = (
    ("home", "Home", "streetAddress", "home_latitude", "home_longitude", "#2ca25f"),
    ("work", "Work", "workAddress", "work_latitude", "work_longitude", "#756bb1"),
    ("school", "School", "schoolAddress", "school_latitude", "school_longitude", "#de8f05"),
)


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INPUT
    df = pd.read_excel(path)
    places = _places(df)
    out = _write_map(places)

    print(f"read: {path}")
    print(f"unique buffers: {len(places):,}")
    for kind, label, *_ in PLACE_SPECS:
        print(f"{label.lower()}: {sum(1 for p in places if p['type'] == kind):,}")
    print(f"wrote: {out}")


def _places(df: pd.DataFrame) -> list[dict]:
    places: list[dict] = []
    seen: set[tuple] = set()
    for _, row in df.iterrows():
        person = _text(row.get("accessCode")) or "(unknown)"
        for kind, label, address_col, lat_col, lon_col, color in PLACE_SPECS:
            lat, lon = _num(row.get(lat_col)), _num(row.get(lon_col))
            if lat is None or lon is None:
                continue
            address = _text(row.get(address_col))
            key = (person, kind, round(lat, 6), round(lon, 6), address.casefold())
            if key in seen:
                continue
            seen.add(key)
            places.append({
                "person": person,
                "type": kind,
                "label": label,
                "address": address,
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "color": color,
                "radius_m": RADIUS_M,
            })
    return places


def _write_map(places: list[dict]) -> str:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    center = _center(places)
    payload = json.dumps(places, ensure_ascii=False)
    legend = "\n".join(
        f'<div><span class="swatch" style="background:{color}"></span>{html.escape(label)}</div>'
        for _, label, *_cols, color in PLACE_SPECS
    )
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Home, Work, and School 300 m Buffers</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
html, body, #map {{ height: 100%; margin: 0; }}
.legend {{
  background: white;
  border: 1px solid #c8ced8;
  border-radius: 6px;
  box-shadow: 0 2px 10px rgba(0,0,0,.12);
  color: #1f2937;
  font: 14px/1.4 Arial, sans-serif;
  padding: 10px 12px;
}}
.legend h1 {{ font-size: 14px; margin: 0 0 8px; }}
.legend div {{ display: flex; align-items: center; gap: 8px; margin: 4px 0; }}
.swatch {{ display: inline-block; width: 14px; height: 14px; border-radius: 3px; opacity: .85; }}
.count {{ color: #64748b; font-size: 12px; margin-top: 8px; }}
</style>
</head>
<body>
<div id="map"></div>
<script>
const places = {payload};
const map = L.map('map', {{ zoomControl: true }}).setView([{center[0]}, {center[1]}], 11);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap'
}}).addTo(map);

const bounds = [];
for (const p of places) {{
  const ll = [p.lat, p.lon];
  bounds.push(ll);
  L.circle(ll, {{
    radius: p.radius_m,
    color: p.color,
    weight: 2,
    fillColor: p.color,
    fillOpacity: 0.13
  }}).addTo(map).bindPopup(
    `<b>${{p.label}}</b><br>` +
    `Person: ${{escapeHtml(p.person)}}<br>` +
    `${{escapeHtml(p.address || '')}}<br>` +
    `${{p.radius_m}} m buffer`
  );
}}
if (bounds.length) map.fitBounds(bounds, {{ padding: [30, 30] }});

const legend = L.control({{ position: 'topright' }});
legend.onAdd = function() {{
  const div = L.DomUtil.create('div', 'legend');
  div.innerHTML = `<h1>300 m buffers</h1>{legend}<div class="count">${{places.length.toLocaleString()}} unique buffers</div>`;
  return div;
}};
legend.addTo(map);

function escapeHtml(value) {{
  return String(value).replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;', "'":'&#39;'}}[c]));
}}
</script>
</body>
</html>
"""
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(document)
    return OUT


def _center(places: list[dict]) -> tuple[float, float]:
    if not places:
        return 49.26, -123.1
    return (
        sum(p["lat"] for p in places) / len(places),
        sum(p["lon"] for p in places) / len(places),
    )


def _num(value: object) -> Optional[float]:
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


if __name__ == "__main__":
    main()
