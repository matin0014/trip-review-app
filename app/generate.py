"""Cleaned-dataset projection from the accepted review state.

Per person-day, run the live `fixpoint` to learn which flags were actually
applied, then translate those accepted flags into the cleaned-file `trip_id`
grammar (see `engine.ids.project_trip_id`):

  * untouched / waived trip      -> keep   ``"143"``
  * accepted fragment/mode_change -> merge  ``"M143"`` (every member row shares it)
  * accepted zero_move            -> remove (row dropped from the cleaned file;
                                      it is an ``R143`` entry in the Do-file only)
  * accepted missing_trip         -> an ``A``-row stub appended for the gap
  * accepted (=reviewed) missing_activity -> a ``Q``-row appended for the gap
                                      (a WAIVED missing_activity leaves nothing)

The cleaned file is one row per surviving RAW row plus the materialised stub/gap
rows, with a single added `trip_id` column. `merge_wins` (in `fixpoint`/`project`)
means a zero_move on a merged piece does NOT remove it. Pure given
(day_trips, accepted_ids).
"""

from __future__ import annotations

import os
import zipfile
from collections import defaultdict

from app import session
from app.decisions import fixpoint, manual_ops, replay, resolutions
from engine.classify import group_person_days
from engine.ids import project_trip_id
from engine.loader import load_trips
from engine.params import DEFAULT
from engine.proposals import _make_stub, split_b_row


def clean_day(day_trips: list, accepted_ids: set, day_ops: list = None,
              params=DEFAULT) -> tuple:
    """Return ``(rows, extras, surfaced, overrides, purpose_overrides,
    mode_overrides)`` for one day.

    `rows` = ``[(raw_row, trip_id), ...]`` in chronological order, removed rows
    excluded. `extras` = synthesized rows the caller appends, as
    ``(kind, ref, trip)``:

      * ``"add"`` — accepted missing_trip stub OR a manual add (``A`` number
        assigned by the caller, ref None)
      * ``"review"`` — accepted=reviewed missing_activity gap (``Q<ref>``, ref =
        the trip before the gap)
      * ``"split"`` — the B-piece of a manual split (``S<ref>-B``, ref = the
        origin raw row)

    A WAIVED missing_activity is never in `accepted_ids`, so it produces no row.
    `surfaced` = the day's flags (for the Do-file's per-row flag column).
    `overrides` = ``{raw_row: live_trip}`` for split A-pieces: the origin raw row
    stays in the file as ``S<row>-A`` but its end time / mode / endpoint must be
    overwritten to the truncated A piece (so A + B do not double-count the span).
    `purpose_overrides` / `mode_overrides` = raw-row labels the reviewer changed.
    ``day_ops`` = currently-active manual edits for this day (from `manual_ops`).
    """
    day_ops = day_ops or []
    live, surfaced = fixpoint(day_trips, accepted_ids, ops=day_ops, params=params)
    applied = [f for f in surfaced if f.flag_id in accepted_ids]

    split_origins = {op["raw_row"] for op in day_ops if op.get("type") == "manual_split"}
    split_b_of = {split_b_row(op["raw_row"]): op["raw_row"]
                  for op in day_ops if op.get("type") == "manual_split"}

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

    # Manual merges: add their members to merged_root so they get M-prefix trip_ids.
    for op in (day_ops or []):
        if op.get("type") == "manual_merge":
            root = min(op["raw_rows"])
            for r in op["raw_rows"]:
                merged_root[r] = root

    # merge_wins: a zero_move on a merged piece is NOT a removal.
    removed = {f.trip for f in applied if f.flag_type == "zero_move"} - set(merged_root)

    # Manual deletes: exclude from the cleaned file (appear as R<row> in Do-file).
    for op in (day_ops or []):
        if op.get("type") == "manual_delete":
            removed.add(op["raw_row"])

    # Manual mode changes -> overwrite mode_of_travel in the cleaned file. When the
    # target row is part of a merge, every member of that group gets the new mode so
    # the shared M-row reads consistently (option b).
    group_members: dict = defaultdict(list)
    for m, root in merged_root.items():
        group_members[root].append(m)
    mode_overrides: dict = {}
    for op in day_ops:
        if op.get("type") == "manual_mode":
            r = op["raw_row"]
            for tgt in group_members.get(merged_root.get(r, r), [r]):
                mode_overrides[tgt] = op["mode"]

    live_by_row = {t.raw_row: t for t in live}
    purpose_overrides: dict = {}
    for op in day_ops:
        if op.get("type") == "manual_purpose":
            r = op["raw_row"]
            live_target = live_by_row.get(r)
            purpose = live_target.purpose if live_target is not None else op.get("purpose", "")
            for tgt in group_members.get(merged_root.get(r, r), [r]):
                if tgt > 0:
                    purpose_overrides[tgt] = purpose

    rows: list = []
    overrides: dict = {}
    for t in sorted(day_trips, key=lambda x: x.start_ts):
        if t.raw_row in removed:
            continue  # R<row> lives in the Do-file only, not the cleaned file
        if t.raw_row in split_origins:
            rows.append((t.raw_row, project_trip_id("split", raw_row=t.raw_row) + "-A"))
            a = live_by_row.get(t.raw_row)
            if a is not None:
                overrides[t.raw_row] = a  # truncate the kept row to the A piece
        elif t.raw_row in merged_root:
            rows.append((t.raw_row, project_trip_id("merge", group=merged_root[t.raw_row])))
        else:
            rows.append((t.raw_row, project_trip_id("keep", raw_row=t.raw_row)))

    # Synthesized rows from `live` (negative raw_row): a split B-piece, else an add
    # (an accepted missing_trip stub or a manual add — both materialise as A-rows).
    extras = []
    for t in live:
        if t.raw_row >= 0:
            continue
        if t.raw_row in split_b_of:
            extras.append(("split", split_b_of[t.raw_row], t))
        else:
            extras.append(("add", None, t))
    # accepted (=reviewed) missing_activity -> a Q gap row before/after the trips.
    for f in applied:
        if f.flag_type == "missing_activity":
            a, b = live_by_row.get(f.left[0]), live_by_row.get(f.right[0])
            if a is not None and b is not None:
                extras.append(("review", f.left[0], _make_stub(a, b, -1)))
    return rows, extras, surfaced, overrides, purpose_overrides, mode_overrides


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
        "routes": _route_cell(stub),
    }


def _route_cell(trip) -> str:
    if not trip.route:
        return "[]"
    return str([
        [round(p.lat, 7), round(p.lon, 7), p.ts.isoformat()]
        for p in trip.route
    ])


def _apply_split_a_overrides(df, overrides: dict) -> None:
    """Overwrite each kept split-origin row to describe the truncated A piece
    (its end time / endpoint / mode / purpose) instead of the full original trip,
    so the ``S<n>-A`` and ``S<n>-B`` rows do not double-count the span. raw_row =
    df position + 1; missing columns are skipped. The original `routes` cell is
    left intact (the A breadcrumbs are a subset of it)."""
    for raw_row, a in overrides.items():
        i = raw_row - 1
        if not (0 <= i < len(df)):
            continue
        vals = {
            "end_date": a.end_ts.strftime("%Y-%m-%d"),
            "end_time": a.end_ts.strftime("%H:%M:%S"),
            "end_latitude": a.end_lat, "end_longitude": a.end_lon,
            "mode_of_travel": a.mode, "purpose_of_travel": a.purpose,
        }
        for col, v in vals.items():
            if col in df.columns:
                df.at[i, col] = v


def _apply_mode_overrides(df, mode_overrides: dict) -> None:
    """Overwrite ``mode_of_travel`` for rows whose mode the reviewer changed by
    hand (raw_row = df position + 1; out-of-range rows skipped)."""
    if "mode_of_travel" not in df.columns:
        return
    for raw_row, mode in mode_overrides.items():
        i = raw_row - 1
        if 0 <= i < len(df):
            df.at[i, "mode_of_travel"] = mode


def _apply_purpose_overrides(df, purpose_overrides: dict) -> None:
    """Overwrite ``purpose_of_travel`` for rows whose purpose the reviewer changed
    by hand (raw_row = df position + 1; out-of-range rows skipped)."""
    if "purpose_of_travel" not in df.columns:
        return
    for raw_row, purpose in purpose_overrides.items():
        i = raw_row - 1
        if 0 <= i < len(df):
            df.at[i, "purpose_of_travel"] = purpose


def project_dataset(raw_path: str, accepted_ids: set, params=DEFAULT, res_map=None,
                    all_ops: list = None) -> tuple:
    """Project the whole dataset once: ``(tid, removed, extras, flag_by_row,
    overrides, purpose_overrides, mode_overrides)``.

    `tid` = {raw_row: trip_id} for surviving loaded trips (keep / M-merge /
    ``S<row>-A`` split origin); `removed` = raw rows dropped by an accepted
    zero_move or manual delete; `extras` = the add/review/split synthesized rows;
    `overrides` = {raw_row: live A trip} columns to overwrite on kept split rows;
    `mode_overrides` = {raw_row: mode} from manual mode changes. When `res_map`
    (flag_id -> resolution) is given, `flag_by_row` maps raw_row ->
    ["<type>:<resolution>", ...] for the Do-file; otherwise it is empty. Shared by
    the cleaned xlsx and the Do-file so they never disagree. ``all_ops`` is the
    full ordered list of active manual ops from `decisions.manual_ops`; filtered
    per day here.
    """
    tid: dict = {}
    removed: set = set()
    extras: list = []
    flag_by_row: dict = defaultdict(list)
    overrides: dict = {}
    purpose_overrides: dict = {}
    mode_overrides: dict = {}
    for (person, date), day_trips in group_person_days(load_trips(raw_path)).items():
        day_key = f"{person}|{date}"
        day_ops = [op for op in (all_ops or []) if op.get("day_key") == day_key]
        rows, day_extras, surfaced, day_overrides, day_purpose_overrides, day_mode_overrides = clean_day(
            day_trips, accepted_ids, day_ops=day_ops, params=params)
        present = {r for r, _ in rows}
        for r, t in rows:
            tid[r] = t
        removed |= {x.raw_row for x in day_trips} - present
        extras += day_extras
        overrides.update(day_overrides)
        purpose_overrides.update(day_purpose_overrides)
        mode_overrides.update(day_mode_overrides)
        if res_map is not None:
            for f in surfaced:
                tag = f"{f.flag_type}:{res_map.get(f.flag_id, 'pending')}"
                touched = [f.trip] if f.trip is not None else list(f.left) + list(f.right)
                for r in touched:
                    flag_by_row[r].append(tag)
    return tid, removed, extras, flag_by_row, overrides, purpose_overrides, mode_overrides


def build_cleaned_df(raw_path: str, accepted_ids: set, params=DEFAULT,
                     all_ops: list = None):
    """Read the raw workbook and return it with a `trip_id` column added, removed
    rows dropped, and accepted missing-trip/reviewed-activity rows appended as
    ``A``/``Q`` rows. raw columns are preserved verbatim. Skipped/unparsed raw
    rows stay as plain keeps.
    """
    import pandas as pd  # lazy: keeps the pure clean_day import light

    df = pd.read_excel(raw_path).reset_index(drop=True)
    tid, removed, extras, _, overrides, purpose_overrides, mode_overrides = project_dataset(
        raw_path, accepted_ids, params, all_ops=all_ops)
    _apply_split_a_overrides(df, overrides)  # truncate kept split rows to their A piece
    _apply_purpose_overrides(df, purpose_overrides)  # hand-edited trip purposes
    _apply_mode_overrides(df, mode_overrides)  # hand-edited travel modes

    n = len(df)
    df["trip_id"] = [tid.get(i + 1, str(i + 1)) for i in range(n)]  # raw_row = position + 1
    keep = [(i + 1) not in removed for i in range(n)]
    cleaned = df[keep].copy()

    if extras:  # append synthesized rows: A = missing-trip stubs, Q = reviewed gaps
        cols = list(cleaned.columns)
        added = []
        for trip_id, _kind, trip in _extra_ids(extras):
            row = {c: None for c in cols}
            row.update(_stub_row(trip))
            row["trip_id"] = trip_id
            added.append(row)
        cleaned = pd.concat([cleaned, pd.DataFrame(added, columns=cols)], ignore_index=True)
    return cleaned


def _extra_ids(extras: list) -> list:
    """Assign trip_ids to synthesized rows: ``A<n>`` for adds (sequential),
    ``Q<ref>`` for reviewed gaps, ``S<ref>-B`` for split B-pieces. Shared so
    cleaned.xlsx and the Do-file agree."""
    out = []
    add_n = 0
    for kind, ref, trip in extras:
        if kind == "add":
            add_n += 1
            out.append((project_trip_id("add", add_counter=add_n), kind, trip))
        elif kind == "split":
            out.append((project_trip_id("split", raw_row=ref) + "-B", kind, trip))
        else:
            out.append((project_trip_id("review", raw_row=ref), kind, trip))
    return out


_STATUS = {"M": "merged", "R": "removed", "A": "added", "Q": "reviewed-gap", "S": "split"}


def _status_of(trip_id: str) -> str:
    return _STATUS.get(trip_id[:1], "kept")   # plain "143" keep ids fall through


_EXTRA_FLAG = {"add": "missing_trip:accept", "review": "missing_activity:reviewed",
               "split": "manual_split"}


def build_dofile_df(raw_path: str, accepted_ids: set, res_map: dict, params=DEFAULT,
                    all_ops: list = None):
    """The Do-file ledger as a DataFrame: EVERY loaded raw row (removed ones kept
    and marked ``R<row>``, unlike the cleaned file which drops them) plus the
    synthesized A/Q rows, with `trip_id` + `status` + `flags` columns added to the
    raw columns. `flags` lists each flag the row received and its resolution
    (``"<type>:<resolution>"``). The full audit record; same projection as cleaned.
    """
    import pandas as pd

    df = pd.read_excel(raw_path).reset_index(drop=True)
    tid, removed, extras, flag_by_row, overrides, purpose_overrides, mode_overrides = project_dataset(
        raw_path, accepted_ids, params, res_map, all_ops=all_ops)
    _apply_split_a_overrides(df, overrides)  # truncate kept split rows to their A piece
    _apply_purpose_overrides(df, purpose_overrides)  # hand-edited trip purposes
    _apply_mode_overrides(df, mode_overrides)  # hand-edited travel modes

    def row_id(i):
        r = i + 1
        if r in removed:
            return project_trip_id("remove", raw_row=r)  # R<row> — kept in the Do-file
        return tid.get(r, str(r))

    n = len(df)
    df["trip_id"] = [row_id(i) for i in range(n)]
    df["status"] = [_status_of(t) for t in df["trip_id"]]
    df["flags"] = ["; ".join(flag_by_row.get(i + 1, [])) for i in range(n)]

    if extras:
        cols = list(df.columns)
        added = []
        for trip_id, kind, trip in _extra_ids(extras):
            row = {c: None for c in cols}
            row.update(_stub_row(trip))
            row["trip_id"] = trip_id
            row["status"] = _status_of(trip_id)   # A->added, Q->reviewed-gap, S->split
            row["flags"] = _EXTRA_FLAG[kind]
            added.append(row)
        df = pd.concat([df, pd.DataFrame(added, columns=cols)], ignore_index=True)
    return df


def generate_cleaned_and_dofile(sid: str, params=DEFAULT) -> tuple:
    """Atomically write cleaned.xlsx + dofile.xlsx; return (cleaned, dofile) paths."""
    raw_path = os.path.join(session.session_dir(sid), session.read_proposals(sid)["raw_name"])
    events = session.read_events(sid)
    accepted, res_map = replay(events), resolutions(events)
    ops = manual_ops(events)

    cleaned = os.path.abspath(os.path.join(session.session_dir(sid), "cleaned.xlsx"))
    dofile = os.path.abspath(os.path.join(session.session_dir(sid), "dofile.xlsx"))
    _write_atomic_xlsx(build_cleaned_df(raw_path, accepted, params, all_ops=ops), cleaned)
    _write_atomic_xlsx(build_dofile_df(raw_path, accepted, res_map, params, all_ops=ops), dofile)
    return cleaned, dofile


def generate_bundle(sid: str, params=DEFAULT) -> str:
    """Write cleaned.xlsx + dofile.xlsx and zip them; return the zip path."""
    cleaned, dofile = generate_cleaned_and_dofile(sid, params)
    out = os.path.abspath(os.path.join(session.session_dir(sid), "export.zip"))
    tmp = out + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(cleaned, "cleaned.xlsx")
        z.write(dofile, "dofile.xlsx")
    os.replace(tmp, out)
    return out
