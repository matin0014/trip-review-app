"""Identifier grammar.

Responsibility: the content-derived flag ids (stable across replays) and the
cleaned-file `trip_id` projection (the `M`/`A`/`R`/`Q`/`S` prefixes). One shared
implementation so viewer labels, Do-file export, and cleaned file never drift.
Pure; no I/O.

A flag id is a hash of WHAT the flag is about, not of when it was made — so the
same flag gets the same id on every classification/replay (the linchpin for the
later cascade/undo work). Pair flags hash the sorted raw-row members of each leg
(member SETS, so the id survives a merge); per-trip flags hash the single row.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace

from engine.models import Flag

_ID_LEN = 16


def flag_id(flag: Flag) -> str:
    """Stable content-derived id for a flag (hex, 16 chars)."""
    if flag.trip is not None:  # per-trip flag (zero_move)
        key = (flag.flag_type, "trip", flag.trip)
    else:  # pair flag
        key = (flag.flag_type, "pair", tuple(sorted(flag.left)), tuple(sorted(flag.right)))
    return hashlib.sha1(repr(key).encode("utf-8")).hexdigest()[:_ID_LEN]


def with_ids(flags: list) -> list:
    """Return the flags with `flag_id` backfilled (frozen -> dataclasses.replace)."""
    return [replace(f, flag_id=flag_id(f)) for f in flags]


def project_trip_id(
    kind: str,
    *,
    raw_row: int = None,
    group: int = None,
    add_counter: int = None,
) -> str:
    """The cleaned-file `trip_id` for one projected row.

    Grammar `<prefix><number>`: keep ``143`` / merge ``M143`` (group = lowest raw
    row) / add ``A07`` / remove ``R145`` (Do-file only) / review-kept ``Q145``
    (missing_activity + mode_change) / split ``S150`` (reserved).
    """
    if kind == "keep":
        return str(raw_row)
    if kind == "merge":
        return f"M{group}"
    if kind == "add":
        return f"A{add_counter:02d}"
    if kind == "remove":
        return f"R{raw_row}"
    if kind == "review":
        return f"Q{raw_row}"
    if kind == "split":
        return f"S{raw_row}"
    raise ValueError(f"unknown projection kind: {kind!r}")
