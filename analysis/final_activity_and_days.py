"""Activity-duration and days-per-participant distributions for the combined
cleaned file. Writes two PNGs next to the input and prints the figures.

Activity duration follows the definition already used by `analysis/distributions.py`
so the two agree: the gap from one trip's END to the next trip's START, between
consecutive trips WITHIN one person-day. Overnight is therefore not an activity
(the participant's day ended), and a negative gap -- the next trip starting
before the previous one finished, which the raw survey does contain -- is
skipped rather than clamped to zero, since it is a logging error, not a
zero-length stay.

As in the start-times script, Q rows (a reviewed gap in ACTIVITY) and Z rows (a
day with no travel) are not trips and take no part in the pairing. Z days do
count as days a participant was surveyed.
"""

from __future__ import annotations

import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

CLEANED = r"C:/Users/matin001/projects/TripReview/final/cleaned.xlsx"
OUT_DIR = r"C:/Users/matin001/projects/TripReview/final"

INK, MUTED, GRID = "#1a1a1a", "#5c5c5c", "#d8d8d8"
SERIES = "#2b6cb0"
ACCENT = "#c05621"

_TZ = re.compile(r"([+-]\d{2}:?\d{2})$")


def stamp(date, tm):
    """A timestamp from the workbook's separate date and time columns. The time
    carries a -08:00 offset; every row in a wave shares it, so it is stripped
    rather than parsed -- the gaps here are within one local day."""
    if pd.isna(date) or pd.isna(tm):
        return pd.NaT
    return pd.to_datetime(f"{str(date)[:10]} {_TZ.sub('', str(tm)).strip()}",
                          errors="coerce")


def _frame(ax, title, xlabel, ylabel):
    ax.set_title(title, fontsize=14, color=INK, pad=12, loc="left")
    ax.set_xlabel(xlabel, fontsize=10, color=MUTED)
    ax.set_ylabel(ylabel, fontsize=10, color=MUTED)
    ax.grid(axis="y", color=GRID, linewidth=0.8, zorder=1)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)


def activity_gaps(trips: pd.DataFrame) -> tuple:
    """``(durations_minutes, n_negative_skipped)`` over within-day consecutive pairs."""
    t = trips.dropna(subset=["_start", "_end"]).sort_values(
        ["accessCode", "start_date", "_start"])
    gaps, negative = [], 0
    for _key, day in t.groupby(["accessCode", "start_date"], sort=False):
        rows = day.to_dict("records")
        for a, b in zip(rows, rows[1:]):
            mins = (b["_start"] - a["_end"]).total_seconds() / 60.0
            if mins < 0:
                negative += 1
            else:
                gaps.append(mins)
    return np.array(gaps), negative


def chart_activity(gaps, negative, out_png) -> dict:
    """Histogram of activity duration in hours, with a final overflow bin so the
    long tail cannot squash the bulk of the distribution into one bar."""
    CAP_H = 12
    hours = gaps / 60.0
    edges = np.arange(0, CAP_H + 0.5, 0.5)
    counts, _ = np.histogram(np.clip(hours, 0, CAP_H - 1e-9), bins=edges)
    over = int((hours >= CAP_H).sum())

    fig, ax = plt.subplots(figsize=(11, 6.6))
    fig.subplots_adjust(top=0.90, bottom=0.30, left=0.075, right=0.98)
    centres = edges[:-1] + 0.25
    ax.bar(centres, counts, width=0.44, color=SERIES, zorder=3)
    if over:
        ax.bar([CAP_H + 0.25], [over], width=0.44, color=ACCENT, zorder=3)
        ax.text(CAP_H + 0.25, over, f" {over:,}\n {CAP_H}h+", ha="center", va="bottom",
                fontsize=8.5, color=ACCENT)

    med = float(np.median(hours))
    ax.axvline(med, color=INK, linewidth=1.2, linestyle="--", zorder=4)
    ax.annotate(f"median {med:.1f} h", xy=(med, ax.get_ylim()[1]),
                xytext=(6, -12), textcoords="offset points",
                fontsize=9, color=INK, ha="left", va="top")

    _frame(ax, "Activity duration between consecutive trips, cleaned Wave 2 dataset",
           "Activity duration (hours, 30-minute bins)", "Activities")
    # no tick at CAP_H itself: it would sit on top of the overflow bar's label
    ax.set_xticks(list(range(0, CAP_H)) + [CAP_H + 0.25])
    ax.set_xticklabels([str(h) for h in range(0, CAP_H)] + [f"{CAP_H}h+"])
    ax.set_xlim(-0.3, CAP_H + 0.8)

    q1, q3 = np.percentile(hours, [25, 75])
    fig.text(0.075, 0.175,
             f"{len(hours):,} activities  ·  median {med:.1f} h  ·  "
             f"middle half {q1:.1f}–{q3:.1f} h  ·  mean {hours.mean():.1f} h",
             fontsize=11.5, color=INK, va="top")
    fig.text(0.075, 0.128, (
        "An activity is the gap from one trip's END to the next trip's START within the "
        "same person-day, so the overnight gap\n"
        f"is not an activity. The last bar pools everything at {CAP_H} h or longer "
        f"({over:,} activities); {negative:,} pairs were skipped because the\n"
        "next trip starts before the previous one ends, which is a logging error rather "
        "than a zero-length stay."),
        fontsize=8.8, color=MUTED, va="top", linespacing=1.6)
    fig.savefig(out_png, dpi=160, facecolor="white")
    return {"n": len(hours), "median_h": med, "mean_h": float(hours.mean()),
            "q1": float(q1), "q3": float(q3), "over": over, "negative": negative}


def chart_days(per_person: pd.Series, out_png) -> dict:
    """How many days each participant contributed."""
    counts = per_person.value_counts().sort_index()
    fig, ax = plt.subplots(figsize=(11, 6.6))
    fig.subplots_adjust(top=0.90, bottom=0.28, left=0.075, right=0.98)

    colors = [ACCENT if d > 7 else SERIES for d in counts.index]
    ax.bar(counts.index, counts.values, width=0.72, color=colors, zorder=3)
    for d, v in counts.items():
        ax.text(d, v, str(v), ha="center", va="bottom", fontsize=8, color=MUTED)

    _frame(ax, "Days recorded per participant, cleaned Wave 2 dataset",
           "Days recorded", "Participants")
    ax.set_xticks(range(int(counts.index.min()), int(counts.index.max()) + 1))
    ax.set_ylim(0, counts.max() * 1.15)

    n_people = len(per_person)
    mean_d = float(per_person.mean())
    over7 = int((per_person > 7).sum())
    fig.text(0.075, 0.155,
             f"{mean_d:.2f} days per participant on average  ·  "
             f"{over7} of {n_people} participants ({over7 / n_people:.0%}) "
             f"recorded more than 7 days",
             fontsize=11.5, color=INK, va="top")
    fig.text(0.075, 0.105, (
        f"Median {per_person.median():.0f} days, range {int(per_person.min())}–"
        f"{int(per_person.max())}. Bars beyond 7 days are highlighted.\n"
        "A day counts if the participant has any row in the cleaned file for it, "
        "including a day on which they did not travel.\n"
        "Days removed during review are not counted, so this is days DELIVERED, "
        "not days originally reported."),
        fontsize=8.8, color=MUTED, va="top", linespacing=1.6)
    fig.savefig(out_png, dpi=160, facecolor="white")
    return {"people": n_people, "mean": mean_d, "median": float(per_person.median()),
            "over7": over7, "min": int(per_person.min()), "max": int(per_person.max())}


def main() -> None:
    df = pd.read_excel(CLEANED)
    tid = df["trip_id"].astype(str)
    trips = df[~tid.str.startswith("Q") & ~tid.str.startswith("Z")].copy()
    trips["_start"] = [stamp(d, t) for d, t in zip(trips["start_date"], trips["start_time"])]
    trips["_end"] = [stamp(d, t) for d, t in zip(trips["end_date"], trips["end_time"])]

    gaps, negative = activity_gaps(trips)
    a = chart_activity(gaps, negative, f"{OUT_DIR}/activity_duration_cleaned.png")
    print("[activity duration]")
    print(f"  activities {a['n']} | median {a['median_h']:.2f} h | mean {a['mean_h']:.2f} h"
          f" | IQR {a['q1']:.2f}-{a['q3']:.2f} h")
    print(f"  >=12h pooled: {a['over']} | negative gaps skipped: {a['negative']}")

    # a day the participant appears on at all, no-travel days included
    days = df.dropna(subset=["accessCode", "start_date"])[
        ["accessCode", "start_date"]].drop_duplicates()
    per_person = days.groupby("accessCode").size()
    d = chart_days(per_person, f"{OUT_DIR}/days_per_participant.png")
    print("[days per participant]")
    print(f"  participants {d['people']} | mean {d['mean']:.2f} | median {d['median']:.0f}"
          f" | range {d['min']}-{d['max']}")
    print(f"  more than 7 days: {d['over7']}")
    print("  distribution:", per_person.value_counts().sort_index().to_dict())


if __name__ == "__main__":
    main()
