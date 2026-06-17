"""Route-cell parsing.

Responsibility: parse the raw `routes` cell (single-quoted JSON of
`[lat, lon, "ISO-timestamp"]` triplets) into `list[RoutePoint]`, and dedup
sub-second breadcrumb bursts (`dt < params.dedup_dt_s`). Pure; no I/O.

TODO (Step 1b): implement `parse_route(cell: str, params) -> list[RoutePoint]`.
"""

from __future__ import annotations
