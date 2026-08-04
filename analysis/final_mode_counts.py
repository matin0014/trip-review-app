"""Trips by mode of travel for the combined cleaned file.

Horizontal bars because the mode labels are words of very different length, and
the question is a ranking ("which modes dominate"), which reads down a column
far better than across a row of rotated tick labels.

The normalisation matters more here than anywhere else: the survey stores modes
with zero-width spaces embedded, so "Walk", "Walk\\u200b" and "Walk\\u200b\\u200b"
are three different strings that all mean walking. Counting the column as it
arrives reports 21 modes for what are really 14, splitting Auto Driver into
2206 -> 1803 + 403 and understating every affected mode.

Q rows (a reviewed ACTIVITY gap) and Z rows (a no-travel day) are not journeys
and carry no meaningful mode, so they take no part in the count.
"""

from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from final_start_times import CLEANED, norm

OUT_PNG = r"C:/Users/matin001/projects/TripReview/final/mode_counts.png"

INK, MUTED, GRID = "#1a1a1a", "#5c5c5c", "#d8d8d8"
SERIES, MISSING = "#2b6cb0", "#8a8a8a"

# not travel modes but the absence of an answer -- greyed so they are not read
# as a small mode share
NON_ANSWERS = {"Unknown", "Not recorded"}


def main() -> None:
    df = pd.read_excel(CLEANED)
    tid = df["trip_id"].astype(str)
    trips = df[~tid.str.startswith("Q") & ~tid.str.startswith("Z")]

    raw_distinct = trips["mode_of_travel"].dropna().astype(str).nunique()
    modes = trips["mode_of_travel"].map(norm).replace("", "Not recorded")
    counts = modes.value_counts().sort_values(ascending=True)   # largest at top
    total = int(counts.sum())

    fig, ax = plt.subplots(figsize=(11, 7.6))
    fig.subplots_adjust(top=0.91, bottom=0.25, left=0.18, right=0.97)

    colors = [MISSING if m in NON_ANSWERS else SERIES for m in counts.index]
    ax.barh(counts.index, counts.values, height=0.68, color=colors, zorder=3)
    for m, v in counts.items():
        ax.text(v + total * 0.006, m, f"{v:,}  ({v / total:.1%})",
                va="center", fontsize=9, color=MUTED)

    ax.set_title("Trips by mode of travel, cleaned Wave 2 dataset",
                 fontsize=14, color=INK, pad=12, loc="left")
    ax.set_xlabel("Trips", fontsize=10, color=MUTED)
    ax.set_xlim(0, counts.max() * 1.16)
    ax.grid(axis="x", color=GRID, linewidth=0.8, zorder=1)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9.5)

    car = int(counts.get("Auto Driver", 0) + counts.get("Auto Passenger", 0))
    fig.text(0.075, 0.175,
             f"{total:,} trips across {len(counts)} modes  ·  "
             f"{car / total:.0%} by car (driver or passenger)  ·  "
             f"{counts.get('Walk', 0) / total:.0%} on foot",
             fontsize=11.5, color=INK, va="top")
    fig.text(0.075, 0.128, (
        "Mode labels are normalised before counting. The survey embeds invisible "
        "zero-width characters in this column, so it\n"
        f"arrives with {raw_distinct} distinct values for what are really {len(counts)} "
        "modes: a second, invisibly different \"Walk\" does not match\n"
        "the first, and counting the column as-is splits Auto Driver into 1,803 + 403. "
        "Grey bars are non-answers, not modes.\n"
        "Excludes Q rows (reviewed activity gaps) and Z rows (no-travel days), neither "
        "of which is a journey."),
        fontsize=8.8, color=MUTED, va="top", linespacing=1.6)

    fig.savefig(OUT_PNG, dpi=160, facecolor="white")
    print(f"wrote {OUT_PNG}")
    print(f"  trips {total} | modes {len(counts)} (raw distinct {raw_distinct})")
    for m, v in counts.sort_values(ascending=False).items():
        print(f"    {m:<18} {v:>5}  {v / total:6.1%}")


if __name__ == "__main__":
    main()
