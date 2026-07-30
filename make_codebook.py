"""Generate trip_id_codebook.xlsx: a plain-language reference for the `trip_id`
column in the cleaned / Do-file exports.

The grammar itself lives in `engine.ids.project_trip_id`; this turns it into
something a person who has never seen the code can read. Regenerate after any
change to that grammar so the two never drift.
"""

from __future__ import annotations

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

OUT = "trip_id_codebook.xlsx"

CODES = [
    {
        "Code": "143",
        "Pattern": "a plain number",
        "Status": "kept",
        "Meaning": "An original trip from the raw survey data, carried through unchanged.",
        "What the reviewer did": "Nothing, or they looked at a flag and decided it was fine (waived it).",
        "In cleaned file": "Yes",
        "In Do-file": "Yes",
    },
    {
        "Code": "M143",
        "Pattern": "M + number",
        "Status": "merged",
        "Meaning": ("Several trips judged to be one real journey, combined into a single trip. "
                    "The number is the LOWEST original row number in the group, and every member "
                    "of that group carries the same M id."),
        "What the reviewer did": ("Accepted a 'fragment' or 'mode change' flag, or merged trips by hand."),
        "In cleaned file": "Yes, as ONE combined row per group",
        "In Do-file": "Yes, every original leg is listed, all sharing the same M id",
    },
    {
        "Code": "A01",
        "Pattern": "A + 2-digit number",
        "Status": "added",
        "Meaning": ("A trip that does NOT exist in the raw data. It was created to fill a gap "
                    "where the participant clearly travelled but nothing was recorded."),
        "What the reviewer did": "Accepted a 'missing trip' flag, or added a trip manually on the map.",
        "In cleaned file": "Yes",
        "In Do-file": "Yes",
    },
    {
        "Code": "Q145",
        "Pattern": "Q + number",
        "Status": "reviewed-gap",
        "Meaning": ("A marker for a gap in ACTIVITY (not travel) that the reviewer looked at and "
                    "confirmed. The number is the row the gap follows. It records that the gap was "
                    "checked, not that a trip happened."),
        "What the reviewer did": "Accepted a 'missing activity' flag. (Waiving one leaves no row at all.)",
        "In cleaned file": "Yes",
        "In Do-file": "Yes",
    },
    {
        "Code": "R145",
        "Pattern": "R + number",
        "Status": "removed",
        "Meaning": ("A trip taken OUT of the cleaned dataset, usually because the participant never "
                    "really moved (GPS drift while stationary)."),
        "What the reviewer did": "Accepted a 'no movement' flag, or deleted the trip by hand.",
        "In cleaned file": "NO, these rows are dropped",
        "In Do-file": "Yes, this is the record of what was removed and why",
    },
    {
        "Code": "Z145",
        "Pattern": "Z + number",
        "Status": "zero-movement",
        "Meaning": ("A DAY on which the participant did not travel at all. Every trip logged that "
                    "day was GPS drift while stationary, so there is one Z row standing for the "
                    "whole day. The travel columns are blank because no journey happened; the "
                    "accessCode and date are kept so the day is still counted."),
        "What the reviewer did": ("Accepted a 'no movement' flag on every trip of that day. This is "
                                  "a finding, not a deletion: the day stays in the dataset."),
        "In cleaned file": "Yes, one row for the whole day",
        "In Do-file": "The day's individual trips appear there as R rows",
    },
    {
        "Code": "S150-A",
        "Pattern": "S + number + -A",
        "Status": "split",
        "Meaning": ("The FIRST half of a trip that was cut in two. The number is the original row "
                    "number of the trip before it was split."),
        "What the reviewer did": "Split one recorded trip into two, e.g. two different modes logged as one.",
        "In cleaned file": "Yes",
        "In Do-file": "Yes",
    },
    {
        "Code": "S150-B",
        "Pattern": "S + number + -B",
        "Status": "split",
        "Meaning": "The SECOND half of that same split trip. Pairs with the -A row above.",
        "What the reviewer did": "Same split as above.",
        "In cleaned file": "Yes",
        "In Do-file": "Yes",
    },
]

STATUS = [
    ("kept", "Original trip, unchanged."),
    ("merged", "Part of a group of trips combined into one journey."),
    ("added", "Trip created by the reviewer; not in the raw data."),
    ("reviewed-gap", "A confirmed gap in activity between two trips."),
    ("removed", "Trip taken out of the cleaned dataset (Do-file only)."),
    ("split", "One half of a trip that was cut in two."),
    ("zero-movement", "Stands for a whole day on which the participant did not travel. "
                      "It is kept in the cleaned file on purpose."),
    ("excluded", "The trip's whole DAY was removed from the dataset, so the row is "
                 "not part of the cleaned data. Different from 'removed', which is "
                 "about a single trip."),
]

NOTES = [
    ("The number is a ROW POSITION, not a trip name",
     "The number inside a trip_id (the 143 in M143) is that trip's row position in the "
     "reviewer's own file. It is only meaningful within that one file."),
    ("Do NOT use trip_id to match trips across reviewers",
     "Because the number is a row position, the same trip_id means completely different "
     "trips in two different reviewers' files. Use the wave_row column instead: it is the "
     "row's position in the original full wave file and is the same for everyone."),
    ("A-numbers restart for each person-day",
     "Added trips are numbered A01, A02, ... within each person-day, so A01 is not unique "
     "on its own. Read it together with the accessCode and date."),
    ("A day with no travel is not a missing day",
     "A Z row means the participant was surveyed that day and did not go anywhere. That is a "
     "real observation and is counted as a day. It is NOT the same as a removed day, which is "
     "absent from the cleaned file entirely and listed on the Do-file's 'removed' sheets."),
    ("The two files are for different jobs",
     "The cleaned file is the deliverable: the dataset as it should be used. The Do-file is "
     "the audit trail: every original row including the ones that were removed, so any "
     "decision can be traced."),
    ("Merged trips appear once in the cleaned file, several times in the Do-file",
     "A merge of 3 trips is 1 row in the cleaned file, but all 3 original rows stay in the "
     "Do-file, each carrying the same M id, so you can see exactly what was combined."),
]


def _style(ws, widths, wrap_cols=()):
    head_fill = PatternFill("solid", fgColor="1F3864")
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 28
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(
                vertical="top", wrap_text=cell.column_letter in wrap_cols)
    ws.freeze_panes = "A2"


def main() -> None:
    codes = pd.DataFrame(CODES)
    status = pd.DataFrame(STATUS, columns=["Status value", "What it means"])
    notes = pd.DataFrame(NOTES, columns=["Point", "Detail"])

    with pd.ExcelWriter(OUT, engine="openpyxl") as xl:
        codes.to_excel(xl, sheet_name="Trip ID codes", index=False)
        status.to_excel(xl, sheet_name="Status column", index=False)
        notes.to_excel(xl, sheet_name="Read this first", index=False)

        _style(xl.sheets["Trip ID codes"], [10, 18, 14, 62, 46, 26, 34], wrap_cols="DEFG")
        _style(xl.sheets["Status column"], [16, 78], wrap_cols="B")
        _style(xl.sheets["Read this first"], [46, 84], wrap_cols="AB")

    print(f"wrote {OUT}: {len(codes)} codes, {len(status)} statuses, {len(notes)} notes")


if __name__ == "__main__":
    main()
