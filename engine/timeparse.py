"""Robust timestamp parsing.

Responsibility: turn the raw file's time strings into timezone-aware `datetime`
objects. Pure; depends only on the standard library.

The Wave 2 raw file stores time in TWO shapes:

* **Row columns** split the moment across `start_date` (``'2024-11-05'``) and
  `start_time` (``'18:01:15.999000-08:00'``) — combine via `combine_date_time`.
* **Route triplets** carry a full ISO instant
  (``'2024-11-05T18:02:48.999000-08:00'``) — parse via `parse_ts`.

Both are tz-aware (a ``-08:00`` offset) with microseconds. `parse_ts` is
deliberately lenient: it accepts ``datetime.fromisoformat`` output directly and
repairs the couple of shapes that function rejects on Python 3.9 (a trailing
``Z``, and fractional-second precision other than 3/6 digits).
"""

from __future__ import annotations

import re
from datetime import datetime

# Matches the fractional-seconds group of an ISO time so we can normalise its
# length to the 6 digits Python 3.9's fromisoformat demands.
_FRAC_RE = re.compile(r"\.(\d+)")


def parse_ts(raw: str) -> datetime:
    """Parse one ISO-8601 instant into a timezone-aware `datetime`.

    Handles the route-triplet form directly and repairs the shapes
    `datetime.fromisoformat` rejects on 3.9 (trailing ``Z``; fractional seconds
    that are not exactly 3 or 6 digits). Raises `ValueError` if it still can't
    parse — the loader decides what to do with a bad row.
    """
    if not isinstance(raw, str):
        raise ValueError(f"timestamp is not a string: {raw!r}")
    s = raw.strip()
    if not s:
        raise ValueError("empty timestamp")

    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.fromisoformat(_normalize_iso(s))


def combine_date_time(date_str: str, time_str: str) -> datetime:
    """Combine the split `start_date`/`start_time` columns into one instant.

    The date carries no offset and the time carries the ``-08:00`` offset, so we
    splice them into a single ISO string (``<date>T<time>``) and reuse the same
    lenient parser.
    """
    if not isinstance(date_str, str) or not isinstance(time_str, str):
        raise ValueError(f"date/time not strings: {date_str!r}, {time_str!r}")
    return parse_ts(f"{date_str.strip()}T{time_str.strip()}")


def _normalize_iso(s: str) -> str:
    """Repair the ISO shapes 3.9's fromisoformat won't take: ``Z`` suffix and
    fractional seconds that aren't 3 or 6 digits (pad/truncate to microseconds)."""
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"

    def fix_frac(m: "re.Match[str]") -> str:
        digits = m.group(1)
        return "." + (digits + "000000")[:6]

    return _FRAC_RE.sub(fix_frac, s, count=1)
