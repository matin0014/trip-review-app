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
from engine.proposals import _apply_add, _make_stub, add_row, split_b_row

PIPE_FIRST_COLUMNS = {
    "region", "_returnRespondent", "streetAddress", "city", "postalCode",
    "totalPeople", "homeType", "ownerStatus", "grossIncome",
    "numberOfVehicles", "streetAddressCoordinate", "age", "gender",
    "maritalStatus", "hasLicense", "hasTransitPass", "educationLevel",
    "employmentStatus", "occupationCategory", "workArrangement", "workMode",
    "personalIncome", "workAddress", "schoolAddress",
}

ADDED_ROW_INHERIT_COLUMNS = {
    "region", "_returnRespondent", "streetAddress", "city", "postalCode",
    "totalPeople", "homeType", "ownerStatus", "grossIncome",
    "numberOfVehicles", "streetAddressCoordinate", "age", "gender",
    "maritalStatus", "hasLicense", "hasTransitPass", "educationLevel",
    "employmentStatus", "occupationCategory", "workArrangement", "workMode",
    "personalIncome", "workAddress", "schoolAddress",
    "home_latitude", "home_longitude", "work_latitude", "work_longitude",
    "school_latitude", "school_longitude",
}


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
    canonical_group_members: dict = defaultdict(list)
    for m, root in merged_root.items():
        canonical_group_members[root].append(m)

    export_root: dict = {}
    for root, members in canonical_group_members.items():
        positives = [m for m in members if m > 0]
        export_root[root] = min(positives) if positives else root
    for m, root in list(merged_root.items()):
        merged_root[m] = export_root.get(root, root)

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
    manual_added_by_row: dict = {}
    add_n = 0
    for op in day_ops:
        if op.get("type") != "manual_add":
            continue
        add_n += 1
        row = add_row(add_n)
        added = [t for t in _apply_add(day_trips, op, add_n) if t.raw_row == row]
        if added:
            manual_added_by_row[row] = added[0]
    merge_overrides: dict = {}
    for root, members in canonical_group_members.items():
        out_root = export_root.get(root, root)
        merged = live_by_row.get(root) or live_by_row.get(out_root)
        if merged is not None and out_root > 0:
            merge_overrides[out_root] = merged
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
            if any(op.get("type") == "manual_time" and op.get("raw_row") == t.raw_row for op in day_ops):
                edited = live_by_row.get(t.raw_row)
                if edited is not None:
                    overrides[t.raw_row] = edited

    # Synthesized rows from `live` (negative raw_row): a split B-piece, else an add
    # (an accepted missing_trip stub or a manual add — both materialise as A-rows).
    # A manual add merged into a positive source row is dofile-only: cleaned.xlsx
    # stays collapsed, while dofile.xlsx keeps the added row's geometry.
    extras = []
    for t in live:
        if t.raw_row >= 0:
            continue
        if t.raw_row in export_root and export_root[t.raw_row] > 0:
            extras.append(("merged_add", None, manual_added_by_row.get(t.raw_row, t)))
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
    overrides.update(merge_overrides)
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
        "start_weekday": stub.start_ts.strftime("%A"),
        "start_date": stub.start_ts.strftime("%Y-%m-%d"),
        "start_time": stub.start_ts.strftime("%H:%M:%S"),
        "end_weekday": stub.end_ts.strftime("%A"),
        "end_date": stub.end_ts.strftime("%Y-%m-%d"),
        "end_time": stub.end_ts.strftime("%H:%M:%S"),
        "mode_of_travel": stub.mode,
        "purpose_of_travel": stub.purpose,
        "start_latitude": stub.start_lat, "start_longitude": stub.start_lon,
        "end_latitude": stub.end_lat, "end_longitude": stub.end_lon,
        "routes": _route_cell(stub),
        "time_duration": _duration_cell(stub),
    }


def _duration_cell(trip) -> str:
    return str(trip.end_ts - trip.start_ts)


def _route_cell(trip) -> str:
    if not trip.route:
        return "[]"
    return str([
        [round(p.lat, 7), round(p.lon, 7), p.ts.isoformat()]
        for p in trip.route
    ])


def _apply_trip_overrides(df, overrides: dict) -> None:
    """Overwrite source rows with their projected live trip geometry.

    Used for split A-pieces and merged clean rows. raw_row = df position + 1;
    missing columns are skipped.
    """
    for raw_row, trip in overrides.items():
        i = raw_row - 1
        if not (0 <= i < len(df)):
            continue
        vals = {
            "start_date": trip.start_ts.strftime("%Y-%m-%d"),
            "start_weekday": trip.start_ts.strftime("%A"),
            "start_time": trip.start_ts.strftime("%H:%M:%S"),
            "end_date": trip.end_ts.strftime("%Y-%m-%d"),
            "end_weekday": trip.end_ts.strftime("%A"),
            "end_time": trip.end_ts.strftime("%H:%M:%S"),
            "start_latitude": trip.start_lat, "start_longitude": trip.start_lon,
            "end_latitude": trip.end_lat, "end_longitude": trip.end_lon,
            "mode_of_travel": trip.mode, "purpose_of_travel": trip.purpose,
            "time_duration": _duration_cell(trip),
            "routes": _route_cell(trip),
        }
        for col, v in vals.items():
            if col in df.columns:
                if isinstance(v, str) and df[col].dtype != object:
                    df[col] = df[col].astype("object")
                df.at[i, col] = v


def _drop_collapsed_merge_members(df):
    """Keep one physical row per M-trip in cleaned.xlsx."""
    if "trip_id" not in df.columns:
        return df
    seen: set = set()
    keep = []
    for trip_id in df["trip_id"]:
        if isinstance(trip_id, str) and trip_id.startswith("M"):
            keep.append(trip_id not in seen)
            seen.add(trip_id)
        else:
            keep.append(True)
    return df[keep].copy()


def _normalize_pipe_first_columns(df) -> None:
    for col in PIPE_FIRST_COLUMNS:
        if col not in df.columns:
            continue
        df[col] = df[col].map(_first_pipe_value)


def _first_pipe_value(value):
    try:
        import pandas as pd
        if bool(pd.isna(value)):
            return value
    except (TypeError, ValueError):
        pass
    if not isinstance(value, str) or "|" not in value:
        return value
    return value.split("|", 1)[0].strip()


def _row_templates(df) -> dict:
    templates = {}
    if "accessCode" not in df.columns:
        return templates
    for _, src in df.iterrows():
        person = src.get("accessCode")
        if person in templates:
            continue
        templates[person] = {
            col: _first_pipe_value(src[col])
            for col in ADDED_ROW_INHERIT_COLUMNS
            if col in df.columns
        }
    return templates


def _extra_base_row(templates: dict, trip, cols: list) -> dict:
    row = {c: None for c in cols}
    template = templates.get(trip.person)
    if template is None and templates:
        template = next(iter(templates.values()))
    if template:
        for col, value in template.items():
            if col in row:
                row[col] = value
    return row


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
    _apply_trip_overrides(df, overrides)  # truncate splits and materialize merged rows
    _apply_purpose_overrides(df, purpose_overrides)  # hand-edited trip purposes
    _apply_mode_overrides(df, mode_overrides)  # hand-edited travel modes
    _normalize_pipe_first_columns(df)
    templates = _row_templates(df)

    n = len(df)
    df["trip_id"] = [tid.get(i + 1, str(i + 1)) for i in range(n)]  # raw_row = position + 1
    keep = [(i + 1) not in removed for i in range(n)]
    cleaned = _drop_collapsed_merge_members(df[keep].copy())

    if extras:  # append synthesized rows: A = missing-trip stubs, Q = reviewed gaps
        cols = list(cleaned.columns)
        added = []
        for trip_id, kind, trip in _extra_ids(extras):
            if kind == "merged_add":
                continue
            row = _extra_base_row(templates, trip, cols)
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
        if kind in ("add", "merged_add"):
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


_EXTRA_FLAG = {"add": "missing_trip:accept", "merged_add": "manual_add:merged",
               "review": "missing_activity:reviewed",
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
    _apply_trip_overrides(df, overrides)  # truncate splits and materialize merged rows
    _apply_purpose_overrides(df, purpose_overrides)  # hand-edited trip purposes
    _apply_mode_overrides(df, mode_overrides)  # hand-edited travel modes
    _normalize_pipe_first_columns(df)
    templates = _row_templates(df)

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
            row = _extra_base_row(templates, trip, cols)
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
