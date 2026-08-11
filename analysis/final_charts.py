"""Every chart profiling the combined cleaned file, in one place.

Deliberately sparse: a title, the marks, and one bold headline. The caveats that
used to sit under each figure are recorded in the module docstrings and the
codebook instead, so the PNGs can go straight into a deck.

Definitions used throughout, so the charts agree with each other and with the app:

  * A TRIP is a plain / M / A / S row. Q rows mark a reviewed gap in ACTIVITY
    between two trips and Z rows stand for a whole day with no travel; neither is
    a journey, so neither is ever counted as one. Z days do count as days.
  * An ACTIVITY is the gap from one trip's END to the next trip's START within
    one person-day (the definition in `analysis/distributions.py`), so overnight
    is not an activity. Its purpose is the EARLIER trip's purpose: you travel
    *in order to* do the thing you then do.
  * Mode and purpose are normalised before any grouping. The survey embeds
    zero-width characters, so the raw columns hold 21 mode strings for 14 real
    modes -- "Walk" and an invisibly different "Walk" do not match, and Auto
    Driver splits into 1,803 + 403.

Trip counts are never rewritten by the journey-folding rules; those affect only
the trips-per-person-day average (see `collapse_count`).
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
SERIES, ACCENT, MISSING = "#2b6cb0", "#c05621", "#8a8a8a"

_INVISIBLE = re.compile(r"[\u200b\u200c\u200d\ufeff\xa0]")
_TZ = re.compile(r"([+-]\d{2}:?\d{2})$")

TRANSIT_MODES = {"Transit", "Transit Bus", "SkyTrain", "SeaBus"}
STOP_PURPOSE = "Travel to transit stop"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday"]


# --- shared helpers --------------------------------------------------------

def norm(s) -> str:
    return "" if pd.isna(s) else _INVISIBLE.sub("", str(s)).strip()


def stamp(date, tm):
    if pd.isna(date) or pd.isna(tm):
        return pd.NaT
    return pd.to_datetime(f"{str(date)[:10]} {_TZ.sub('', str(tm)).strip()}",
                          errors="coerce")


def rounded_hour(hhmm):
    """Minute >= 30 rounds up; 23:30+ wraps to 0. Mirrors `app.charts`."""
    m = re.match(r"^(\d{1,2}):(\d{2})", str(hhmm))
    if not m:
        return None
    return (int(m.group(1)) + (1 if int(m.group(2)) >= 30 else 0)) % 24


def headline(fig, text, y=0.045):
    fig.text(0.075, y, text, fontsize=12.5, color=INK, va="bottom", weight="bold")


def frame(ax, title=None, xlabel=None, ylabel=None, axis="y"):
    if title:
        ax.set_title(title, fontsize=14, color=INK, pad=12, loc="left")
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=10, color=MUTED)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=10, color=MUTED)
    ax.grid(axis=axis, color=GRID, linewidth=0.8, zorder=1)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)


def collapse_count(day: pd.DataFrame) -> int:
    """Trips on one person-day with one JOURNEY counted once: consecutive
    shopping; a Walk for Recreation then a Walk Returning Home; a trip to a
    transit stop then the transit ride."""
    rows = day.to_dict("records")
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
    ordered = trips.sort_values(["accessCode", "start_date", "start_time"])
    return int(sum(collapse_count(d) for _k, d in
                   ordered.groupby(["accessCode", "start_date"], sort=False)))


def hour_bins(trips: pd.DataFrame) -> pd.Series:
    h = trips["start_time"].map(rounded_hour).dropna()
    return h.astype(int).value_counts().reindex(range(24), fill_value=0)


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
                continue  # next trip starts before this one ends: a logging error
            # "empty" is the survey's own placeholder for an unanswered purpose,
            # not a purpose (see analysis/distributions.py), so it joins the blanks
            purpose = a["_purpose"]
            if not purpose or purpose.lower() == "empty":
                purpose = "Not recorded"
            recs.append({"accessCode": person, "start_date": date,
                         "purpose": purpose, "hours": mins / 60.0})
    return pd.DataFrame(recs)


# --- charts ----------------------------------------------------------------

def chart_start_times(trips, pdays, title, out):
    counts = hour_bins(trips)
    fig, ax = plt.subplots(figsize=(11, 6.0))
    fig.subplots_adjust(top=0.90, bottom=0.15, left=0.075, right=0.98)
    ax.plot(counts.index, counts.values, marker="o", markersize=5, linewidth=2,
            color=SERIES, zorder=3)
    ax.fill_between(counts.index, counts.values, alpha=0.10, color=SERIES, zorder=2)
    frame(ax, title, "Hour of day", "Trips")
    ax.set_xticks(range(24))
    ax.set_xlim(-0.5, 23.5)
    ax.set_ylim(0, counts.max() * 1.12)
    headline(fig, f"{len(trips):,} trips   ·   "
                  f"{adjusted_trips(trips) / len(pdays):.2f} trips per person per day")
    fig.savefig(out, dpi=160, facecolor="white")
    plt.close(fig)


def chart_start_times_by_weekday(trips, pdays, out):
    """Seven panels on ONE shared y-scale: the point is comparing days, which a
    per-panel scale would quietly defeat."""
    fig, axes = plt.subplots(2, 4, figsize=(15, 7.4), sharey=True, sharex=True)
    fig.subplots_adjust(top=0.86, bottom=0.13, left=0.05, right=0.98,
                        hspace=0.35, wspace=0.12)
    for i, day in enumerate(WEEKDAYS):
        ax = axes[i // 4][i % 4]
        sub = trips[trips["_dayname"] == day]
        counts = hour_bins(sub)
        ax.plot(counts.index, counts.values, marker="o", markersize=3,
                linewidth=1.6, color=SERIES, zorder=3)
        ax.fill_between(counts.index, counts.values, alpha=0.10, color=SERIES, zorder=2)
        frame(ax)
        ax.set_title(f"{day}   ({len(sub):,} trips)", fontsize=11, color=INK,
                     loc="left", pad=6)
        ax.set_xticks(range(0, 24, 6))
        ax.set_xlim(-0.5, 23.5)
    axes[1][3].axis("off")
    # Thursday sits above the hidden 8th cell, so `sharex` leaves its column
    # with no hour labels anywhere -- put them back on the panel itself
    axes[0][3].tick_params(labelbottom=True)
    fig.suptitle("Trip start times by weekday, cleaned Wave 2 dataset",
                 fontsize=15, color=INK, x=0.05, ha="left", y=0.965)
    headline(fig, f"{len(trips):,} trips   ·   shared vertical scale, so panels compare "
                  f"directly", y=0.04)
    fig.savefig(out, dpi=160, facecolor="white")
    plt.close(fig)


def chart_activity_hist(recs, out):
    CAP = 12
    hours = recs["hours"].values
    edges = np.arange(0, CAP + 0.5, 0.5)
    counts, _ = np.histogram(np.clip(hours, 0, CAP - 1e-9), bins=edges)
    over = int((hours >= CAP).sum())
    fig, ax = plt.subplots(figsize=(11, 6.0))
    fig.subplots_adjust(top=0.90, bottom=0.15, left=0.075, right=0.98)
    ax.bar(edges[:-1] + 0.25, counts, width=0.44, color=SERIES, zorder=3)
    if over:
        ax.bar([CAP + 0.25], [over], width=0.44, color=ACCENT, zorder=3)
    frame(ax, "Activity duration between consecutive trips, cleaned Wave 2 dataset",
          "Hours (30-minute bins)", "Activities")
    ax.set_xticks(list(range(0, CAP)) + [CAP + 0.25])
    ax.set_xticklabels([str(h) for h in range(0, CAP)] + [f"{CAP}h+"])
    ax.set_xlim(-0.3, CAP + 0.8)
    headline(fig, f"{len(hours):,} activities   ·   median "
                  f"{np.median(hours):.1f} h   ·   mean {hours.mean():.1f} h")
    fig.savefig(out, dpi=160, facecolor="white")
    plt.close(fig)


def _purpose_bars(series, counts, title, xlabel, out, headline_text):
    order = series.sort_values(ascending=True)
    fig, ax = plt.subplots(figsize=(11, 7.4))
    fig.subplots_adjust(top=0.91, bottom=0.13, left=0.30, right=0.97)
    colors = [MISSING if p == "Not recorded" else SERIES for p in order.index]
    ax.barh(order.index, order.values, height=0.68, color=colors, zorder=3)
    for p, v in order.items():
        ax.text(v + order.max() * 0.012, p, f"{v:.1f} h   (n={counts[p]:,})",
                va="center", fontsize=9, color=MUTED)
    frame(ax, title, xlabel, axis="x")
    ax.set_xlim(0, order.max() * 1.28)
    headline(fig, headline_text)
    fig.savefig(out, dpi=160, facecolor="white")
    plt.close(fig)


# Dropped from the by-purpose charts. "Returning Home" is not an out-of-home
# activity at all: the gap after a homeward trip is time spent AT HOME until the
# next departure, so it measures dwell-at-home and would sit near the top of a
# chart about what people go out to do. "Not recorded" is the absence of an
# answer. Both stay in the overall duration histogram, which is about gaps
# between trips rather than activity types.
EXCLUDED_PURPOSES = {"Returning Home", "Not recorded"}


def chart_activity_by_purpose(recs, out_episode, out_perday):
    recs = recs[~recs["purpose"].isin(EXCLUDED_PURPOSES)]
    episode = recs.groupby("purpose")["hours"].mean()
    n_ep = recs.groupby("purpose")["hours"].size()
    _purpose_bars(episode, n_ep,
                  "Average activity duration per episode, by purpose",
                  "Hours per episode", out_episode,
                  f"{len(recs):,} episodes   ·   overall mean "
                  f"{recs['hours'].mean():.1f} h per episode")

    # total hours of one purpose on one person-day, then averaged across the days
    # that purpose actually occurred -- averaging over ALL days would answer a
    # different question (how much of a typical day goes to it, zeros included)
    per_day = (recs.groupby(["accessCode", "start_date", "purpose"])["hours"].sum()
               .reset_index())
    daily = per_day.groupby("purpose")["hours"].mean()
    n_days = per_day.groupby("purpose")["hours"].size()
    n_person_days = len(recs[["accessCode", "start_date"]].drop_duplicates())
    _purpose_bars(daily, n_days,
                  "Average hours per day spent on each activity, by purpose",
                  "Hours per day on which the activity occurred", out_perday,
                  f"{n_person_days:,} person-days with at least one activity   ·   "
                  f"each purpose averaged only over days it occurred")


def chart_days_per_participant(days, out):
    per_person = days.groupby("accessCode").size()
    counts = per_person.value_counts().sort_index()
    fig, ax = plt.subplots(figsize=(11, 6.0))
    fig.subplots_adjust(top=0.90, bottom=0.15, left=0.075, right=0.98)
    ax.bar(counts.index, counts.values, width=0.72,
           color=[ACCENT if d > 7 else SERIES for d in counts.index], zorder=3)
    for d, v in counts.items():
        ax.text(d, v, str(v), ha="center", va="bottom", fontsize=8, color=MUTED)
    frame(ax, "Days recorded per participant, cleaned Wave 2 dataset",
          "Days recorded", "Participants")
    ax.set_xticks(range(int(counts.index.min()), int(counts.index.max()) + 1))
    ax.set_ylim(0, counts.max() * 1.15)
    over7 = int((per_person > 7).sum())
    headline(fig, f"{per_person.mean():.2f} days per participant   ·   {over7} of "
                  f"{len(per_person)} ({over7 / len(per_person):.0%}) recorded more "
                  f"than 7 days")
    fig.savefig(out, dpi=160, facecolor="white")
    plt.close(fig)


def chart_modes(trips, out):
    modes = trips["_mode"].replace("", "Not recorded")
    counts = modes.value_counts().sort_values(ascending=True)
    total = int(counts.sum())
    fig, ax = plt.subplots(figsize=(11, 7.4))
    fig.subplots_adjust(top=0.91, bottom=0.13, left=0.18, right=0.97)
    ax.barh(counts.index, counts.values, height=0.68,
            color=[MISSING if m in ("Unknown", "Not recorded") else SERIES
                   for m in counts.index], zorder=3)
    for m, v in counts.items():
        ax.text(v + total * 0.006, m, f"{v:,}  ({v / total:.1%})",
                va="center", fontsize=9, color=MUTED)
    frame(ax, "Trips by mode of travel, cleaned Wave 2 dataset", "Trips", axis="x")
    ax.set_xlim(0, counts.max() * 1.16)
    car = int(counts.get("Auto Driver", 0) + counts.get("Auto Passenger", 0))
    headline(fig, f"{total:,} trips   ·   {car / total:.0%} by car   ·   "
                  f"{counts.get('Walk', 0) / total:.0%} on foot")
    fig.savefig(out, dpi=160, facecolor="white")
    plt.close(fig)


def main() -> None:
    df = pd.read_excel(CLEANED)
    tid = df["trip_id"].astype(str)
    df["_mode"] = df["mode_of_travel"].map(norm)
    df["_purpose"] = df["purpose_of_travel"].map(norm)
    dt = pd.to_datetime(df["start_date"], errors="coerce")
    df["_weekday"] = dt.dt.weekday
    df["_dayname"] = dt.dt.day_name()

    trips = df[~tid.str.startswith("Q") & ~tid.str.startswith("Z")].copy()
    trips["_start"] = [stamp(d, t) for d, t in zip(trips["start_date"], trips["start_time"])]
    trips["_end"] = [stamp(d, t) for d, t in zip(trips["end_date"], trips["end_time"])]
    days = df.dropna(subset=["accessCode", "start_date"])[
        ["accessCode", "start_date", "_weekday"]].drop_duplicates()

    chart_start_times(trips, days,
                      "Trip start times, cleaned Wave 2 dataset — all days",
                      f"{OUT_DIR}/start_times_cleaned.png")
    wd = trips[trips["_weekday"] < 5]
    chart_start_times(wd, days[days["_weekday"] < 5],
                      "Trip start times, cleaned Wave 2 dataset — weekdays (Mon-Fri)",
                      f"{OUT_DIR}/start_times_cleaned_weekday.png")
    chart_start_times_by_weekday(trips, days,
                                 f"{OUT_DIR}/start_times_by_weekday.png")

    recs = activity_records(trips)
    chart_activity_hist(recs, f"{OUT_DIR}/activity_duration_cleaned.png")
    chart_activity_by_purpose(recs,
                              f"{OUT_DIR}/activity_by_purpose_episode.png",
                              f"{OUT_DIR}/activity_by_purpose_per_day.png")
    chart_days_per_participant(days[["accessCode", "start_date"]].drop_duplicates(),
                               f"{OUT_DIR}/days_per_participant.png")
    chart_modes(trips, f"{OUT_DIR}/mode_counts.png")

    print(f"trips {len(trips):,} | person-days {len(days):,} | activities {len(recs):,}")
    charted = recs[~recs["purpose"].isin(EXCLUDED_PURPOSES)]   # match the charts
    print(f"by-purpose charts exclude {sorted(EXCLUDED_PURPOSES)}: "
          f"{len(charted):,} episodes charted")
    print("\nmean hours per EPISODE by purpose:")
    ep = charted.groupby("purpose")["hours"].agg(["mean", "size"]).sort_values(
        "mean", ascending=False)
    for p, r in ep.iterrows():
        print(f"  {p:<45} {r['mean']:5.2f} h  (n={int(r['size']):,})")
    print("\nmean hours PER DAY by purpose (days the activity occurred):")
    pd_ = (charted.groupby(["accessCode", "start_date", "purpose"])["hours"].sum()
           .reset_index().groupby("purpose")["hours"].agg(["mean", "size"])
           .sort_values("mean", ascending=False))
    for p, r in pd_.iterrows():
        print(f"  {p:<45} {r['mean']:5.2f} h  (n={int(r['size']):,} days)")
    print("\nwrote 7 PNGs to", OUT_DIR)


if __name__ == "__main__":
    main()
