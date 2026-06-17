"""Identifier grammar.

Responsibility: the content-derived flag ids (stable across replays) and the
cleaned-file `trip_id` projection (the `M`/`A`/`R`/`Q`/`S` prefixes). One shared
implementation so viewer labels, Do-file export, and cleaned file never drift.
Pure; no I/O.

TODO (Step 2): implement `flag_id(...)` and `project_trip_id(...)`.
"""

from __future__ import annotations
