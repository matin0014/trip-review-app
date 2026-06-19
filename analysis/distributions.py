"""Raw-vs-cleaned distribution charts (Trip Review Tool QC deliverable).

Two charts, each produced for the RAW file and the CLEANED dataset (cleaned =
every proposal auto-accepted via the engine's apply_all):

1. **Activity duration by purpose** — activity duration is the gap between a
   trip's end and the next consecutive trip's start within a person-day; labelled
   by the activity's purpose = the EARLIER trip A's purpose (missing -> 'None').
   Rendered as horizontal bars of AVERAGE duration per purpose (raw vs cleaned),
   plus a per-weekday faceted version of the same.
2. **Trip start times** — count of trips by hour of day, raw vs cleaned.

    python analysis/distributions.py [raw_xlsx]

Outputs PNGs to analysis/output/. Dev-only (matplotlib is not a shipped-app dep).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

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
MIN_COUNT = 10             # overall: drop purposes with fewer activities (noisy averages)
WEEKDAY_MIN_COUNT = 8      # per-weekday panel: higher bar (smaller samples) to suppress noise
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
            purpose = a.purpose or "None"
            if purpose.strip().lower() == "empty":
                purpose = "None"  # survey placeholder, not a real purpose
            recs.append((a.end_ts.strftime("%A"), purpose, dur))
    return recs


def mean_hours_by_purpose(recs: list, min_count: int = 1) -> dict:
    """purpose -> (mean duration in HOURS, n), keeping only purposes with >= min_count."""
    vals: dict = defaultdict(list)
    for _, purpose, dur in recs:
        vals[purpose].append(dur)
    return {p: (np.mean(v) / 60.0, len(v)) for p, v in vals.items() if len(v) >= min_count}


def _ordered_purposes(raw_recs: list) -> list:
    """Purposes (>= MIN_COUNT in raw) ordered ascending by mean duration, so the
    longest activities sit at the TOP of a horizontal bar chart (photo style)."""
    means = mean_hours_by_purpose(raw_recs, MIN_COUNT)
    return sorted(means, key=lambda p: means[p][0])


def _grouped_barh(ax, purposes, raw_means, cleaned_means) -> None:
    y = np.arange(len(purposes))
    h = 0.4
    ax.barh(y + h / 2, [raw_means.get(p, (0,))[0] for p in purposes],
            height=h, color="tab:blue", label="raw")
    ax.barh(y - h / 2, [cleaned_means.get(p, (0,))[0] for p in purposes],
            height=h, color="tab:orange", label="cleaned")
    ax.set_yticks(y)
    ax.set_yticklabels(purposes)
    ax.grid(axis="x", alpha=0.3)


def chart_activity_avg(raw_recs, cleaned_recs, purposes, path) -> None:
    """Overall: average activity duration per purpose, raw vs cleaned (photo style)."""
    fig, ax = plt.subplots(figsize=(12, max(4, 0.5 * len(purposes))))
    _grouped_barh(ax, purposes,
                  mean_hours_by_purpose(raw_recs, MIN_COUNT),
                  mean_hours_by_purpose(cleaned_recs, 1))
    ax.set_xlabel("average activity duration (hours)")
    ax.set_title("Average activity duration by purpose - raw vs cleaned")
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def chart_activity_weekday(raw_recs, cleaned_recs, purposes, path) -> None:
    """Per-weekday facets of the same average-duration bars (unstacked)."""
    fig, axes = plt.subplots(2, 4, figsize=(20, 10), sharex=True)
    axes = axes.flatten()
    for wi, weekday in enumerate(WEEKDAYS):
        ax = axes[wi]
        rr = [r for r in raw_recs if r[0] == weekday]
        cc = [r for r in cleaned_recs if r[0] == weekday]
        _grouped_barh(ax, purposes,
                      mean_hours_by_purpose(rr, WEEKDAY_MIN_COUNT),
                      mean_hours_by_purpose(cc, WEEKDAY_MIN_COUNT))
        ax.set_title(f"{weekday}  (n={len(rr)})", fontsize=10)
        ax.set_xlabel("avg duration (h)")
        ax.tick_params(axis="y", labelsize=7)
    axes[7].axis("off")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower right", fontsize=11)
    fig.suptitle("Average activity duration by purpose, per weekday - raw vs cleaned",
                 fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(path, dpi=110)
    plt.close(fig)


def rounded_hour(ts) -> int:
    """Start time rounded to the NEAREST hour: minute >= 30 rounds up, else down.
    Wraps 24 -> 0 so a 23:30+ trip lands at midnight (hour-of-day is circular)."""
    return (ts.hour + (1 if ts.minute >= 30 else 0)) % 24


def chart_start_times(raw: list, cleaned: list, path: str, weekday_only: bool = False) -> None:
    def keep(t) -> bool:
        return t.start_ts.weekday() < 5 if weekday_only else True

    fig, ax = plt.subplots(figsize=(11, 5))
    for trips, label, color in [(raw, "raw", "tab:blue"), (cleaned, "cleaned", "tab:orange")]:
        sel = [t for t in trips if keep(t)]
        hours = np.bincount([rounded_hour(t.start_ts) for t in sel], minlength=24)
        ax.plot(range(24), hours, marker="o", color=color, label=f"{label} (n={len(sel)})")
    scope = "weekdays only (Mon-Fri)" if weekday_only else "all days"
    ax.set_xticks(range(24))
    ax.set_xlabel("hour of day (start time rounded to nearest hour)")
    ax.set_ylabel("number of trips")
    ax.set_title(f"Trip start times, {scope} - raw vs cleaned")
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
    purposes = _ordered_purposes(raw_recs)

    chart_activity_avg(raw_recs, cleaned_recs, purposes,
                       os.path.join(OUT_DIR, "activity_avg.png"))
    chart_activity_weekday(raw_recs, cleaned_recs, purposes,
                           os.path.join(OUT_DIR, "activity_avg_by_weekday.png"))
    chart_start_times(raw, cleaned, os.path.join(OUT_DIR, "start_times.png"))
    chart_start_times(raw, cleaned, os.path.join(OUT_DIR, "start_times_weekday.png"),
                      weekday_only=True)

    print(f"raw trips: {len(raw)}   cleaned trips: {len(cleaned)}")
    print(f"activities (gaps): raw {len(raw_recs)}   cleaned {len(cleaned_recs)}")
    rm = mean_hours_by_purpose(raw_recs, MIN_COUNT)
    cm = mean_hours_by_purpose(cleaned_recs, 1)
    print("\navg activity duration by purpose (hours)  raw -> cleaned:")
    for p in reversed(purposes):
        print(f"  {p:<40} {rm[p][0]:5.2f} -> {cm.get(p,(0,))[0]:5.2f}   (n={rm[p][1]})")
    print(f"\nwrote 4 PNGs to {OUT_DIR}/")


if __name__ == "__main__":
    main()
