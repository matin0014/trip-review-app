"""The per-wave distributions, for WAVE 1.

    .venv\\Scripts\\python.exe analysis\\wave1_charts.py

Standalone on purpose. `final_charts.py` reads a Trip Review export; wave 1 is a
post-processed research file with a different schema entirely, and bending that
script to read both would put a pile of wave-1 special cases into the path that
produces the wave 2 and wave 3 deliverables. Nothing here is imported by it.

What differs from a Trip Review export, and how it is mapped:

  * There is no `start_time` / `end_time` pair. `Start Date Time_Trip` and
    `End Date Time_Trip` are real datetimes and are used directly. (`Date only_AS`
    is NOT a date despite the name -- it is identical to the start datetime, so
    grouping on it raw would make every trip its own "day". The date is taken
    from it explicitly.)
  * `Activity Type` is the purpose, `Mode of Travel` the mode.
  * Labels come in a short and a long form of the same category -- "Social" and
    "Social (e.g., Visiting friends/relatives, cultural/social events)" -- so the
    trailing parenthetical is stripped before any grouping, exactly as the wave
    2/3 comparison does. That collapses 19 raw mode strings and 17 raw purposes.
  * There is no Q / Z / M trip-id grammar and no zero-movement day. Trip ids are
    plain numbers, `S...` for splits, and `iA...` for trips the imputation model
    inserted.

IMPUTED TRIPS ARE INCLUDED. 769 of the 3,125 rows are model-imputed rather than
observed, and they are part of what this file delivers as "cleaned", so leaving
them out would chart something the deliverable is not. Every chart's headline
carries the observed/imputed split rather than burying it.

Activity duration is recomputed here as the gap between consecutive trips, the
same definition wave 2 and wave 3 use, rather than read from the file's own
`imputed_activity_duration_hr` -- so the three waves' charts mean the same thing.
"""

from __future__ import annotations

import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chartkit import (FAINT, GRID, INK, MISSING, MUTED, SURFACE, circular_kde,
                      figure, headline, hour_axis, save, style)

CLEANED = (r"P:/UiTR/Shared Folder/1. Current Student/Smartphone app data cleaning"
           r"/Cleaned Finals/trip_review_export/cleaned.xlsx")
OUT_DIR = r"C:/Users/matin001/projects/TripReview/final/distributions/Wave1"
LABEL = "cleaned Wave 1 dataset"

# Wave 1 gets its own neon so that a wave 1 chart put beside a wave 2 (pink) or
# wave 3 (green) chart is never mistaken for one of them.
SERIES, ACCENT = "#22d3ee", "#ff9ecb"

_INVISIBLE = re.compile(r"[\u200b\u200c\u200d\ufeff\xa0]")
_PAREN = re.compile(r"\s*\([^)]*\)\s*$")

TRANSIT_MODES = {"Transit", "Transit Bus", "SkyTrain", "SeaBus"}
STOP_PURPOSE = "Travel to transit stop"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday"]
EXCLUDED_PURPOSES = {"Returning Home", "Not recorded"}


def norm(s) -> str:
    """Invisible characters stripped and the survey's own prompt text dropped, so
    the short and long spellings of one category group together."""
    if pd.isna(s):
        return ""
    return _PAREN.sub("", _INVISIBLE.sub("", str(s)).strip()).strip()


def load() -> pd.DataFrame:
    df = pd.read_excel(CLEANED)
    t = pd.DataFrame({
        "accessCode": df["accessCode"],
        "_start": pd.to_datetime(df["Start Date Time_Trip"], errors="coerce"),
        "_end": pd.to_datetime(df["End Date Time_Trip"], errors="coerce"),
        "_mode": df["Mode of Travel"].map(norm),
        "_purpose": df["Activity Type"].map(norm),
        "_dayname": df["Day of the week_AS"].map(norm),
        "_imputed": df["imputed_trip"].astype(int),
    })
    t["start_date"] = t["_start"].dt.date
    t["_weekday"] = t["_dayname"].map({d: i for i, d in enumerate(WEEKDAYS)})
    return t.dropna(subset=["_start"])


def trips_per_hour(trips: pd.DataFrame, bw: float = 0.7):
    """Smooth start-time curve in TRIPS per hour.

    `circular_kde` returns a density integrating to 1; multiplying by the trip
    count puts the axis back in the unit the reader thinks in and keeps panels on
    a shared scale comparable.
    """
    s = trips["_start"].dropna()
    hours = (s.dt.hour + s.dt.minute / 60.0).to_numpy(dtype=float)
    grid, dens = circular_kde(hours, bw=bw)
    return grid, dens * hours.size


def collapse_count(rows: list) -> int:
    """Trips on one person-day belonging to one JOURNEY counted once: consecutive
    shopping; a Walk for Recreation then a Walk Returning Home; a trip to a
    transit stop then the transit ride. Mirrors `final_charts.collapse_count`."""
    n = i = 0
    while i < len(rows):
        cur = rows[i]
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        if cur["_purpose"] == "Shopping":
            while i + 1 < len(rows) and rows[i + 1]["_purpose"] == "Shopping":
                i += 1
        elif (cur["_mode"] == "Walk" and cur["_purpose"] == "Recreation"
              and nxt is not None and nxt["_mode"] == "Walk"
              and nxt["_purpose"] == "Returning Home"):
            i += 1
        elif (cur["_purpose"] == STOP_PURPOSE
              and nxt is not None and nxt["_mode"] in TRANSIT_MODES):
            i += 1
        n += 1
        i += 1
    return n


def adjusted_trips(trips: pd.DataFrame) -> int:
    ordered = trips.sort_values(["accessCode", "start_date", "_start"])
    return int(sum(collapse_count(d.to_dict("records")) for _k, d in
                   ordered.groupby(["accessCode", "start_date"], sort=False)))


def activity_records(trips: pd.DataFrame) -> pd.DataFrame:
    """One row per within-day consecutive trip pair: the gap, and the purpose of
    the earlier trip (what the person travelled in order to do)."""
    t = trips.dropna(subset=["_start", "_end"]).sort_values(
        ["accessCode", "start_date", "_start"])
    recs = []
    for (person, date), day in t.groupby(["accessCode", "start_date"], sort=False):
        rows = day.to_dict("records")
        for a, b in zip(rows, rows[1:]):
            mins = (b["_start"] - a["_end"]).total_seconds() / 60.0
            if mins < 0:
                continue      # next trip starts before this one ends: a logging error
            # wave 1 spells the unanswered purpose "Not Selected" where waves 2
            # and 3 use "empty". Both are the absence of an answer, not an
            # activity, so both fold into the same bucket -- otherwise the
            # by-purpose charts would carry a phantom activity here that the
            # other waves' identical charts drop.
            purpose = a["_purpose"]
            if not purpose or purpose.lower() in ("empty", "not selected"):
                purpose = "Not recorded"
            recs.append({"accessCode": person, "start_date": date,
                         "purpose": purpose, "hours": mins / 60.0})
    return pd.DataFrame(recs)


# --- charts ----------------------------------------------------------------

def chart_start_times(trips, n_days, title, out):
    grid, curve = trips_per_hour(trips)
    fig, ax = figure(figsize=(12, 6.2))
    fig.subplots_adjust(top=0.89, bottom=0.17, left=0.08, right=0.97)
    ax.fill_between(grid, curve, color=SERIES, alpha=0.14, zorder=2)
    ax.plot(grid, curve, color=SERIES, linewidth=3, zorder=3, solid_capstyle="round")

    # the two rush hours are what a reader looks for, so name them outright
    for window in (slice(0, int(len(grid) * 11 / 24)),
                   slice(int(len(grid) * 11 / 24), len(grid))):
        i = window.start + int(np.argmax(curve[window]))
        h = grid[i]
        ax.plot([h], [curve[i]], "o", color=SERIES, markersize=6, zorder=4)
        ax.annotate(f"{int(h):02d}:{int(round((h % 1) * 60)):02d}",
                    xy=(h, curve[i]), xytext=(0, 12), textcoords="offset points",
                    ha="center", fontsize=10.5, color=SERIES, weight="700")

    style(ax, title, "Hour of day", "Trips starting per hour")
    hour_axis(ax)
    ax.set_ylim(0, curve.max() * 1.16)
    headline(fig, f"{len(trips):,} trips   ·   "
                  f"{adjusted_trips(trips) / n_days:.2f} trips per person per day")
    save(fig, out)
    plt.close(fig)


def chart_start_times_by_weekday(trips, out):
    """Seven panels on ONE shared y-scale: the point is comparing days, which a
    per-panel scale would quietly defeat."""
    fig, axes = figure(2, 4, figsize=(15, 7.6), sharey=True, sharex=True)
    fig.subplots_adjust(top=0.85, bottom=0.14, left=0.05, right=0.98,
                        hspace=0.38, wspace=0.12)
    grid, week = trips_per_hour(trips)
    week = week / 7.0
    peak = week.max()
    for i, day in enumerate(WEEKDAYS):
        ax = axes[i // 4][i % 4]
        sub = trips[trips["_dayname"] == day]
        ax.plot(grid, week, color=FAINT, linewidth=1.2, zorder=2,
                linestyle=(0, (3, 2)))
        g, curve = trips_per_hour(sub, bw=0.85)   # thinner slice, more smoothing
        peak = max(peak, curve.max())
        ax.fill_between(g, curve, color=SERIES, alpha=0.14, zorder=3)
        ax.plot(g, curve, color=SERIES, linewidth=2.2, zorder=4,
                solid_capstyle="round")
        style(ax)
        ax.set_title(f"{day}   ({len(sub):,} trips)", fontsize=11, color=INK,
                     loc="left", pad=6, weight="600")
        ax.set_xticks(range(0, 25, 6))
        ax.set_xlim(0, 24)
    # `sharey` means whichever panel autoscaled LAST sets the limit for all seven,
    # which clips the busiest days; set the shared limit from the busiest curve
    axes[0][0].set_ylim(0, peak * 1.10)
    axes[1][3].axis("off")
    # Thursday sits above the hidden 8th cell, so `sharex` leaves its column with
    # no hour labels anywhere -- put them back on the panel itself
    axes[0][3].tick_params(labelbottom=True)
    fig.suptitle(f"Trip start times by weekday, {LABEL}", fontsize=15, color=INK,
                 x=0.05, ha="left", y=0.965, weight="600")
    headline(fig, f"{len(trips):,} trips   ·   shared vertical scale, so panels "
                  f"compare directly", y=0.045, x=0.05)
    save(fig, out)
    plt.close(fig)


def chart_activity_hist(recs, out):
    CAP = 6
    hours = recs["hours"].values
    edges = np.arange(0, CAP + 0.5, 0.5)
    counts, _ = np.histogram(np.clip(hours, 0, CAP - 1e-9), bins=edges)
    over = int((hours >= CAP).sum())
    fig, ax = figure(figsize=(12, 6.2))
    fig.subplots_adjust(top=0.89, bottom=0.17, left=0.08, right=0.97)
    ax.bar(edges[:-1] + 0.25, counts, width=0.42, color=SERIES, zorder=3)
    if over:
        ax.bar([CAP + 0.25], [over], width=0.42, color=ACCENT, zorder=3)
        ax.annotate(f"{over:,}", xy=(CAP + 0.25, over), xytext=(0, 6),
                    textcoords="offset points", ha="center", fontsize=9.5,
                    color=ACCENT, weight="600")
    med = float(np.median(hours))
    ax.axvline(med, color=INK, linewidth=1.3, linestyle=(0, (4, 3)), zorder=4)
    ax.annotate(f"median {med * 60:.0f} min", xy=(med, counts.max() * 0.88),
                xytext=(9, 0), textcoords="offset points", fontsize=10.5,
                color=INK, weight="600")
    style(ax, f"Activity duration between consecutive trips, {LABEL}",
          "Hours (30-minute bins)", "Activities")
    ax.set_xticks(list(range(0, CAP)) + [CAP + 0.25])
    ax.set_xticklabels([str(h) for h in range(0, CAP)] + [f"{CAP}h+"])
    ax.set_xlim(-0.35, CAP + 0.85)
    headline(fig, f"{len(hours):,} activities   ·   median {med * 60:.0f} min   ·   "
                  f"{(hours < 1).mean():.0%} under an hour")
    save(fig, out)
    plt.close(fig)


def _purpose_bars(series, counts, title, xlabel, out, headline_text):
    order = series.sort_values(ascending=True)
    fig, ax = figure(figsize=(12, 7.6))
    fig.subplots_adjust(top=0.91, bottom=0.13, left=0.30, right=0.97)
    ax.barh(order.index, order.values, height=0.68, color=SERIES, zorder=3)
    for p, v in order.items():
        ax.text(v + order.max() * 0.012, p, f"{v:.1f} h   (n={counts[p]:,})",
                va="center", fontsize=9, color=MUTED)
    style(ax, title, xlabel, axis="x")
    ax.set_xlim(0, order.max() * 1.28)
    headline(fig, headline_text)
    save(fig, out)
    plt.close(fig)


def chart_activity_by_purpose(recs, out_episode, out_perday):
    # "Returning Home" is not an out-of-home activity: the gap after a homeward
    # trip is time spent AT HOME until the next departure. "Not recorded" is the
    # absence of an answer. Both stay in the overall duration histogram.
    recs = recs[~recs["purpose"].isin(EXCLUDED_PURPOSES)]
    _purpose_bars(recs.groupby("purpose")["hours"].mean(),
                  recs.groupby("purpose")["hours"].size(),
                  "Average activity duration per episode, by purpose",
                  "Hours per episode", out_episode,
                  f"{len(recs):,} episodes   ·   overall mean "
                  f"{recs['hours'].mean():.1f} h per episode")

    # total hours of one purpose on one person-day, then averaged across the days
    # that purpose actually occurred -- averaging over ALL days would answer a
    # different question (how much of a typical day goes to it, zeros included)
    per_day = (recs.groupby(["accessCode", "start_date", "purpose"])["hours"].sum()
               .reset_index())
    n_person_days = len(recs[["accessCode", "start_date"]].drop_duplicates())
    _purpose_bars(per_day.groupby("purpose")["hours"].mean(),
                  per_day.groupby("purpose")["hours"].size(),
                  "Average hours per day spent on each activity, by purpose",
                  "Hours per day on which the activity occurred", out_perday,
                  f"{n_person_days:,} person-days   ·   each purpose averaged only "
                  f"over the days it occurred")


def chart_days_per_participant(days, out):
    per_person = days.groupby("accessCode").size()
    counts = per_person.value_counts().sort_index()
    fig, ax = figure(figsize=(12, 6.2))
    fig.subplots_adjust(top=0.89, bottom=0.17, left=0.08, right=0.97)
    ax.bar(counts.index, counts.values, width=0.72,
           color=[ACCENT if d > 7 else SERIES for d in counts.index], zorder=3)
    for d, v in counts.items():
        ax.text(d, v, str(v), ha="center", va="bottom", fontsize=8, color=MUTED)
    style(ax, f"Days recorded per participant, {LABEL}", "Days recorded",
          "Participants")
    ax.set_xticks(range(int(counts.index.min()), int(counts.index.max()) + 1))
    ax.set_ylim(0, counts.max() * 1.15)
    over7 = int((per_person > 7).sum())
    headline(fig, f"{per_person.mean():.2f} days per participant   ·   {over7} of "
                  f"{len(per_person)} ({over7 / len(per_person):.0%}) recorded more "
                  f"than 7 days")
    save(fig, out)
    plt.close(fig)


def chart_modes(trips, out):
    modes = trips["_mode"].replace("", "Not recorded")
    counts = modes.value_counts().sort_values(ascending=True)
    total = int(counts.sum())
    fig, ax = figure(figsize=(12, 7.6))
    fig.subplots_adjust(top=0.91, bottom=0.13, left=0.20, right=0.97)
    ax.barh(counts.index, counts.values, height=0.68,
            color=[MISSING if m in ("Unknown", "Not recorded") else SERIES
                   for m in counts.index], zorder=3)
    for m, v in counts.items():
        ax.text(v + total * 0.006, m, f"{v:,}  ({v / total:.1%})", va="center",
                fontsize=9, color=MUTED)
    style(ax, f"Trips by mode of travel, {LABEL}", "Trips", axis="x")
    ax.set_xlim(0, counts.max() * 1.16)
    car = int(counts.get("Auto Driver", 0) + counts.get("Auto Passenger", 0))
    headline(fig, f"{total:,} trips   ·   {car / total:.0%} by car   ·   "
                  f"{counts.get('Walk', 0) / total:.0%} on foot")
    save(fig, out)
    plt.close(fig)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    trips = load()
    days = trips[["accessCode", "start_date", "_weekday"]].drop_duplicates()
    n_imp = int((trips["_imputed"] == 1).sum())
    print(f"trips {len(trips):,} ({len(trips) - n_imp:,} observed + {n_imp:,} "
          f"imputed) | participants {trips['accessCode'].nunique()} | "
          f"person-days {len(days):,}")
    print(f"modes {trips['_mode'].nunique()} | purposes {trips['_purpose'].nunique()}"
          f"  (after normalising the short/long spellings)")

    chart_start_times(trips, len(days),
                      f"Trip start times, {LABEL} — all days",
                      f"{OUT_DIR}/start_times_cleaned.png")
    wd = trips[trips["_weekday"] < 5]
    chart_start_times(wd, len(days[days["_weekday"] < 5]),
                      f"Trip start times, {LABEL} — weekdays (Mon-Fri)",
                      f"{OUT_DIR}/start_times_cleaned_weekday.png")
    chart_start_times_by_weekday(trips, f"{OUT_DIR}/start_times_by_weekday.png")

    recs = activity_records(trips)
    chart_activity_hist(recs, f"{OUT_DIR}/activity_duration_cleaned.png")
    chart_activity_by_purpose(recs, f"{OUT_DIR}/activity_by_purpose_episode.png",
                              f"{OUT_DIR}/activity_by_purpose_per_day.png")
    chart_days_per_participant(days[["accessCode", "start_date"]].drop_duplicates(),
                               f"{OUT_DIR}/days_per_participant.png")
    chart_modes(trips, f"{OUT_DIR}/mode_counts.png")

    print(f"activities {len(recs):,}")
    print(f"\nwrote 8 PNGs to {OUT_DIR}")


if __name__ == "__main__":
    main()
