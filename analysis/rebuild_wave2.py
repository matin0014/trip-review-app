"""Rebuild the combined wave-2 deliverable from the four reviewer sessions.

Run after any change to the projection (`app/generate.py`) or the combine, since
the exports are derived and every one of them goes stale the moment that code
changes:

    .venv\\Scripts\\python.exe analysis\\rebuild_wave2.py

Regenerates each session's export from its decision log, combines the four, and
writes cleaned.xlsx + dofile.xlsx to `OUT_DIR`. The session folders are COPIED to
a temp working dir first, so the reviewers' own folders are never written to.

Shafie carved 11 participants out of Yerdana's file and cleaned them, so both
splits export those people; `OWNERS` says Shafie's copy is the authoritative one.
Without it the combine refuses to run rather than double-count them.
"""

from __future__ import annotations

import collections
import sys

import pandas as pd

sys.path.insert(0, r"c:/Users/matin001/projects/trip-review-app")

from app import combine

SESSIONS_ROOT = r"C:/Users/matin001/projects/TripReview/sessions"
# filed by wave: workbooks under exports/<wave>/, the charts drawn from them
# under distributions/<wave>/ (see analysis/final_charts.py)
WAVE = "Wave2"
OUT_DIR = f"C:/Users/matin001/projects/TripReview/final/exports/{WAVE}"
WAVE = (r"P:/UiTR/Shared Folder/1. Current Student/Matin/"
        r"4. Smartphone App Data Cleaning/Wave2 data with SD_coordinates_updated.xlsx")

SESSIONS = {
    "Split 1":         f"{SESSIONS_ROOT}/34a88af7abc2",
    "Split 2 Yerdana": f"{SESSIONS_ROOT}/f684e9825e45",
    "Split 2 Shafie":  f"{SESSIONS_ROOT}/2a170a5bef68",
    "Split 3":         f"{SESSIONS_ROOT}/1dd86489e8c9",
}

SHAFIE_CODES = ["weqq-wsw-xw", "wxzi-wog-qw", "xbze-xcd-gx", "xfcf-xyy-gx",
                "xvas-xoa-fx", "yixz-ywf-ay", "yofb-ybg-fy", "yoxz-yea-wy",
                "zeqz-zfq-bz", "zgqe-zdg-ez", "zybw-zyi-iz"]
OWNERS = {code: "Split 2 Shafie" for code in SHAFIE_CODES}


def main() -> None:
    cleaned_path, dofile_path = combine.combine_sessions(
        SESSIONS, out_dir=OUT_DIR, owners=OWNERS)
    print(f"wrote {cleaned_path}")
    print(f"wrote {dofile_path}")

    cl = pd.read_excel(cleaned_path)
    sheets = pd.read_excel(dofile_path, sheet_name=None)
    wave = pd.read_excel(WAVE)

    dupes = int(cl["wave_row"].dropna().duplicated().sum())
    stray = len(set(cl["wave_row"].dropna().astype(int)) - set(range(1, len(wave) + 1)))
    kinds = collections.Counter(
        "plain" if str(t).isdigit() else str(t)[0] for t in cl["trip_id"])

    print()
    print("=== CHECKS (all three must be clean) ===")
    print(f"  duplicate wave_row              : {dupes}   (must be 0)")
    print(f"  wave_rows not in the wave file  : {stray}   (must be 0)")
    print(f"  participants                    : {cl['accessCode'].nunique()}")
    print()
    print(f"  rows          : {len(cl):,}")
    print(f"  per split     : {dict(collections.Counter(cl['source_split']))}")
    print(f"  trip_id kinds : {dict(sorted(kinds.items()))}")
    # An owned participant can legitimately end up in no split at all -- their
    # owner removed every one of their days -- but that is a whole participant
    # leaving the dataset, so it should be stated rather than discovered later.
    present = set(cl["accessCode"].dropna())
    vanished = sorted(set(OWNERS) - present)
    print()
    print(f"  owned participants absent from the dataset: {vanished or 'none'}")
    print()
    print("=== REMOVED (pooled across the four splits) ===")
    for _, r in sheets["removed summary"].iterrows():
        print(f"  {r['Measure']:<32} {r['Count']}")
    print()
    print("  dofile sheets:", {n: len(v) for n, v in sheets.items()})


if __name__ == "__main__":
    main()
