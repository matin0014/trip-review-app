"""Domain data types — plain holders, no logic.

These dataclasses are the vocabulary the rest of the engine speaks in. They carry
data only; all computation lives in the modules that operate on them (`geo`,
`velocity`, `classify`, ...). Keeping them logic-free is what stops a `Trip` from
turning into a god object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class RoutePoint:
    """One GPS breadcrumb: position + timestamp. Immutable."""

    lat: float
    lon: float
    ts: datetime


@dataclass
class Trip:
    """A single recorded trip (one raw row), plus its parsed route breadcrumbs.

    `raw_row` is the stable source-row identity used throughout the id grammar
    (it never changes; the within-day `seq` is derived elsewhere and not stored
    here). Synthesized trips (added stubs) use a negative/sentinel `raw_row` set
    by the loader/proposals layer; the route is empty for those.
    """

    raw_row: int
    person: str | None
    mode: str
    purpose: str
    start_ts: datetime
    end_ts: datetime
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    route: list[RoutePoint] = field(default_factory=list)
