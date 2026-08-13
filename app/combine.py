"""Combine several reviewers' exports into one dataset.

A wave is split by participant so reviewers can clean in parallel
(`split_wave.ipynb`), which means the deliverable has to be stitched back
together afterwards. Doing that naively corrupts the data in two ways this
module exists to prevent:

  * `trip_id` is NOT a key. It is derived from `raw_row`, which is a row's
    POSITION inside one reviewer's own workbook, so "130" exists in every split
    and means a different trip in each. `wave_row` -- the row's position in the
    original undivided wave file, stamped by the splitter -- is the only
    identifier that means the same thing everywhere, so it is what we join and
    de-duplicate on.
  * A participant can end up in TWO splits when reviewers carve up work by hand
    (one took the tail of another's file). Both then export that participant,
    and a plain concatenation silently double-counts every one of their trips.
    We refuse to guess: an overlap is an error unless the caller says which
    split owns the participant.

Everything here is pure DataFrame work so it can be tested without sessions;
`combine_sessions` is the thin driver that regenerates each session's export
first (the on-disk ones go stale the moment anyone reviews another day).
"""

from __future__ import annotations

from collections import defaultdict

KEY = "wave_row"
_SUMMARY_MEASURE = "Measure"
_SUMMARY_COUNT = "Count"


class CombineError(Exception):
    """Raised when combining would silently produce a wrong dataset."""


def participants(df) -> set:
    """The accessCodes present in one reviewer's exported rows."""
    if df is None or df.empty or "accessCode" not in df.columns:
        return set()
    return set(df["accessCode"].dropna().unique())


def find_overlaps(parts: dict) -> dict:
    """``{accessCode: [split names]}`` for participants exported by MORE than one
    split. Empty when the splits are disjoint, which is the normal case."""
    where: dict = defaultdict(list)
    for name, df in parts.items():
        for code in participants(df):
            where[code].append(name)
    return {code: sorted(names) for code, names in where.items() if len(names) > 1}


def resolve_overlaps(parts: dict, owners: dict = None) -> dict:
    """Keep each owned participant only in the split that owns them.

    `owners` maps accessCode -> the split name whose cleaning of that participant
    is authoritative. Every DETECTED overlap must be listed; an unresolved one
    raises rather than letting a double-counted participant reach the dataset.

    Ownership is applied to every split unconditionally, NOT only where an
    overlap was detected. The owner's export can legitimately be empty for that
    participant -- if the owner removed all of their days, they contribute no
    rows -- and in that case the participant appears in exactly one export, the
    non-owner's, so overlap detection cannot see them. Dropping only detected
    overlaps would then keep the non-owner's UNREVIEWED copy, which is the
    silent-wrong-answer this whole module exists to prevent.
    """
    owners = owners or {}
    unknown = sorted({o for o in owners.values() if o not in parts})
    if unknown:
        raise CombineError(
            f"owner(s) {', '.join(map(repr, unknown))} are not among the splits "
            f"being combined ({', '.join(map(repr, sorted(parts)))})")

    overlaps = find_overlaps(parts)
    unresolved = sorted(set(overlaps) - set(owners))
    if unresolved:
        raise CombineError(
            f"{len(unresolved)} participant(s) appear in more than one split with no owner "
            f"given, so combining would double-count them: "
            + "; ".join(f"{c} in {'+'.join(overlaps[c])}" for c in unresolved[:5])
            + (" ..." if len(unresolved) > 5 else ""))

    out = {}
    for name, df in parts.items():
        drop = {c for c, owner in owners.items() if owner != name}
        out[name] = (df[~df["accessCode"].isin(drop)].copy()
                     if drop and df is not None and not df.empty and "accessCode" in df.columns
                     else (df.copy() if df is not None else df))
    return out


def owned_but_absent(parts: dict, owners: dict = None) -> list:
    """Owned participants who end up with NO rows anywhere: their owner exported
    none (every day removed) and the other splits' copies were dropped. Their
    absence is correct, but it is a deletion of a whole participant, so the
    caller should be able to state it rather than discover it later."""
    owners = owners or {}
    resolved = resolve_overlaps(parts, owners)
    present = set()
    for df in resolved.values():
        present |= participants(df)
    return sorted(set(owners) - present)


def combine_cleaned(parts: dict, owners: dict = None):
    """One cleaned dataset from ``{split name: cleaned df}``.

    Adds a `source_split` column so any row can be traced back to the reviewer
    who produced it, and refuses to return a dataset containing the same
    `wave_row` twice.
    """
    import pandas as pd

    resolved = resolve_overlaps(parts, owners)
    frames = []
    for name, df in resolved.items():
        if df is None or df.empty:
            continue
        df = df.copy()
        df["source_split"] = name
        frames.append(df)
    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True, sort=False)
    _assert_unique_key(out)
    if "accessCode" in out.columns and KEY in out.columns:
        out = out.sort_values(["accessCode", KEY], kind="stable").reset_index(drop=True)
    return out


def _assert_unique_key(df) -> None:
    """A repeated `wave_row` means one raw trip reached the dataset twice, which
    is the exact failure a split-then-recombine workflow risks."""
    if KEY not in df.columns:
        raise CombineError(
            f"exports have no {KEY!r} column, so rows cannot be matched across "
            "splits; re-split the wave with split_wave.ipynb, which stamps it")
    real = df[df[KEY].notna()]
    dup = real[real.duplicated(KEY, keep=False)]
    if dup.empty:
        return
    codes = sorted(dup["accessCode"].dropna().unique()) if "accessCode" in dup else []
    raise CombineError(
        f"{dup[KEY].nunique()} {KEY} value(s) appear in more than one split "
        f"({len(dup)} rows), i.e. the same trip was cleaned twice"
        + (f"; participants: {', '.join(map(str, codes[:5]))}" if codes else ""))


def combine_dofile(parts: dict, owners: dict = None) -> dict:
    """One Do-file from ``{split name: {sheet name: df}}``.

    The ledger sheets stack (with `source_split` added); the removed-summary
    counts add up; precision is recomputed from POOLED tp/fp rather than
    averaged, because averaging rates over different denominators is wrong.
    """
    import pandas as pd

    ledger = {name: sheets.get("dofile") for name, sheets in parts.items()}
    resolved = resolve_overlaps(ledger, owners)
    dropped_by_split = {
        name: participants(ledger[name]) - participants(resolved[name])
        for name in ledger}

    out: dict = {}
    stacked = []
    for name, df in resolved.items():
        if df is None or df.empty:
            continue
        df = df.copy()
        df["source_split"] = name
        stacked.append(df)
    out["dofile"] = (pd.concat(stacked, ignore_index=True, sort=False)
                     if stacked else pd.DataFrame())

    out["precision"] = _pool_precision(
        {n: s.get("precision") for n, s in parts.items()})

    for sheet in ("removed summary", "removed rows"):
        frames = []
        for name, sheets in parts.items():
            df = sheets.get(sheet)
            if df is None or df.empty:
                continue
            df = df.copy()
            if sheet == "removed rows":
                df = df[~df.get("person", pd.Series(dtype=object))
                        .isin(dropped_by_split.get(name, set()))]
                if df.empty:
                    continue
            df["source_split"] = name
            frames.append(df)
        if sheet == "removed summary":
            out[sheet] = _sum_summary(frames)
        else:
            out[sheet] = (pd.concat(frames, ignore_index=True, sort=False)
                          if frames else pd.DataFrame())
    return out


def _sum_summary(frames):
    """Add the per-split removed-summary counts into one table, keeping each
    measure's wording and order from the first split that reported it."""
    import pandas as pd

    if not frames:
        return pd.DataFrame(columns=[_SUMMARY_MEASURE, _SUMMARY_COUNT, "What it means"])
    order, totals, blurb = [], defaultdict(int), {}
    for df in frames:
        for _, row in df.iterrows():
            m = row[_SUMMARY_MEASURE]
            if m not in blurb:
                order.append(m)
                blurb[m] = row.get("What it means", "")
            totals[m] += int(row[_SUMMARY_COUNT] or 0)
    return pd.DataFrame([{_SUMMARY_MEASURE: m, _SUMMARY_COUNT: totals[m],
                          "What it means": blurb[m]} for m in order])


def combine_sessions(sessions: dict, out_dir: str, owners: dict = None,
                     work_dir: str = None) -> tuple:
    """Regenerate every session's export, combine them, write the two files.

    ``sessions`` = {split name: session dir}. Each session is COPIED to
    `work_dir` before generating: the export writes cleaned.xlsx / dofile.xlsx
    into the session folder, and a reviewer's folder is the record of their work,
    not scratch space. Regenerating rather than reading the existing files is
    deliberate too -- an export on disk is a snapshot from whenever Generate was
    last pressed, so it silently omits any review done since.

    Returns ``(cleaned_path, dofile_path)``.
    """
    import os
    import shutil
    import tempfile

    import pandas as pd

    from app import generate, session as session_mod, state

    work_dir = work_dir or tempfile.mkdtemp(prefix="combine-")
    os.makedirs(out_dir, exist_ok=True)
    prev_dir = session_mod.SESSIONS_DIR
    cleaned_parts, dofile_parts = {}, {}
    total = len(sessions)
    try:
        session_mod.SESSIONS_DIR = work_dir
        for i, (name, src) in enumerate(sessions.items(), start=1):
            sid = os.path.basename(os.path.normpath(src))
            dest = os.path.join(work_dir, sid)
            # regenerating a session takes a while and says nothing while it runs,
            # so report per step: a silent multi-minute wait is indistinguishable
            # from a hang
            print(f"[{i}/{total}] {name}: copying session...", flush=True)
            if not os.path.exists(dest):
                shutil.copytree(src, dest)
            print(f"[{i}/{total}] {name}: regenerating export...", flush=True)
            state.clear(sid)
            cleaned_path, dofile_path = generate.generate_cleaned_and_dofile(sid)
            cleaned_parts[name] = pd.read_excel(cleaned_path)
            dofile_parts[name] = pd.read_excel(dofile_path, sheet_name=None)
            print(f"[{i}/{total}] {name}: {len(cleaned_parts[name]):,} rows", flush=True)
    finally:
        session_mod.SESSIONS_DIR = prev_dir
        state.clear()

    print("combining...", flush=True)
    final_cleaned = combine_cleaned(cleaned_parts, owners)
    final_dofile = combine_dofile(dofile_parts, owners)

    cleaned_out = os.path.abspath(os.path.join(out_dir, "cleaned.xlsx"))
    dofile_out = os.path.abspath(os.path.join(out_dir, "dofile.xlsx"))
    generate._write_atomic_xlsx(final_cleaned, cleaned_out)
    generate._write_atomic_xlsx_sheets(final_dofile, dofile_out)
    return cleaned_out, dofile_out


def _pool_precision(by_split: dict):
    """Recompute precision from summed tp/fp per (detector, origin)."""
    import pandas as pd

    acc: dict = defaultdict(lambda: {"tp": 0, "fp": 0})
    for df in by_split.values():
        if df is None or df.empty:
            continue
        for _, r in df.iterrows():
            acc[(r["detector"], r["origin"])]["tp"] += int(r["tp"] or 0)
            acc[(r["detector"], r["origin"])]["fp"] += int(r["fp"] or 0)
    rows = []
    for (det, origin), c in sorted(acc.items()):
        n = c["tp"] + c["fp"]
        rows.append({"detector": det, "origin": origin, "tp": c["tp"], "fp": c["fp"],
                     "precision": (c["tp"] / n) if n else None})
    return pd.DataFrame(rows, columns=["detector", "origin", "tp", "fp", "precision"])
