"""Rebuild the combined wave-3 deliverable from the four reviewer sessions.

    .venv\\Scripts\\python.exe analysis\\rebuild_wave3.py

Same shape as `rebuild_wave2.py`, with one structural difference in how the work
was divided. In wave 2, Shafie reviewed a workbook already carved down to his 11
participants, so the two sessions overlapped only on those. In wave 3 he and
Matin worked from the SAME 68-participant file, so every one of those 68 appears
in both exports and needs an owner -- the division of labour exists only in the
decision logs, not in the files. `OWNERS` is therefore derived, not typed out.

Who did what, measured from the logs: Matin edited 62 participants, Shafie 9, and
3 of Shafie's were ones Matin had also cleaned. Per instruction a contested
participant goes to Matin, so Shafie owns the remaining 6.

The contested three are a real discard, not a formality -- on zgqe-zdg-ez both
reviewers marked all 7 days cleaned, and they disagree about whether short
midday campus walks are trips (Matin deleted five of them, Shafie kept them).
Matin's version wins for consistency with the other 61 participants he cleaned.

Yerdana and Fabiha each have their own file and overlap nobody.
"""

from __future__ import annotations

import collections
import json
import sys

import pandas as pd

sys.path.insert(0, r"c:/Users/matin001/projects/trip-review-app")

from app import combine

SESSIONS_ROOT = r"C:/Users/matin001/projects/TripReview/sessions"
WAVE = "Wave3"
OUT_DIR = f"C:/Users/matin001/projects/TripReview/final/exports/{WAVE}"
WAVE_FILE = (r"P:/UiTR/Shared Folder/1. Current Student/Smartphone app data cleaning/"
             r"Wave 3 data/merged wave 3 with coordinates fixed.xlsx")

SESSIONS = {
    "Matin":   f"{SESSIONS_ROOT}/Matin_Wave_3",
    "Shafie":  f"{SESSIONS_ROOT}/Shafie_Wave_3",
    "Yerdana": f"{SESSIONS_ROOT}/Yerdana_Wave_3",
    "Fabiha":  f"{SESSIONS_ROOT}/Fabiha_Wave_3",
}

# Shafie's uncontested participants. The three he shares with Matin
# (adiw-abo-oa, zgob-zfo-iz, zgqe-zdg-ez) are deliberately absent: contested
# goes to Matin.
SHAFIE_OWNS = {
    "zgzb-zie-bz", "zids-zoc-oz", "ziov-zqf-iz",
    "zixa-zsy-cz", "zqsc-zgy-az", "zzsf-zbx-dz",
}


def edited_participants(session_dir: str) -> set:
    """Participants this reviewer actually touched, from their decision log.

    A `decision` event names a flag rather than a day, so flags are mapped back
    to their day through the frozen baseline; every other event carries `day_key`.
    """
    prop = json.load(open(f"{session_dir}/proposals.json", encoding="utf-8"))
    flag_day = {f["id"]: dk for dk, day in prop["days"].items()
                for f in day.get("flags", [])}
    people = set()
    with open(f"{session_dir}/decisions.jsonl", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            e = json.loads(line)
            dk = e.get("day_key")
            if e.get("type") == "decision" and e.get("flag_id") in flag_day:
                dk = flag_day[e["flag_id"]]
            if dk:
                people.add(dk.split("|")[0])
    return people


def build_owners() -> tuple:
    """``(owners, untouched)`` for the shared Matin/Shafie workbook.

    Both sessions export all 68 participants, so any left unassigned would make
    the combine refuse -- correctly, since it will not guess whose cleaning wins.
    """
    prop = json.load(open(f"{SESSIONS['Matin']}/proposals.json", encoding="utf-8"))
    shared = {v["person"] for v in prop["days"].values()}
    stray = sorted(SHAFIE_OWNS - shared)
    if stray:
        raise SystemExit(f"SHAFIE_OWNS names participants not in the shared file: {stray}")

    worked = edited_participants(SESSIONS["Matin"]) | edited_participants(SESSIONS["Shafie"])
    owners = {p: ("Shafie" if p in SHAFIE_OWNS else "Matin") for p in shared}
    return owners, sorted(shared - worked)


def main() -> None:
    owners, untouched = build_owners()
    n_shafie = sum(1 for v in owners.values() if v == "Shafie")
    print(f"shared Matin/Shafie file: {len(owners)} participants "
          f"-> Shafie {n_shafie}, Matin {len(owners) - n_shafie}")
    # a participant nobody edited enters the dataset unreviewed, and nothing in
    # the output would show it, so say so before it is baked in
    print(f"  edited by neither reviewer: {untouched or 'none'}")

    cleaned_path, dofile_path = combine.combine_sessions(
        SESSIONS, out_dir=OUT_DIR, owners=owners)
    print(f"\nwrote {cleaned_path}\nwrote {dofile_path}")

    cl = pd.read_excel(cleaned_path)
    sheets = pd.read_excel(dofile_path, sheet_name=None)
    wave = pd.read_excel(WAVE_FILE)

    dupes = int(cl["wave_row"].dropna().duplicated().sum())
    stray = len(set(cl["wave_row"].dropna().astype(int)) - set(range(1, len(wave) + 1)))
    print()
    print("=== CHECKS (first two must be 0) ===")
    print(f"  duplicate wave_row             : {dupes}")
    print(f"  wave_rows not in the wave file : {stray}")
    print(f"  participants                   : {cl['accessCode'].nunique()}")
    print(f"  rows                           : {len(cl):,}")
    print(f"  per split                      : {dict(collections.Counter(cl['source_split']))}")
    print(f"  owned participants absent      : "
          f"{sorted(set(owners) - set(cl['accessCode'].dropna())) or 'none'}")
    print()
    print("=== REMOVED (pooled) ===")
    for _, r in sheets["removed summary"].iterrows():
        print(f"  {r['Measure']:<32} {r['Count']}")


if __name__ == "__main__":
    main()
