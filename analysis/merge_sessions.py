"""Merge a wave's split reviewer sessions into ONE session for the whole wave.

    .venv\\Scripts\\python.exe analysis\\merge_sessions.py Wave2
    .venv\\Scripts\\python.exe analysis\\merge_sessions.py Wave3

A wave is split by participant so reviewers can work in parallel, which leaves
the wave's review spread across four sessions that cannot be opened together.
This stitches them back into a single session whose export is byte-identical to
the combined deliverable, so the whole wave can be browsed, re-checked and
re-exported in one place.

WHY A REWRITE RATHER THAN A CONCATENATION
`raw_row` is a row's POSITION in one reviewer's workbook, so row 130 exists in
every split and means a different trip in each. `flag_id` is worse: a SHA1 over
the flag's raw rows, so the same flag has a different id in every split. Simply
appending the four logs would therefore address the wrong trips and decide
flags that do not exist. Every event is re-keyed instead:

  * `raw_row` (and the row encoded inside a split B-half) is remapped through
    the `wave_row` column the splitter stamped into each workbook, which is the
    one identifier meaning the same thing everywhere.
  * `flag_id` is recomputed by replaying each split to enumerate the flags it
    raised, mapping their rows to merged positions, and re-hashing. Decisions
    are then rewritten to the new ids.

The merged log is consequently NOT the original audit trail. It replays to the
same result, but the split sessions remain the archival record.

OWNERSHIP
A participant can appear in two splits; only one reviewer's work can survive.
The rule matches the deliverable exactly (see `rebuild_wave2/3.py`), so the
export can be diffed against the current cleaned.xlsx as a correctness check.

Manual ops carry `day_key`, so they are filtered by owner directly. Decisions
name only a flag, so they are filtered by the day their flag belongs to.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import sys

import pandas as pd

sys.path.insert(0, r"c:/Users/matin001/projects/trip-review-app")

from app.decisions import (APPLY_RESOLUTIONS, _active_events, all_raised_flags,
                           manual_ops,
                           resolutions)
from engine.classify import group_person_days
from engine.ids import with_ids
from engine.loader import load_trips
from engine.params import DEFAULT
from engine.proposals import _ADD_BASE, _SPLIT_B_OFFSET

REPO = r"c:/Users/matin001/projects/trip-review-app"
SESSIONS_DIR = f"{REPO}/sessions"
RAW_DIR = (r"P:/UiTR/Shared Folder/1. Current Student/Matin/"
           r"4. Smartphone App Data Cleaning/Raw")

# Wave 2's 11 contested participants belong to Shafie, matching
# `rebuild_wave2.SHAFIE_CODES`; wave 3's contested ones belong to Matin, with
# Shafie keeping the six he alone worked (`rebuild_wave3.SHAFIE_OWNS`).
W2_SHAFIE = {"weqq-wsw-xw", "wxzi-wog-qw", "xbze-xcd-gx", "xfcf-xyy-gx",
             "xvas-xoa-fx", "yixz-ywf-ay", "yofb-ybg-fy", "yoxz-yea-wy",
             "zeqz-zfq-bz", "zgqe-zdg-ez", "zybw-zyi-iz"}
W3_SHAFIE = {"zgzb-zie-bz", "zids-zoc-oz", "ziov-zqf-iz",
             "zixa-zsy-cz", "zqsc-zgy-az", "zzsf-zbx-dz"}

WAVES = {
    "Wave2": {
        "splits": ["Matin_Wave_2", "Yerdana_Wave_2", "Shafie_Wave_2",
                   "Fabiha_Wave_2"],
        "raw": f"{RAW_DIR}/Wave2 data with SD_coordinates_updated.xlsx",
        "priority": ["Shafie_Wave_2"],      # wins any contest it is part of
        "owns": {"Shafie_Wave_2": W2_SHAFIE},
        "out": "Wave2_MERGED",
    },
    "Wave3": {
        "splits": ["Matin_Wave_3", "Shafie_Wave_3", "Yerdana_Wave_3",
                   "Fabiha_Wave_3"],
        "raw": f"{RAW_DIR}/merged wave 3 with coordinates fixed.csv",
        "priority": ["Shafie_Wave_3"],
        "owns": {"Shafie_Wave_3": W3_SHAFIE},
        "out": "Wave3_MERGED",
    },
}

STICKY = {"day_cleaned", "day_include", "trip_comment"}


def split_raw(sdir: str) -> str:
    hits = [p for p in glob.glob(os.path.join(sdir, "*.xlsx"))
            if os.path.basename(p) not in ("cleaned.xlsx", "dofile.xlsx")
            and not os.path.basename(p).startswith("~$")]
    assert len(hits) == 1, (sdir, hits)
    return hits[0]


def owner_map(cfg: dict) -> dict:
    """participant -> the split whose decisions survive."""
    members = {}
    for name in cfg["splits"]:
        df = pd.read_excel(split_raw(f"{SESSIONS_DIR}/{name}"))
        for person in df["accessCode"].dropna().unique():
            members.setdefault(person, []).append(name)

    owners = {}
    for person, splits in members.items():
        if len(splits) == 1:
            owners[person] = splits[0]
            continue
        # contested: the priority split only if it is explicitly given this
        # participant, otherwise the other one
        winner = None
        for pri in cfg["priority"]:
            if pri in splits and person in cfg["owns"].get(pri, set()):
                winner = pri
        if winner is None:
            others = [s for s in splits if s not in cfg["priority"]]
            winner = others[0] if others else splits[0]
        owners[person] = winner
    return owners


def flag_ids_by_split(sdir: str, row_map: dict) -> dict:
    """``{old_flag_id: (new_flag_id, day_key)}`` for every flag the split raises.

    Enumerated from the FULL cascade, not from a single baseline pass. Accepting
    a merge re-classifies the merged trip and can raise a flag that does not
    exist in the unedited classification; the reviewer could and did decide
    those, so a baseline-only enumeration silently fails to map their ids and
    drops the decision. That cost 370 rows and 32 Q-rows on the first attempt.
    """
    with open(os.path.join(sdir, "decisions.jsonl"), encoding="utf-8") as fh:
        events = [json.loads(l) for l in fh if l.strip()]
    res = resolutions(events)
    accepted = {f for f, r in res.items() if r in APPLY_RESOLUTIONS}
    ops = manual_ops(events)

    out = {}
    for (person, date), day in group_person_days(load_trips(split_raw(sdir))).items():
        day_key = f"{person}|{date}"
        day_ops = [o for o in ops if o.get("day_key") == day_key]
        for f in all_raised_flags(day, accepted, ops=day_ops, params=DEFAULT):
            # a cascade-born flag can sit on a split B-piece or an added trip,
            # whose synthetic raw_row is absent from row_map, so every row goes
            # through remap_row rather than a plain lookup
            left = tuple(remap_row(r, row_map) for r in f.left)
            right = tuple(remap_row(r, row_map) for r in f.right)
            trip = remap_row(f.trip, row_map) if f.trip is not None else None
            if any(r is None for r in left + right) or (
                    f.trip is not None and trip is None):
                continue
            moved = f.__class__(
                flag_type=f.flag_type, left=left, right=right, trip=trip,
                group=min(left + right, default=None) if f.group is not None else None,
                evidence=f.evidence, origin=f.origin,
            )
            out[f.flag_id] = (with_ids([moved])[0].flag_id, day_key)
    return out


def remap_row(row: int, row_map: dict) -> "int | None":
    """A split-local raw_row (or synthetic encoding of one) in merged terms.

    Three negative encodings exist and only one of them carries a row number
    that needs moving:

      * below ``-_ADD_BASE``  -- ``add_row(n)``, keyed off a per-day add index,
        so it means the same thing in any workbook and is left alone.
      * at or below ``-(_SPLIT_B_OFFSET + 1)`` -- a split's B-piece, keyed off
        its ORIGIN row, which is split-local and must be remapped.
      * a small negative -- a stub row from `proposals._make_stub`, numbered
        downward from -1 within one day, so it is day-scoped and left alone.
        Treating a stub as a B-piece is what produced a `None` here.
    """
    if row >= 0:
        return row_map.get(row)
    if row < -_ADD_BASE:
        return row
    if row > -(_SPLIT_B_OFFSET + 1):          # stub, not a B-piece
        return row
    orig = -row - _SPLIT_B_OFFSET
    moved = row_map.get(orig)
    return None if moved is None else -(moved + _SPLIT_B_OFFSET)


def merge(wave: str) -> str:
    cfg = WAVES[wave]
    out_dir = f"{SESSIONS_DIR}/{cfg['out']}"
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)

    # the merged workbook: the whole wave, positions 1..N in wave_row order
    raw = (pd.read_csv(cfg["raw"], low_memory=False) if cfg["raw"].endswith(".csv")
           else pd.read_excel(cfg["raw"]))
    if "wave_row" not in raw.columns:
        raw.insert(0, "wave_row", range(1, len(raw) + 1))
    raw = raw.sort_values("wave_row").reset_index(drop=True)
    merged_pos = {int(w): i + 1 for i, w in enumerate(raw["wave_row"])}
    dest = os.path.join(out_dir, f"{cfg['out']}.xlsx")
    raw.to_excel(dest, index=False)
    print(f"  merged workbook: {len(raw):,} rows -> {os.path.basename(dest)}")

    owners = owner_map(cfg)
    events, seq = [], 0
    for name in cfg["splits"]:
        sdir = f"{SESSIONS_DIR}/{name}"
        sdf = pd.read_excel(split_raw(sdir))
        # split position (1-based) -> merged position, via wave_row
        row_map = {i + 1: merged_pos[int(w)] for i, w in enumerate(sdf["wave_row"])
                   if int(w) in merged_pos}
        fmap = flag_ids_by_split(sdir, row_map)

        with open(f"{sdir}/decisions.jsonl", encoding="utf-8") as fh:
            raw_events = [json.loads(l) for l in fh if l.strip()]

        # Resolve undo/redo BEFORE filtering. `revert`/`redo`/`undo_event` are a
        # global LIFO over the whole session and carry no day_key, so an
        # ownership filter cannot keep them attached to the right participant:
        # dropping them silently reinstated every undone action (+22 added trips
        # on the first attempt). `_active_events` collapses the stack to the
        # events actually in force, which need no revert to replay.
        effective = _active_events(raw_events)
        sticky = [e for e in raw_events if e.get("type") in STICKY]

        mine = {p for p, o in owners.items() if o == name}
        kept = 0
        for e in effective + sticky:
            kind = e.get("type")
            if kind == "decision":
                hit = fmap.get(e.get("flag_id"))
                if hit is None or hit[1].split("|")[0] not in mine:
                    continue
                e = dict(e, flag_id=hit[0])
            else:
                day_key = e.get("day_key")
                if not day_key or day_key.split("|")[0] not in mine:
                    continue
                e = dict(e)
                if "raw_row" in e:
                    moved = remap_row(int(e["raw_row"]), row_map)
                    if moved is None:
                        continue
                    e["raw_row"] = moved
                if "raw_rows" in e:
                    moved = [remap_row(int(r), row_map) for r in e["raw_rows"]]
                    if any(m is None for m in moved):
                        continue
                    e["raw_rows"] = moved
                if e.get("merged_rows"):
                    e["merged_rows"] = [remap_row(int(r), row_map)
                                        for r in e["merged_rows"]]
            seq += 1
            events.append(dict(e, seq=seq, _src=name))
            kept += 1
        print(f"  {name:<18} {len(raw_events):>6,} events -> {kept:>6,} kept "
              f"({len(mine)} participants owned)")

    # replayed in original order per split; undo/redo stacks are per split and a
    # revert must not pop an event contributed by a different reviewer, so the
    # streams stay grouped rather than interleaved by timestamp
    with open(f"{out_dir}/decisions.jsonl", "w", encoding="utf-8") as fh:
        for e in events:
            e.pop("_src", None)
            fh.write(json.dumps(e) + "\n")
    print(f"  merged log: {len(events):,} events")
    return out_dir


def main() -> None:
    wave = sys.argv[1] if len(sys.argv) > 1 else "Wave2"
    assert wave in WAVES, f"wave must be one of {sorted(WAVES)}"
    print(f"=== merging {wave} ===")
    out = merge(wave)
    print(f"\nwrote {out}")
    print("Open it in the app, or run the export and diff against the current "
          "cleaned.xlsx to verify.")


if __name__ == "__main__":
    main()
