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
    """Return ``(rows, stub_trips)`` for one person-day.

    `rows` = ``[(raw_row, trip_id), ...]`` in chronological order, removed rows
    excluded. `stub_trips` = the materialised missing-trip stub `Trip`s (negative
    raw_row) the caller turns into ``A`` rows (assigning their global numbers).
    """
    live, surfaced = fixpoint(day_trips, accepted_ids, params)
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

    # fixpoint already inserted accepted missing_trip stubs (negative raw_row) with
    # correct A.end->B.start bridging (even across merges) — just hand them back.
    stub_trips = [t for t in live if t.raw_row < 0]
    return rows, stub_trips


def _write_atomic_xlsx(df, out: str) -> None:
    """Write `df` to `out` atomically (build in memory, then temp file + replace)."""
    import io
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    tmp = out + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(buf.getvalue())
    os.replace(tmp, out)


def _stub_row(stub) -> dict:
    """Map a stub `Trip` onto the raw column names (others left blank)."""
    return {
        "accessCode": stub.person,
        "start_date": stub.start_ts.strftime("%Y-%m-%d"),
        "start_time": stub.start_ts.strftime("%H:%M:%S"),
        "end_date": stub.end_ts.strftime("%Y-%m-%d"),
        "end_time": stub.end_ts.strftime("%H:%M:%S"),
        "mode_of_travel": stub.mode,
        "purpose_of_travel": stub.purpose,
        "start_latitude": stub.start_lat, "start_longitude": stub.start_lon,
        "end_latitude": stub.end_lat, "end_longitude": stub.end_lon,
        "routes": "[]",
    }


def build_cleaned_df(raw_path: str, accepted_ids: set, params=DEFAULT):
    """Read the raw workbook and return it with a `trip_id` column added, removed
    rows dropped, and accepted missing-trip stubs appended as ``A`` rows. raw
    columns are preserved verbatim; trip_id labels each row (keep / M-merge / A-add).
    Skipped/unparsed raw rows stay as plain keeps.
    """
    import pandas as pd  # lazy: keeps the pure clean_day import light

    df = pd.read_excel(raw_path).reset_index(drop=True)
    trips = load_trips(raw_path)
    tid: dict = {}
    removed: set = set()
    stub_trips: list = []
    for day_trips in group_person_days(trips).values():
        rows, stubs = clean_day(day_trips, accepted_ids, params)
        present = {r for r, _ in rows}
        for r, t in rows:
            tid[r] = t
        removed |= {x.raw_row for x in day_trips} - present
        stub_trips += stubs

    n = len(df)
    df["trip_id"] = [tid.get(i + 1, str(i + 1)) for i in range(n)]  # raw_row = position + 1
    keep = [(i + 1) not in removed for i in range(n)]
    cleaned = df[keep].copy()

    if stub_trips:  # materialise accepted missing-trip stubs as trailing A-rows
        cols = list(cleaned.columns)
        added = []
        for i, stub in enumerate(stub_trips, start=1):
            row = {c: None for c in cols}
            row.update(_stub_row(stub))
            row["trip_id"] = project_trip_id("add", add_counter=i)
            added.append(row)
        cleaned = pd.concat([cleaned, pd.DataFrame(added, columns=cols)], ignore_index=True)
    return cleaned


def generate_cleaned(sid: str, params=DEFAULT) -> str:
    """Build and atomically write ``sessions/<sid>/cleaned.xlsx``; return its path."""
    raw_name = session.read_proposals(sid)["raw_name"]
    raw_path = os.path.join(session.session_dir(sid), raw_name)
    df = build_cleaned_df(raw_path, replay(session.read_events(sid)), params)

    out = os.path.abspath(os.path.join(session.session_dir(sid), "cleaned.xlsx"))
    _write_atomic_xlsx(df, out)
    return out
