"""Raw xlsx -> list[Trip].

Responsibility: THE single file that touches pandas/openpyxl. Reads a raw trip
workbook, applies `timeparse` / `normalize` / `routes`, and yields fully-built
`Trip` objects. Keeping all spreadsheet I/O here means the rest of the engine is
pure and unit-testable without a file.

`raw_row` is the trip's stable identity for the id grammar: the 1-based position
in the source file (header excluded), so the first data row is ``1``. It never
changes; the within-day `seq` is derived later, not stored here.

A row that can't yield a usable trip (unparseable timestamps, missing endpoint
coordinates) is skipped rather than aborting the load; `load_trips` reports how
many were dropped via the optional `report` out-parameter.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from engine.models import Trip
from engine.normalize import normalize_mode
from engine.routes import parse_route
from engine.timeparse import combine_date_time

REQUIRED_COLUMNS = (
    "accessCode",
    "start_date",
    "start_time",
    "end_date",
    "end_time",
    "mode_of_travel",
    "purpose_of_travel",
    "start_latitude",
    "start_longitude",
    "end_latitude",
    "end_longitude",
    "routes",
)


@dataclass
class LoadReport:
    """Counts from a load, for visibility into what was kept vs dropped."""

    total_rows: int = 0
    loaded: int = 0
    skipped: int = 0


def load_trips(path: str, report: "LoadReport | None" = None) -> list[Trip]:
    """Read a raw trip workbook into `Trip` objects (one per usable row)."""
    df = pd.read_excel(path)
    _require_columns(df)

    if report is not None:
        report.total_rows = len(df)

    trips: list[Trip] = []
    for raw_row, row in enumerate(df.itertuples(index=False), start=1):
        trip = _build_trip(raw_row, row._asdict())
        if trip is not None:
            trips.append(trip)

    if report is not None:
        report.loaded = len(trips)
        report.skipped = report.total_rows - report.loaded
    return trips


def _require_columns(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"raw file is missing required columns: {missing}")


def _build_trip(raw_row: int, cells: dict) -> "Trip | None":
    """Build one `Trip`; return None if the row lacks usable geometry/times."""
    coords = _coords(cells)
    if coords is None:
        return None
    try:
        start_ts = combine_date_time(_date(cells["start_date"]), str(cells["start_time"]))
        end_ts = combine_date_time(_date(cells["end_date"]), str(cells["end_time"]))
    except (ValueError, TypeError):
        return None

    start_lat, start_lon, end_lat, end_lon = coords
    return Trip(
        raw_row=raw_row,
        person=_text(cells["accessCode"]) or None,
        mode=normalize_mode(cells["mode_of_travel"]),
        purpose=_text(cells["purpose_of_travel"]),
        start_ts=start_ts,
        end_ts=end_ts,
        start_lat=start_lat,
        start_lon=start_lon,
        end_lat=end_lat,
        end_lon=end_lon,
        route=parse_route(cells["routes"]),
    )


def _coords(cells: dict) -> "tuple[float, float, float, float] | None":
    """Return the four endpoint coords, or None if any is missing/non-numeric."""
    try:
        vals = (
            float(cells["start_latitude"]),
            float(cells["start_longitude"]),
            float(cells["end_latitude"]),
            float(cells["end_longitude"]),
        )
    except (ValueError, TypeError):
        return None
    return None if any(math.isnan(v) for v in vals) else vals


def _date(value: object) -> str:
    """Date as ``YYYY-MM-DD``, tolerating a pandas Timestamp (drops any time part)."""
    return str(value).split("T")[0].split(" ")[0].strip()


def _text(value: object) -> str:
    """A trimmed string, with NaN/None rendered as empty."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()
