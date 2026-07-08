"""Header KPIs and the regional overview cards (top of the dashboard).

Small, separate from the nine indicators because it is page furniture, not a chart:
the five summary tiles (total valid trips, unique participants, full-week coverage,
avg trips/person/day, survey period) plus the per-region overview card (trips,
participants, trips/person/day, full-week %). Each reported for raw and cleaned.
"""

from __future__ import annotations

from .pipeline import Dataset


def header_kpis(ds: Dataset) -> dict:
    """The five top-of-page tiles, raw vs cleaned: total valid trips, unique
    participants, full-week-coverage count, avg trips/person/day, survey period
    (min..max trip date)."""
    raise NotImplementedError


def regional_overview(ds: Dataset) -> dict:
    """Per-region card values (trips, participants, trips/person/day, full-week %),
    raw vs cleaned, one entry per label in ``pipeline.REGIONS``."""
    raise NotImplementedError
