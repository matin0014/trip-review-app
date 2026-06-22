"""Cleaned-dataset projection from the accepted review state.

Per person-day, run the live `fixpoint` to learn which flags were actually
applied, then translate those accepted flags into the cleaned-file `trip_id`
grammar (see `engine.ids.project_trip_id`):

  * untouched / waived trip      -> keep   ``"143"``
  * accepted fragment/mode_change -> merge  ``"M143"`` (every member row shares it)
  * accepted zero_move            -> remove (row dropped from the cleaned file;
                                      it is an ``R143`` entry in the Do-file only)
  * accepted missing_trip         -> an ``A``-row stub appended for the gap

The cleaned file is one row per surviving RAW row plus the materialised stubs,
with a single added `trip_id` column. `merge_wins` (in `fixpoint`/`project`) means
a zero_move on a merged piece does NOT remove it. missing_activity (reviewed) is
left as a plain keep for now — its `Q` marker is a later step. Pure given
(day_trips, accepted_ids).
"""

from __future__ import annotations

import os
from collections import defaultdict

from app import session
from app.decisions import fixpoint, replay
from engine.classify import group_person_days
from engine.ids import project_trip_id
from engine.loader import load_trips
from engine.params import DEFAULT


def clean_day(day_trips: list, accepted_ids: set, params=DEFAULT) -> tuple:
    """Return ``(rows, stubs)`` for one person-day.

    `rows` = ``[(raw_row, trip_id), ...]`` in chronological order, removed rows
    excluded. `stubs` = ``[(left_row, right_row), ...]`` missing-trip bridges to
    materialise as added rows (the caller assigns their global ``A`` numbers).
    """
    _, surfaced = fixpoint(day_trips, accepted_ids, params)
    applied = [f for f in surfaced if f.flag_id in accepted_ids]

    parent: dict = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)  # root = lowest raw row

    for f in applied:
        if f.flag_type in ("fragment", "mode_change"):
            union(f.left[0], f.right[0])

    groups: dict = defaultdict(list)
    for r in parent:
        groups[find(r)].append(r)
    merged_root = {m: root for root, members in groups.items() if len(members) > 1 for m in members}

    # merge_wins: a zero_move on a merged piece is NOT a removal.
    removed = {f.trip for f in applied if f.flag_type == "zero_move"} - set(merged_root)

    rows: list = []
    for t in sorted(day_trips, key=lambda x: x.start_ts):
        if t.raw_row in removed:
            continue  # R<row> lives in the Do-file only, not the cleaned file
        if t.raw_row in merged_root:
            rows.append((t.raw_row, project_trip_id("merge", group=merged_root[t.raw_row])))
        else:
            rows.append((t.raw_row, project_trip_id("keep", raw_row=t.raw_row)))

    stubs = [(f.left[0], f.right[0]) for f in applied if f.flag_type == "missing_trip"
             and f.left[0] not in removed and f.right[0] not in removed]
    return rows, stubs


def _write_atomic_xlsx(df, out: str) -> None:
    """Write `df` to `out` atomically (build in memory, then temp file + replace)."""
    import io
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    tmp = out + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(buf.getvalue())
    os.replace(tmp, out)


def build_cleaned_df(raw_path: str, accepted_ids: set, params=DEFAULT):
    """Read the raw workbook and return it with a `trip_id` column added and the
    removed rows dropped. raw columns are preserved verbatim; trip_id labels each
    surviving row (keep / M-merge). Skipped/unparsed raw rows stay as plain keeps.

    NOTE: missing-trip `A`-stub rows are not materialised here yet (step 3, with
    the Do-file); accepting a missing_trip currently leaves the gap unfilled in
    the cleaned file.
    """
    import pandas as pd  # lazy: keeps the pure clean_day import light

    df = pd.read_excel(raw_path).reset_index(drop=True)
    trips = load_trips(raw_path)
    tid: dict = {}
    removed: set = set()
    for day_trips in group_person_days(trips).values():
        rows, _stubs = clean_day(day_trips, accepted_ids, params)
        present = {r for r, _ in rows}
        for r, t in rows:
            tid[r] = t
        removed |= {x.raw_row for x in day_trips} - present

    n = len(df)
    df["trip_id"] = [tid.get(i + 1, str(i + 1)) for i in range(n)]  # raw_row = position + 1
    keep = [(i + 1) not in removed for i in range(n)]
    return df[keep].copy()


def generate_cleaned(sid: str, params=DEFAULT) -> str:
    """Build and atomically write ``sessions/<sid>/cleaned.xlsx``; return its path."""
    raw_name = session.read_proposals(sid)["raw_name"]
    raw_path = os.path.join(session.session_dir(sid), raw_name)
    df = build_cleaned_df(raw_path, replay(session.read_events(sid)), params)

    out = os.path.abspath(os.path.join(session.session_dir(sid), "cleaned.xlsx"))
    _write_atomic_xlsx(df, out)
    return out
