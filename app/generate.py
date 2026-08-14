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
import re
import zipfile
from collections import defaultdict
from datetime import datetime

from app import charts, metrics, regions, routing, session
from app.decisions import (day_overrides, excluded_days, fixpoint, manual_ops, replay,
                           resolutions, trip_comments)
from engine.classify import group_person_days
from engine.ids import project_trip_id
from engine.loader import load_trips
from engine.params import DEFAULT
from engine.proposals import _make_stub, split_b_row


def clean_day(day_trips: list, accepted_ids: set, day_ops: list = None,
              params=DEFAULT) -> tuple:
    """Return ``(rows, extras, surfaced, overrides, mode_overrides, time_overrides,
    purpose_overrides, merge_overrides, position_overrides)`` for one day.

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
    ``day_ops`` = currently-active manual edits for this day (from `manual_ops`).
    """
    day_ops = day_ops or []
    live, surfaced = fixpoint(day_trips, accepted_ids, ops=day_ops, params=params)
    applied = [f for f in surfaced if f.flag_id in accepted_ids]

    split_origins = {op["raw_row"] for op in day_ops if op.get("type") == "manual_split"}
    split_b_of = {split_b_row(op["raw_row"]): op["raw_row"]
                  for op in day_ops if op.get("type") == "manual_split"}

    # Merge groups are read DIRECTLY from the live (fixpoint) trips, never re-derived
    # from flags + ops here -- so the export's grouping is always exactly what the
    # reviewer saw on screen. Each live merged trip already carries its full
    # membership in `absorbed` and its canonical id in `raw_row` (lowest positive
    # member, else lowest -- see engine `_merge_group`). Re-deriving the grouping
    # independently used to let an accepted fragment/mode_change and a manual_merge
    # that SHARE a row disagree, splitting one merged trip into two M-rows on export
    # (and letting order-dependent merges land differently than the live view).
    merged_root: dict = {}
    for t in live:
        members = {t.raw_row} | set(t.absorbed)
        if len(members) > 1:
            for m in members:
                merged_root[m] = t.raw_row

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
    purpose_overrides: dict = {}
    for op in day_ops:
        if op.get("type") == "manual_mode":
            r = op["raw_row"]
            for tgt in group_members.get(merged_root.get(r, r), [r]):
                mode_overrides[tgt] = op["mode"]
        elif op.get("type") == "manual_purpose":
            r = op["raw_row"]
            for tgt in group_members.get(merged_root.get(r, r), [r]):
                purpose_overrides[tgt] = op["purpose"]

    # Manual time edits -> overwrite the raw row's own start/end date+time columns.
    # Each physical row keeps its own times (unlike mode's option-b spread): a merged
    # member's M-row is just a shared id, the underlying rows stay distinct. Last edit
    # of a row wins (dict assignment in op order).
    time_overrides: dict = {}
    for op in day_ops:
        if op.get("type") == "manual_time":
            time_overrides[op["raw_row"]] = (op["start_ts"], op["end_ts"])

    live_by_row = {t.raw_row: t for t in live}

    # Merge collapse (cleaned file only): each merge group becomes ONE row = its
    # lowest workbook (positive) member, whose end/endpoint/route the caller
    # overwrites from the live merged trip. That live trip spans every leg including
    # an ADDED one that has no workbook row, so a logged+added merge collapses
    # correctly instead of leaving the added leg as a stray A-row.
    merge_overrides: dict = {}
    for root, members in group_members.items():
        if len(members) >= 2 and root >= 0:
            lt = live_by_row.get(root)
            if lt is not None:
                merge_overrides[root] = lt

    # Manual reposition -> overwrite the row's endpoint + route from the live
    # (already-repositioned) trip. Only for PLAIN kept rows: a row that is ALSO
    # part of a merge or a split-A already gets its live trip's endpoint/route
    # through merge_overrides/overrides above, and those are built from the same
    # live_by_row (so already reflect the reposition) -- adding it here too would
    # just be a redundant, order-dependent second override of the same columns.
    position_overrides: dict = {}
    for op in day_ops:
        if op.get("type") == "manual_reposition":
            r = op["raw_row"]
            if r not in merged_root and r not in split_origins:
                lt = live_by_row.get(r)
                if lt is not None:
                    position_overrides[r] = lt

    # Every workbook row `live` still accounts for, either standing alone or
    # folded into a merged trip. `removed` above is a RE-DERIVATION of two known
    # removal reasons (an accepted zero_move, a manual delete), and it cannot
    # cover every reason the fixpoint drops a row -- an accepted merge whose
    # partner was deleted, zero_moved, or split takes the WHOLE group out of
    # `live`, yet only the partner is named in `removed`. The surviving members
    # then fell through to the plain-keep branch below and were exported with
    # their original times and coordinates, looking like ordinary untouched
    # trips: 185 such rows across the four wave-2 splits, 3.9% of the dataset.
    # So `live` is the authority on WHAT SURVIVED, exactly as it already is on
    # how survivors are grouped (see merged_root above). A row it does not
    # account for is not in the cleaned file; `project_dataset` derives the
    # Do-file's R<row> entries from whatever is missing here, so those rows
    # stay auditable rather than vanishing silently.
    live_rows: set = set()
    for t in live:
        live_rows |= {t.raw_row} | set(t.absorbed)

    rows: list = []
    overrides: dict = {}
    for t in sorted(day_trips, key=lambda x: x.start_ts):
        if t.raw_row in removed:
            continue  # R<row> lives in the Do-file only, not the cleaned file
        if t.raw_row not in live_rows:
            continue  # the fixpoint dropped it; the reviewer's screen has no such trip
        # MERGE WINS over split-origin: a row can be both (split a trip, then merge
        # its A piece with a neighbour). The A piece is inside the merged trip now,
        # so this row must take the M-id and be collapsed into the group's kept row.
        # Labelling it S<row>-A instead let it escape the merge collapse (which only
        # drops M-prefixed rows) AND left it untruncated (its live trip no longer
        # exists standalone, so the A override could not populate) -- the cleaned
        # file then carried the A piece twice, once inside the M row and once as a
        # full-length S<row>-A. The B piece is unaffected and still lands as S<row>-B.
        if t.raw_row in merged_root:
            rows.append((t.raw_row, project_trip_id("merge", group=merged_root[t.raw_row])))
        elif t.raw_row in split_origins:
            rows.append((t.raw_row, project_trip_id("split", raw_row=t.raw_row) + "-A"))
            a = live_by_row.get(t.raw_row)
            if a is not None:
                overrides[t.raw_row] = a  # truncate the kept row to the A piece
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
    return (rows, extras, surfaced, overrides, mode_overrides, time_overrides,
            purpose_overrides, merge_overrides, position_overrides)


def _write_atomic_xlsx(df, out: str) -> None:
    """Write `df` to `out` atomically (build in memory, then temp file + replace)."""
    import io
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    tmp = out + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(buf.getvalue())
    os.replace(tmp, out)


def _write_atomic_xlsx_sheets(sheets: dict, out: str) -> None:
    """Write ``{sheet_name: df}`` to `out` atomically, one sheet per entry, in
    order (the first is sheet index 0, what a plain ``pd.read_excel(out)`` reads)."""
    import io

    import pandas as pd

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
    tmp = out + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(buf.getvalue())
    os.replace(tmp, out)


def build_precision_df(raw_path: str, events: list, params=DEFAULT):
    """The precision sheet: one row per scored ``(detector, origin)`` pair with
    its TP/FP counts and precision (``None`` when never decided)."""
    import pandas as pd

    table = metrics.precision_table(raw_path, events, params)
    rows = [
        {"detector": ftype, "origin": origin, "tp": c["tp"], "fp": c["fp"],
         "precision": c["precision"]}
        for (ftype, origin), c in sorted(table.items())
    ]
    return pd.DataFrame(rows, columns=["detector", "origin", "tp", "fp", "precision"])


def _route_cell(route) -> str:
    """Serialize a trip's breadcrumbs back into the raw `routes` cell format: a
    Python-literal list of ``[lat, lon, "ISO-ts"]`` triplets (so `parse_route`
    round-trips it). Empty route -> ``"[]"``. A hand-added trip routed via OSM
    carries breadcrumbs here; a missing-trip / reviewed-gap stub stays ``"[]"``."""
    if not route:
        return "[]"
    return repr([[round(p.lat, 6), round(p.lon, 6), p.ts.isoformat()] for p in route])


_TZ_SUFFIX = re.compile(r"([+-]\d{2}:\d{2})$")


def _fmt_time(dt, original=None) -> str:
    """A time-of-day cell in the workbook's own style (offset-carrying, e.g.
    ``10:20:00.999000-08:00``), so an overwritten cell doesn't leave the column
    with two formats: a tz-aware datetime brings its own offset; a naive one (a
    manual time edit is typed offset-less in the UI) inherits the offset of the
    `original` cell value it overwrites, or stays bare if that had none."""
    if dt.utcoffset() is not None:
        return dt.isoformat().split("T", 1)[1]
    m = _TZ_SUFFIX.search(str(original if original is not None else ""))
    return dt.time().isoformat() + (m.group(1) if m else "")


def _note_for_trip(comments: dict, trip) -> str:
    """The reviewer's note for a SYNTHESIZED row (manual add / split B / gap stub).

    Their raw_row is a per-day negative id that repeats across days, so the note is
    keyed by (day_key, raw_row); the day_key is rebuilt from the trip itself."""
    if not comments or trip.person is None:
        return ""
    day_key = f"{trip.person}|{trip.start_ts.date()}"
    return comments.get((day_key, trip.raw_row), "")


def _stub_row(stub) -> dict:
    """Map a stub `Trip` onto the raw column names (others left blank)."""
    return {
        "accessCode": stub.person,
        "start_date": stub.start_ts.strftime("%Y-%m-%d"),
        "start_time": _fmt_time(stub.start_ts),
        "end_date": stub.end_ts.strftime("%Y-%m-%d"),
        "end_time": _fmt_time(stub.end_ts),
        "time_duration": str(stub.end_ts - stub.start_ts),
        "mode_of_travel": stub.mode,
        "purpose_of_travel": stub.purpose,
        "start_latitude": stub.start_lat, "start_longitude": stub.start_lon,
        "end_latitude": stub.end_lat, "end_longitude": stub.end_lon,
        "routes": _route_cell(stub.route),
    }


# Never inferred, whatever the data looks like: `wave_row` identifies ONE source
# row, so a synthesized trip genuinely has none and inventing one would break the
# wave_row join the combine relies on. The rest are the export's own columns.
_NEVER_BACKFILL = {"wave_row", "trip_id", "comment", "status", "flags", "source_split"}


def _backfill_person_columns(df):
    """Fill the participant's own details onto rows that have none.

    A synthesized row -- an added trip, a split's B piece, a reviewed-gap marker --
    is built from a `Trip` in memory, so it carries the journey but none of the
    workbook's columns: no region, no age, no employment. That is 8% of a wave's
    rows silently dropping out of any demographic breakdown, and added trips are
    the worst of it because an `A` row is the ONLY row for that journey (a split's
    A piece still carries them, so its B piece has a populated neighbour).

    Which columns are safe is read from the data rather than hardcoded, so this
    keeps working when a wave changes its schema: a column is a person-level
    constant only if every participant who has a value has exactly ONE value.
    Anything varying within a participant (times, coordinates, purpose, and
    `wave_row` itself) is row-level and left alone.
    """
    if "accessCode" not in df.columns or df.empty:
        return df
    for col in df.columns:
        if col in _NEVER_BACKFILL:
            continue
        known = df[df[col].notna() & df["accessCode"].notna()]
        if known.empty:
            continue
        if (known.groupby("accessCode")[col].nunique() > 1).any():
            continue  # varies within a participant -> row-level, not theirs to inherit
        blank = df[col].isna() & df["accessCode"].notna()
        if blank.any():
            df.loc[blank, col] = df.loc[blank, "accessCode"].map(
                known.groupby("accessCode")[col].first())
    return df


def _fill_weekdays(df):
    """Derive a missing weekday from the row's own date.

    The weekday is row-level, so it cannot be inherited from another row the way
    a participant's age can -- but it is a pure function of the date, which every
    synthesized row does carry."""
    import pandas as pd

    for date_col, wd_col in (("start_date", "start_weekday"), ("end_date", "end_weekday")):
        if date_col in df.columns and wd_col in df.columns:
            blank = df[wd_col].isna() & df[date_col].notna()
            if blank.any():
                df.loc[blank, wd_col] = pd.to_datetime(
                    df.loc[blank, date_col], errors="coerce").dt.day_name()
    return df


def _set_cell(df, i: int, col: str, v) -> None:
    """Assign ``df.at[i, col] = v``, first widening the column to ``object`` when a
    string is written into a non-object (e.g. all-NaN float64) column. Without this
    pandas warns now and will raise in a future version; a workbook column that is
    entirely blank reads back as float64, so an override would otherwise break."""
    if col not in df.columns:
        return
    if isinstance(v, str) and df[col].dtype != object:
        df[col] = df[col].astype(object)
    df.at[i, col] = v


def _apply_split_a_overrides(df, overrides: dict) -> None:
    """Overwrite each kept split-origin row to describe the truncated A piece
    (its end time / BOTH endpoints / mode / purpose) instead of the full original
    trip, so the ``S<n>-A`` and ``S<n>-B`` rows do not double-count the span.
    The start point is written too (normally a no-op) so a reposition made before
    the split isn't lost. ``time_duration`` is recomputed for the truncated span.
    raw_row = df position + 1; missing columns are skipped. The original `routes`
    cell is left intact (the A breadcrumbs are a subset of it)."""
    for raw_row, a in overrides.items():
        i = raw_row - 1
        if not (0 <= i < len(df)):
            continue
        vals = {
            "end_date": a.end_ts.strftime("%Y-%m-%d"),
            "end_time": _fmt_time(a.end_ts),
            "time_duration": str(a.end_ts - a.start_ts),
            "start_latitude": a.start_lat, "start_longitude": a.start_lon,
            "end_latitude": a.end_lat, "end_longitude": a.end_lon,
            "mode_of_travel": a.mode, "purpose_of_travel": a.purpose,
        }
        for col, v in vals.items():
            _set_cell(df, i, col, v)


def _apply_mode_overrides(df, mode_overrides: dict) -> None:
    """Overwrite ``mode_of_travel`` for rows whose mode the reviewer changed by
    hand (raw_row = df position + 1; out-of-range rows skipped)."""
    for raw_row, mode in mode_overrides.items():
        i = raw_row - 1
        if 0 <= i < len(df):
            _set_cell(df, i, "mode_of_travel", mode)


def _apply_purpose_overrides(df, purpose_overrides: dict) -> None:
    """Overwrite ``purpose_of_travel`` for rows whose purpose the reviewer changed
    by hand (raw_row = df position + 1; out-of-range rows skipped)."""
    for raw_row, purpose in purpose_overrides.items():
        i = raw_row - 1
        if 0 <= i < len(df):
            _set_cell(df, i, "purpose_of_travel", purpose)


def _apply_time_overrides(df, time_overrides: dict) -> None:
    """Overwrite the start/end date+time columns for rows whose times the reviewer
    edited by hand (raw_row = df position + 1; out-of-range rows skipped). The op
    stores naive ISO instants (``YYYY-MM-DDTHH:MM:SS``); they are split back into
    the workbook's separate date / time columns (each only if it exists), keeping
    the overwritten cell's UTC-offset style, and ``time_duration`` is recomputed."""
    for raw_row, (start_iso, end_iso) in time_overrides.items():
        i = raw_row - 1
        if not (0 <= i < len(df)):
            continue
        sdt, edt = datetime.fromisoformat(start_iso), datetime.fromisoformat(end_iso)
        old = lambda col: df.at[i, col] if col in df.columns else None
        vals = {
            "start_date": sdt.strftime("%Y-%m-%d"),
            "start_time": _fmt_time(sdt, old("start_time")),
            "end_date": edt.strftime("%Y-%m-%d"),
            "end_time": _fmt_time(edt, old("end_time")),
            "time_duration": str(edt - sdt),
        }
        for col, v in vals.items():
            _set_cell(df, i, col, v)


def _apply_position_overrides(df, position_overrides: dict) -> None:
    """Overwrite a repositioned row's endpoint + route (raw_row = df position +
    1; out-of-range rows skipped) with its live (already-repositioned) trip's
    values. `clean_day` only populates this for plain kept rows -- a merged or
    split-A row's live endpoint/route already flows through
    `_apply_merge_overrides`/`_apply_split_a_overrides`, so there is no overlap."""
    for raw_row, t in position_overrides.items():
        i = raw_row - 1
        if not (0 <= i < len(df)):
            continue
        vals = {
            "start_latitude": t.start_lat, "start_longitude": t.start_lon,
            "end_latitude": t.end_lat, "end_longitude": t.end_lon,
            "routes": _route_cell(t.route),
        }
        for col, v in vals.items():
            _set_cell(df, i, col, v)


def _merge_drop_rows(tid: dict) -> set:
    """The extra workbook legs to drop from the cleaned file so each merge group is
    ONE row: for every ``M<root>`` id shared by 2+ workbook rows, keep the lowest and
    drop the rest. The kept row's end/endpoint/route are overwritten separately from
    the live merged trip (`_apply_merge_overrides`). The Do-file keeps every leg."""
    groups: dict = defaultdict(list)
    for raw_row, t in tid.items():
        if isinstance(t, str) and t[:1] == "M":
            groups[t].append(raw_row)
    drop: set = set()
    for members in groups.values():
        if len(members) >= 2:
            drop |= set(sorted(members)[1:])   # keep the lowest workbook row
    return drop


def _apply_merge_overrides(df, merge_overrides: dict) -> None:
    """Overwrite each kept merge row (raw_row = df position + 1) to describe the WHOLE
    merged trip, exactly as the viewer shows it: end date/time/endpoint, concatenated
    route, AND mode + purpose all come from the live merged trip (which takes the LAST
    leg's mode/purpose, per engine `_merge_group`); the start TIME stays the first
    leg's, but the start POINT is written from the live trip too, so a reposition of
    the first leg's start isn't lost (a no-op when it was never moved). The stale raw
    ``time_duration`` is recomputed for the merged span. This folds an added leg (no
    workbook row) into the single kept row. Applied BEFORE the manual
    mode/purpose/time overrides so an explicit hand-edit still wins."""
    for raw_row, t in merge_overrides.items():
        i = raw_row - 1
        if not (0 <= i < len(df)):
            continue
        t = routing.fill_route_gaps(t)   # street path across the merge seam
        vals = {
            "end_date": t.end_ts.strftime("%Y-%m-%d"),
            "end_time": _fmt_time(t.end_ts),
            "time_duration": str(t.end_ts - t.start_ts),
            "start_latitude": t.start_lat, "start_longitude": t.start_lon,
            "end_latitude": t.end_lat, "end_longitude": t.end_lon,
            "routes": _route_cell(t.route),
            "mode_of_travel": t.mode, "purpose_of_travel": t.purpose,
        }
        for col, v in vals.items():
            _set_cell(df, i, col, v)


def project_dataset(raw_path: str, accepted_ids: set, params=DEFAULT, res_map=None,
                    all_ops: list = None, excluded: set = None,
                    comments: dict = None) -> tuple:
    """Project the whole dataset once: ``(tid, removed, extras, flag_by_row,
    overrides, mode_overrides, time_overrides, purpose_overrides, excluded_rows,
    merge_overrides, position_overrides, comment_by_row, zero_move_days)``.
    ``merge_overrides`` =
    {kept merge row: live merged trip}, cleaned-only, folds every leg (incl. an
    added one) into the one kept row. ``position_overrides`` = {raw_row: live
    trip} from manual repositions (plain kept rows only -- see `clean_day`).
    ``comments`` = {(day_key, raw_row): text} from `decisions.trip_comments`;
    ``comment_by_row`` returns them re-keyed by raw_row for the WORKBOOK (positive)
    rows only -- an added trip's negative raw_row repeats across days, so those are
    resolved per-extra by the caller instead.

    `tid` = {raw_row: trip_id} for surviving loaded trips (keep / M-merge /
    ``S<row>-A`` split origin); `removed` = raw rows dropped by an accepted
    zero_move or manual delete; `extras` = the add/review/split synthesized rows;
    `overrides` = {raw_row: live A trip} columns to overwrite on kept split rows;
    `mode_overrides` = {raw_row: mode} from manual mode changes;
    `time_overrides` = {raw_row: (start_ts, end_ts)} from manual time edits;
    `purpose_overrides` = {raw_row: purpose} from manual purpose changes;
    `excluded_rows` = every raw row of an excluded (out-of-region, not recovered)
    person-day; `zero_move_days` = {day_key: representative raw_row} for days the
    participant did not travel at all (see below).
    When `res_map` (flag_id -> resolution) is given, `flag_by_row` maps
    raw_row -> ["<type>:<resolution>", ...] for the Do-file; otherwise it is empty.
    Shared by the cleaned xlsx and the Do-file so they never disagree. ``all_ops``
    is the full ordered list of active manual ops from `decisions.manual_ops`;
    filtered per day here. ``excluded`` = day_keys to drop wholesale (their rows are
    kept out of the cleaned file but recorded in the Do-file as ``out_of_region``).
    """
    excluded = excluded or set()
    tid: dict = {}
    removed: set = set()
    extras: list = []
    flag_by_row: dict = defaultdict(list)
    overrides: dict = {}
    mode_overrides: dict = {}
    time_overrides: dict = {}
    purpose_overrides: dict = {}
    merge_overrides: dict = {}
    position_overrides: dict = {}
    comment_by_row: dict = {}
    excluded_rows: set = set()
    zero_move_days: dict = {}
    comments = comments or {}
    for (person, date), day_trips in group_person_days(load_trips(raw_path)).items():
        day_key = f"{person}|{date}"
        # Notes ride along with the row wherever it lands -- including on an
        # excluded day, whose rows still appear in the Do-file.
        for x in day_trips:
            note = comments.get((day_key, x.raw_row))
            if note:
                comment_by_row[x.raw_row] = note
        if day_key in excluded:  # whole day dropped: no projection, just audit the rows
            for x in day_trips:
                excluded_rows.add(x.raw_row)
                if res_map is not None:
                    flag_by_row[x.raw_row].append("out_of_region:excluded")
            continue
        day_ops = [op for op in (all_ops or []) if op.get("day_key") == day_key]
        (rows, day_extras, surfaced, day_overrides, day_mode_overrides,
         day_time_overrides, day_purpose_overrides, day_merge_overrides,
         day_position_overrides) = clean_day(
            day_trips, accepted_ids, day_ops=day_ops, params=params)
        present = {r for r, _ in rows}
        for r, t in rows:
            tid[r] = t
        day_rows = {x.raw_row for x in day_trips}
        removed |= day_rows - present

        # A day the participant did not travel at all. Every logged trip was an
        # accepted zero_move (GPS drift while stationary) and nothing replaced
        # them, so the whole day would otherwise vanish from the cleaned file and
        # read as "no data" rather than the finding it actually is: this person
        # stayed put. `clean_day` only removes rows for two reasons, an accepted
        # zero_move or a manual delete, so "all rows gone and none deleted by
        # hand" is exactly "all rows were zero_move". A manual delete is the
        # reviewer calling a record junk, which is NOT a claim about movement,
        # so such a day is deliberately left out.
        hand_deleted = {op["raw_row"] for op in day_ops
                        if op.get("type") == "manual_delete"}
        if day_rows and not present and not day_extras and not (day_rows & hand_deleted):
            # every row of the day, not just the one the Z id is built from: the
            # Do-file marks them all ZR<row> so a removed trip can be told apart
            # from an ordinary removal by whether it belonged to a no-travel day
            zero_move_days[day_key] = sorted(day_rows)
        extras += day_extras
        overrides.update(day_overrides)
        mode_overrides.update(day_mode_overrides)
        time_overrides.update(day_time_overrides)
        purpose_overrides.update(day_purpose_overrides)
        merge_overrides.update(day_merge_overrides)
        position_overrides.update(day_position_overrides)
        if res_map is not None:
            for f in surfaced:
                tag = f"{f.flag_type}:{res_map.get(f.flag_id, 'pending')}"
                touched = [f.trip] if f.trip is not None else list(f.left) + list(f.right)
                for r in touched:
                    flag_by_row[r].append(tag)
    return (tid, removed, extras, flag_by_row, overrides, mode_overrides,
            time_overrides, purpose_overrides, excluded_rows, merge_overrides,
            position_overrides, comment_by_row, zero_move_days)


ZERO_MOVE_NOTE = "zero movement day"

# Blanked on a zero-movement row: everything describing a JOURNEY, because no
# journey happened. What stays is who and when (accessCode / dates / weekday),
# the row's provenance (wave_row), and the person-level survey columns, which
# are attributes of the participant rather than of a trip -- without those the
# row could not be joined to anything and would be unusable.
_ZERO_MOVE_BLANK_COLS = (
    "start_time", "end_time", "time_duration", "identifier",
    "mode_of_travel", "purpose_of_travel", "routes",
    "start_latitude", "start_longitude", "end_latitude", "end_longitude",
)


def _zero_move_day_rows(df, zero_move_days: dict, cols: list) -> list:
    """One placeholder row per day the participant did not travel.

    Such a day has every trip removed as an accepted zero_move, so it would
    otherwise disappear from the cleaned file entirely and be indistinguishable
    from a day the participant never reported. Keeping one blanked row preserves
    the day as an observation: this person was surveyed and did not travel.

    The row is built from a real workbook row of that day (so it keeps that day's
    identity and survey columns), with the journey columns emptied, an ``R<row>``
    trip_id, and a fixed note."""
    rows = []
    n = len(df)
    for _day_key, day_rows in sorted(zero_move_days.items(), key=lambda kv: kv[1]):
        raw_row = day_rows[0]          # the day's lowest row carries the Z id
        i = raw_row - 1
        if not (0 <= i < n):
            continue
        row = {c: df.iloc[i].get(c) for c in cols}
        for c in _ZERO_MOVE_BLANK_COLS:
            if c in row:
                row[c] = None
        row["trip_id"] = project_trip_id("zero_move", raw_row=raw_row)
        row["comment"] = ZERO_MOVE_NOTE
        rows.append(row)
    return rows


def build_cleaned_df(raw_path: str, accepted_ids: set, params=DEFAULT,
                     all_ops: list = None, excluded: set = None,
                     comments: dict = None):
    """Read the raw workbook and return it with `trip_id` + `comment` columns
    added, removed rows dropped, and accepted missing-trip/reviewed-activity rows
    appended as ``A``/``Q`` rows. raw columns are preserved verbatim.
    Skipped/unparsed raw rows stay as plain keeps. Rows of an `excluded`
    (out-of-region, not recovered) day are dropped from the cleaned file entirely.
    ``comments`` = {(day_key, raw_row): text} from `decisions.trip_comments`;
    rows with no note get an empty string.
    """
    import pandas as pd  # lazy: keeps the pure clean_day import light

    comments = comments or {}
    df = pd.read_excel(raw_path).reset_index(drop=True)
    (tid, removed, extras, _, overrides, mode_overrides, time_overrides,
     purpose_overrides, excluded_rows, merge_overrides, position_overrides,
     comment_by_row, zero_move_days) = project_dataset(
        raw_path, accepted_ids, params, all_ops=all_ops, excluded=excluded,
        comments=comments)
    _apply_split_a_overrides(df, overrides)  # truncate kept split rows to their A piece
    _apply_merge_overrides(df, merge_overrides)  # kept merge row -> whole-trip end/route/mode/purpose
    _apply_position_overrides(df, position_overrides)  # repositioned row -> new endpoint/route
    _apply_mode_overrides(df, mode_overrides)  # hand-edited travel modes (win over the merge default)
    _apply_time_overrides(df, time_overrides)  # hand-edited start/end times
    _apply_purpose_overrides(df, purpose_overrides)  # hand-edited trip purposes (win over merge default)
    merge_drop = _merge_drop_rows(tid)  # each merge -> one row (extra legs dropped)

    n = len(df)
    df["trip_id"] = [tid.get(i + 1, str(i + 1)) for i in range(n)]  # raw_row = position + 1
    # Comment column. A merged group collapses to ONE row here, so that kept row
    # carries every member's note (joined, de-duplicated) -- otherwise a note on a
    # dropped leg would silently vanish from the deliverable.
    merged_note: dict = {}
    m_groups: dict = defaultdict(list)
    for raw_row, t in tid.items():
        if isinstance(t, str) and t[:1] == "M":
            m_groups[t].append(raw_row)
    for members in m_groups.values():
        notes = []
        for r in sorted(members):
            note = comment_by_row.get(r)
            if note and note not in notes:
                notes.append(note)
        if notes:
            merged_note[min(members)] = " | ".join(notes)
    df["comment"] = [merged_note.get(i + 1) or comment_by_row.get(i + 1, "") or ""
                     for i in range(n)]
    # drop zero_move/manual deletes, whole out-of-region days, and merged extra legs
    dropped = removed | excluded_rows | merge_drop
    keep = [(i + 1) not in dropped for i in range(n)]
    cleaned = df[keep].copy()

    zm_rows = _zero_move_day_rows(df, zero_move_days, list(cleaned.columns))
    if zm_rows:
        cleaned = pd.concat([cleaned, pd.DataFrame(zm_rows, columns=list(cleaned.columns))],
                            ignore_index=True)

    if extras:  # append synthesized rows: A = missing-trip stubs, Q = reviewed gaps
        cols = list(cleaned.columns)
        added = []
        for trip_id, kind, trip in _extra_ids(extras):
            if kind == "add":
                trip = routing.route_trip(trip)  # OSM street path for a route-less stub
            row = {c: None for c in cols}
            row.update(_stub_row(trip))
            row["trip_id"] = trip_id
            # a synthesized row's raw_row is a PER-DAY negative id, so its note is
            # looked up by (day_key, raw_row), not from the workbook-row map
            row["comment"] = _note_for_trip(comments, trip)
            added.append(row)
        cleaned = pd.concat([cleaned, pd.DataFrame(added, columns=cols)], ignore_index=True)
    if extras or zm_rows:
        cleaned = _sort_chronologically(cleaned)  # slot A/Q/split/zero-move rows into place
    # synthesized rows arrive with only their journey filled in; give them the
    # participant's own details so they are not invisible to any grouping
    cleaned = _fill_weekdays(_backfill_person_columns(cleaned))
    return cleaned


def _chrono_key(df):
    """A per-row sortable datetime from the raw ``start_date`` + ``start_time``
    columns, robust to the tz-offset suffix (``-08:00``) and sub-second precision.
    Unparseable rows -> NaT (sorted last within their person)."""
    import pandas as pd

    date = pd.to_datetime(df["start_date"], errors="coerce").dt.normalize()
    tstr = (df["start_time"].astype(str)
            .str.replace(r"([+-]\d{2}:?\d{2})$", "", regex=True).str.strip())
    return date + pd.to_timedelta(tstr, errors="coerce")


def _sort_chronologically(df):
    """Order every row by start time WITHIN each person, so a synthesized A/Q/split
    row lands in its real chronological position instead of piling up at the bottom.
    Person blocks keep their original first-appearance order (not alphabetised), and
    real rows keep their relative order on ties (stable sort)."""
    person_order = {p: i for i, p in enumerate(dict.fromkeys(df["accessCode"]))}
    return (df.assign(_p=df["accessCode"].map(person_order), _o=_chrono_key(df))
            .sort_values(["_p", "_o"], kind="stable", na_position="last")
            .drop(columns=["_p", "_o"]).reset_index(drop=True))


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


_STATUS = {"M": "merged", "R": "removed", "A": "added", "Q": "reviewed-gap", "S": "split",
           "Z": "zero-movement"}


def _status_of(trip_id: str) -> str:
    # ZR<row> before Z<row>: a ZR row IS a removal (of a trip on a no-travel day),
    # and a one-character prefix test would call it "zero-movement" and quietly
    # drop it out of the removed counts
    if trip_id[:2] == "ZR":
        return "removed"
    return _STATUS.get(trip_id[:1], "kept")   # plain "143" keep ids fall through


_EXTRA_FLAG = {"add": "missing_trip:accept", "review": "missing_activity:reviewed",
               "split": "manual_split"}


def build_dofile_df(raw_path: str, accepted_ids: set, res_map: dict, params=DEFAULT,
                    all_ops: list = None, excluded: set = None, comments: dict = None):
    """The Do-file ledger as a DataFrame: EVERY loaded raw row (removed ones kept
    and marked ``R<row>``, unlike the cleaned file which drops them) plus the
    synthesized A/Q rows, with `trip_id` + `status` + `flags` columns added to the
    raw columns. `flags` lists each flag the row received and its resolution
    (``"<type>:<resolution>"``). The full audit record; same projection as cleaned.
    Rows of an `excluded` out-of-region day keep their raw trip_id but are marked
    ``status=excluded`` with an ``out_of_region:excluded`` flag (dropped from cleaned,
    retained here so the reviewer can see — and recover — what was auto-removed).
    """
    import pandas as pd

    df = pd.read_excel(raw_path).reset_index(drop=True)
    # merge_overrides is cleaned-only (the Do-file keeps every leg untouched);
    # position_overrides applies here too, same as mode/time/purpose -- a
    # reposition is a data correction the audit ledger should show.
    comments = comments or {}
    (tid, removed, extras, flag_by_row, overrides, mode_overrides, time_overrides,
     purpose_overrides, excluded_rows, _merge_overrides, position_overrides,
     comment_by_row, _zero_move_days) = project_dataset(
        raw_path, accepted_ids, params, res_map, all_ops=all_ops, excluded=excluded,
        comments=comments)
    _apply_split_a_overrides(df, overrides)  # truncate kept split rows to their A piece
    _apply_position_overrides(df, position_overrides)  # repositioned row -> new endpoint/route
    _apply_mode_overrides(df, mode_overrides)  # hand-edited travel modes
    _apply_time_overrides(df, time_overrides)  # hand-edited start/end times
    _apply_purpose_overrides(df, purpose_overrides)  # hand-edited trip purposes

    # Rows belonging to a day the participant did not travel. Their removal has a
    # different meaning from an ordinary one -- the whole day was GPS drift, not
    # this particular trip -- so they are marked ZR<row> and can be filtered out
    # of any analysis of what reviewers chose to remove.
    zero_move_rows = {r for day in _zero_move_days.values() for r in day}

    def row_id(i):
        r = i + 1
        if r in removed:
            kind = "zero_move_removed" if r in zero_move_rows else "remove"
            return project_trip_id(kind, raw_row=r)      # ZR<row> / R<row>, Do-file only
        return tid.get(r, str(r))

    n = len(df)
    df["trip_id"] = [row_id(i) for i in range(n)]
    df["status"] = ["excluded" if (i + 1) in excluded_rows else _status_of(t)
                    for i, t in enumerate(df["trip_id"])]
    df["flags"] = ["; ".join(flag_by_row.get(i + 1, [])) for i in range(n)]
    # The ledger keeps every leg, so each row shows its OWN note (no merge-group
    # joining like the cleaned file needs).
    df["comment"] = [comment_by_row.get(i + 1, "") or "" for i in range(n)]

    if extras:
        cols = list(df.columns)
        added = []
        for trip_id, kind, trip in _extra_ids(extras):
            if kind == "add":
                trip = routing.route_trip(trip)  # OSM street path for a route-less stub
            row = {c: None for c in cols}
            row.update(_stub_row(trip))
            row["trip_id"] = trip_id
            row["status"] = _status_of(trip_id)   # A->added, Q->reviewed-gap, S->split
            row["flags"] = _EXTRA_FLAG[kind]
            row["comment"] = _note_for_trip(comments, trip)
            added.append(row)
        df = pd.concat([df, pd.DataFrame(added, columns=cols)], ignore_index=True)
        df = _sort_chronologically(df)  # slot A/Q/split rows into their real spot
    # same as the cleaned file: the ledger's synthesized rows get the participant's
    # details too, so a Do-file row is never less identifiable than a cleaned one
    df = _fill_weekdays(_backfill_person_columns(df))
    return df


def excluded_day_keys(raw_path: str, events: list) -> set:
    """The set of person-day keys dropped from the cleaned export: out-of-region by
    the locked region boxes, minus any the reviewer recovered, plus any removed by
    hand. The single source of truth shared by the export and the day index."""
    days = {f"{p}|{d}": t
            for (p, d), t in group_person_days(load_trips(raw_path)).items()}
    auto_out = regions.out_of_region_days(days, regions.load_regions())
    return excluded_days(events, auto_out)


def build_removed_dfs(raw_path: str, events: list, excluded: set):
    """``(summary_df, rows_df)`` accounting for everything dropped as a whole DAY.

    A day leaves the dataset either because the reviewer removed it by hand or
    because it falls outside the study regions and was not recovered. Both are
    invisible in the cleaned file by design, which makes "how much was dropped,
    and from whom" unanswerable from the deliverable alone. These two sheets are
    that record.

    A PERSON counts as removed when EVERY one of their days is gone, i.e. they
    contribute nothing at all to the cleaned dataset -- as opposed to someone who
    merely lost a day or two. Both numbers matter, so the summary reports the
    fully-removed people separately from the partially-affected ones.

    `rows_df` is every raw row of those days, verbatim, plus which day it came
    from and why it went -- so a dropped participant can still be inspected, or
    reinstated, without going back to the pre-review workbook.
    """
    import pandas as pd

    df = pd.read_excel(raw_path).reset_index(drop=True)
    trips = load_trips(raw_path)
    days = group_person_days(trips)

    by_person: dict = defaultdict(set)
    for (person, date) in days:
        by_person[person].add(f"{person}|{date}")
    hand_removed = {k for k, inc in day_overrides(events).items() if not inc}

    removed_people = sorted(p for p, keys in by_person.items() if keys and keys <= excluded)
    partial_people = sorted(p for p, keys in by_person.items()
                            if keys & excluded and not keys <= excluded)

    rows = []
    for (person, date), day_trips in days.items():
        day_key = f"{person}|{date}"
        if day_key not in excluded:
            continue
        reason = "removed by reviewer" if day_key in hand_removed else "outside study region"
        for t in day_trips:
            i = t.raw_row - 1
            if not (0 <= i < len(df)):
                continue
            rows.append({"day_key": day_key, "person": person, "date": str(date),
                         "reason": reason,
                         "person_fully_removed": person in removed_people,
                         "trip_id": project_trip_id("remove", raw_row=t.raw_row),
                         **df.iloc[i].to_dict()})
    rows_df = pd.DataFrame(rows)
    if not rows_df.empty:
        rows_df = rows_df.sort_values(["person", "date", "trip_id"]).reset_index(drop=True)

    n_hand = len({k for k in excluded if k in hand_removed})
    summary = [
        ("People removed entirely", len(removed_people),
         "Every one of their days was dropped, so they contribute no trips to the cleaned file."),
        ("People who lost some days", len(partial_people),
         "Still present in the cleaned file, but with fewer days than they reported."),
        ("Days removed (total)", len(excluded), "Person-days dropped, for either reason below."),
        ("  ...removed by the reviewer", n_hand, "Dropped by hand with the remove-day action."),
        ("  ...outside the study region", len(excluded) - n_hand,
         "Auto-dropped as out-of-region and not reinstated by the reviewer."),
        ("Trips removed with those days", len(rows_df),
         "Raw survey rows belonging to the dropped days; each one is listed on the detail sheet."),
    ]
    summary_df = pd.DataFrame(summary, columns=["Measure", "Count", "What it means"])
    return summary_df, rows_df


def generate_cleaned_and_dofile(sid: str, params=DEFAULT) -> tuple:
    """Atomically write cleaned.xlsx + dofile.xlsx; return (cleaned, dofile) paths."""
    raw_path = os.path.join(session.session_dir(sid), session.read_proposals(sid)["raw_name"])
    events = session.read_events(sid)
    accepted, res_map = replay(events), resolutions(events)
    ops = manual_ops(events)
    excluded = excluded_day_keys(raw_path, events)
    comments = trip_comments(events)

    cleaned = os.path.abspath(os.path.join(session.session_dir(sid), "cleaned.xlsx"))
    dofile = os.path.abspath(os.path.join(session.session_dir(sid), "dofile.xlsx"))
    _write_atomic_xlsx(build_cleaned_df(raw_path, accepted, params, all_ops=ops,
                                        excluded=excluded, comments=comments), cleaned)
    dofile_df = build_dofile_df(raw_path, accepted, res_map, params, all_ops=ops,
                                excluded=excluded, comments=comments)
    precision_df = build_precision_df(raw_path, events, params)
    removed_summary_df, removed_rows_df = build_removed_dfs(raw_path, events, excluded)
    _write_atomic_xlsx_sheets({"dofile": dofile_df, "precision": precision_df,
                               "removed summary": removed_summary_df,
                               "removed rows": removed_rows_df}, dofile)
    return cleaned, dofile


def _start_time_png_paths(sid: str) -> list:
    """The two QC start-time PNGs for the export, written into the session dir.
    Charts are a bonus deliverable: any failure here (matplotlib missing or
    broken in a stripped build) must never block the export itself, so the
    caller treats an empty list as "zip without charts"."""
    raw_path = os.path.join(session.session_dir(sid), session.read_proposals(sid)["raw_name"])
    days = {f"{p}|{d}": t for (p, d), t in group_person_days(load_trips(raw_path)).items()}
    series = charts.start_time_series(days, session.read_events(sid))
    return charts.write_start_time_pngs(series, session.session_dir(sid))


def generate_bundle(sid: str, params=DEFAULT) -> str:
    """Write cleaned.xlsx + dofile.xlsx (+ the two start-time chart PNGs) and
    zip them; return the zip path."""
    cleaned, dofile = generate_cleaned_and_dofile(sid, params)
    try:
        pngs = _start_time_png_paths(sid)
    except Exception:
        pngs = []  # charts must never block the export
    out = os.path.abspath(os.path.join(session.session_dir(sid), "export.zip"))
    tmp = out + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(cleaned, "cleaned.xlsx")
        z.write(dofile, "dofile.xlsx")
        for p in pngs:
            z.write(p, os.path.basename(p))
    os.replace(tmp, out)
    return out
