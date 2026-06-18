"""Route-cell parsing.

Responsibility: parse the raw `routes` cell into `list[RoutePoint]` and dedup
sub-second breadcrumb bursts. Pure; no I/O.

The cell is a Python-literal list of ``[lat, lon, "ISO-timestamp"]`` triplets
(single-quoted, so JSON won't take it — we use `ast.literal_eval`). Two raw
shapes are tolerated by returning an empty route: an empty list ``[]`` (89 rows),
and a value that won't parse — including the handful of cells Excel truncated at
its 32,767-char limit (truncation salvage is deferred; a truncated route simply
falls back to displacement/duration velocity downstream).

Dedup: GPS sometimes emits several points within the same second. We keep the
first point of each such burst and drop any later point less than
`params.dedup_dt_s` after the last KEPT point, so segment speeds aren't computed
across near-zero `dt`.
"""

from __future__ import annotations

import ast

from engine.models import RoutePoint
from engine.params import DEFAULT, Params
from engine.timeparse import parse_ts


def parse_route(cell: object, params: Params = DEFAULT) -> list[RoutePoint]:
    """Parse one `routes` cell into deduped `RoutePoint`s (empty on any failure)."""
    triplets = _safe_literal(cell)
    if not triplets:
        return []

    points: list[RoutePoint] = []
    for item in triplets:
        point = _to_point(item)
        if point is not None:
            points.append(point)
    return _dedup_bursts(points, params.dedup_dt_s)


def _safe_literal(cell: object) -> list:
    """`ast.literal_eval` the cell to a list; return ``[]`` on anything unexpected."""
    if not isinstance(cell, str) or not cell.strip():
        return []
    try:
        value = ast.literal_eval(cell)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return []
    return value if isinstance(value, list) else []


def _to_point(item: object) -> "RoutePoint | None":
    """Build a `RoutePoint` from one ``[lat, lon, ts]`` triplet; skip if malformed."""
    if not isinstance(item, (list, tuple)) or len(item) < 3:
        return None
    lat, lon, ts = item[0], item[1], item[2]
    try:
        return RoutePoint(float(lat), float(lon), parse_ts(ts))
    except (ValueError, TypeError):
        return None


def _dedup_bursts(points: list[RoutePoint], dedup_dt_s: float) -> list[RoutePoint]:
    """Keep the first point, then drop any point < `dedup_dt_s` after the last kept."""
    if not points:
        return []
    kept = [points[0]]
    for p in points[1:]:
        if (p.ts - kept[-1].ts).total_seconds() >= dedup_dt_s:
            kept.append(p)
    return kept
