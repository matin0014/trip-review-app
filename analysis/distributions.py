"""Raw-vs-cleaned distribution charts (Trip Review Tool QC deliverable).

Two charts, each produced for the RAW file and the CLEANED dataset (cleaned =
every proposal auto-accepted via the engine's apply_all):

1. **Activity duration by weekday** — activity duration is the gap between a
   trip's end and the next consecutive trip's start within a person-day; labelled
   by the activity's purpose = the EARLIER trip A's purpose (missing -> 'None').
   Rendered as one stacked histogram per weekday.
2. **Trip start times** — count of trips by hour of day, raw vs cleaned.

    python analysis/distributions.py [raw_xlsx]

Outputs PNGs to analysis/output/. Dev-only (matplotlib is not a shipped-app dep).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

import matplotlib

matplotlib.use("Agg")  # headless: write files, no display
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

# Allow running as a plain script (python analysis/distributions.py): put the
# project root (this file's parent dir) on the path so `engine` imports resolve.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.classify import classify_all, group_person_days  # noqa: E402
from engine.loader import load_trips  # noqa: E402
from engine.proposals import apply_all  # noqa: E402

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DUR_MAX_MIN = 180          # activities longer than this fall in the top bin
DUR_BIN_MIN = 15
N_PURPOSES = 8             # top purposes kept distinct; the rest become CATCH_ALL
CATCH_ALL = "(other purposes)"  # distinct from the real purpose literally named "Other"
DEFAULT_RAW = os.path.join("data", "app_data_wave2_raw.xlsx")
OUT_DIR = os.path.join("analysis", "output")


def activity_records(trips: list) -> list:
    """(weekday, purpose, duration_min) for each consecutive within-day pair.

    Duration = next.start - this.end (minutes); negative gaps (overlaps) skipped.
    Purpose = the earlier trip's purpose, or 'None' when unlabelled.
    """
    recs = []
    for day in group_person_days(trips).values():
        for a, b in zip(day, day[1:]):
            dur = (b.start_ts - a.end_ts).total_seconds() / 60.0
            if dur < 0:
                continue
            recs.append((a.end_ts.strftime("%A"), a.purpose or "None", dur))
    return recs


def purpose_categories(recs: list) -> list:
    """Top purposes (kept distinct); everything else folds into CATCH_ALL."""
    common = [p for p, _ in Counter(p for _, p, _ in recs).most_common(N_PURPOSES)]
    common = list(dict.fromkeys(common))  # dedupe, preserve order
    return common + [CATCH_ALL]


def _category(purpose: str, categories: list) -> str:
    return purpose if purpose in categories else CATCH_ALL


def chart_activity(recs: list, title: str, path: str, categories: list) -> None:
    edges = list(range(0, DUR_MAX_MIN + DUR_BIN_MIN, DUR_BIN_MIN))
    palette = {c: plt.cm.tab10(i % 10) for i, c in enumerate(categories)}

    fig, axes = plt.subplots(2, 4, figsize=(18, 8), sharex=True, sharey=True)
    axes = axes.flatten()
    for wi, weekday in enumerate(WEEKDAYS):
        ax = axes[wi]
        bottom = np.zeros(len(edges) - 1)
        for c in categories:
            durs = [
                min(d, DUR_MAX_MIN - 1e-6)
                for (w, p, d) in recs
                if w == weekday and _category(p, categories) == c
            ]
            counts, _ = np.histogram(durs, bins=edges)
            ax.bar(edges[:-1], counts, width=DUR_BIN_MIN, bottom=bottom,
                   align="edge", color=palette[c], label=c)
            bottom += counts
        ax.set_title(f"{weekday}  (n={int(bottom.sum())})", fontsize=10)
        ax.set_xlabel("activity duration (min, capped 180)")
    axes[0].set_ylabel("count")
    axes[7].axis("off")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower right", ncol=2, fontsize=9,
               title="activity purpose")
    fig.suptitle(title, fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(path, dpi=110)
    plt.close(fig)


def chart_start_times(raw: list, cleaned: list, path: str) -> None:
    fig, ax = plt.subplots(figsize=(11, 5))
    for trips, label, color in [(raw, "raw", "tab:blue"), (cleaned, "cleaned", "tab:orange")]:
        hours = np.bincount([t.start_ts.hour for t in trips], minlength=24)
        ax.plot(range(24), hours, marker="o", color=color, label=f"{label} (n={len(trips)})")
    ax.set_xticks(range(24))
    ax.set_xlabel("hour of day")
    ax.set_ylabel("number of trips")
    ax.set_title("Trip start times — raw vs cleaned")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("raw_xlsx", nargs="?", default=DEFAULT_RAW)
    args = ap.parse_args()

    raw = load_trips(args.raw_xlsx)
    cleaned = apply_all(raw, classify_all(raw))
    os.makedirs(OUT_DIR, exist_ok=True)

    raw_recs = activity_records(raw)
    cleaned_recs = activity_records(cleaned)
    categories = purpose_categories(raw_recs)

    chart_activity(raw_recs, "Activity duration by weekday - RAW",
                   os.path.join(OUT_DIR, "activity_raw.png"), categories)
    chart_activity(cleaned_recs, "Activity duration by weekday - CLEANED",
                   os.path.join(OUT_DIR, "activity_cleaned.png"), categories)
    chart_start_times(raw, cleaned, os.path.join(OUT_DIR, "start_times.png"))

    print(f"raw trips: {len(raw)}   cleaned trips: {len(cleaned)}")
    print(f"activities (gaps): raw {len(raw_recs)}   cleaned {len(cleaned_recs)}")
    rd = np.median([d for _, _, d in raw_recs]) if raw_recs else 0
    cd = np.median([d for _, _, d in cleaned_recs]) if cleaned_recs else 0
    print(f"median activity duration: raw {rd:.1f} min   cleaned {cd:.1f} min")
    short_raw = sum(1 for _, _, d in raw_recs if d < 15)
    short_cln = sum(1 for _, _, d in cleaned_recs if d < 15)
    print(f"activities < 15 min: raw {short_raw}   cleaned {short_cln} "
          f"({short_raw - short_cln} fewer after cleaning)")
    print(f"purpose categories: {categories}")
    print(f"wrote 3 PNGs to {OUT_DIR}/")


if __name__ == "__main__":
    main()
