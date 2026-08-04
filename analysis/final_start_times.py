"""Start-time distribution + trips-per-person-day for the COMBINED cleaned file.

Run against the deliverable itself (not a session), so what it reports is what
anyone opening cleaned.xlsx would count. Writes one PNG next to the input.

Two things about the deliverable shape it has to respect:

  * Not every row is a trip. ``Q`` rows mark a reviewed gap in ACTIVITY between
    two trips and ``Z`` rows stand for a whole day the participant did not
    travel. Both carry an accessCode and a date, and Q rows even carry a start
    time, so counting rows would inflate every figure here. Trips are the
    plain / M / A / S rows only.
  * ``Z`` days still count in the DENOMINATOR: a surveyed day with no travel is
    a real zero, and dropping it would bias trips-per-day upward.

The mode and purpose columns arrive from the survey with zero-width spaces
embedded (``"Walk\\u200b\\u200b"`` is a distinct value from ``"Walk"``), which
silently splits categories -- 34 Walk rows and 403 Auto Driver rows hide behind
invisible characters. Everything is normalised before matching.
"""

from __future__ import annotations

import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

CLEANED = r"C:/Users/matin001/projects/TripReview/final/cleaned.xlsx"
OUT_DIR = r"C:/Users/matin001/projects/TripReview/final"

_INVISIBLE = re.compile(r"[\u200b\u200c\u200d\ufeff\xa0]")

INK, MUTED, GRID = "#1a1a1a", "#5c5c5c", "#d8d8d8"
SERIES = "#2b6cb0"


def norm(s) -> str:
    """Survey text with the invisible characters stripped, for reliable matching."""
    if pd.isna(s):
        return ""
    return _INVISIBLE.sub("", str(s)).strip()


def rounded_hour(hhmm: str):
    """Start hour, minute >= 30 rounding up; 23:30+ wraps to 0 (hour-of-day is
    circular). Mirrors `app.charts.rounded_hour` so this agrees with the in-app
    chart rather than quietly binning differently."""
    m = re.match(r"^(\d{1,2}):(\d{2})", str(hhmm))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return (h + (1 if mi >= 30 else 0)) % 24


# Riding "Transit" is a MODE in this survey, not a purpose; the purpose that
# marks the walk to the stop is "Travel to transit stop". Every mode that is a
# transit ride counts, since the access leg belongs to the journey whether the
# ride is a bus, the SkyTrain or the SeaBus.
TRANSIT_MODES = {"Transit", "Transit Bus", "SkyTrain", "SeaBus"}
STOP_PURPOSE = "Travel to transit stop"


def collapse_count(day: pd.DataFrame) -> tuple:
    """``(trips, {rule: legs folded away})`` for one person-day.

    Three rules, each describing ONE journey the survey happens to log as
    several trips. This only ever affects the trips-per-person-day average --
    the cleaned dataset itself is never rewritten:

      * consecutive Shopping trips -- shop, then shop again, is one shopping trip;
      * a Walk for Recreation followed by a Walk Returning Home -- the walk back
        is the return leg of the same recreational walk, not a second outing;
      * a trip whose purpose is "Travel to transit stop" followed by a transit
        ride -- getting to the stop is the access leg of that transit journey.
    """
    rows = day.to_dict("records")
    folded = {"shopping": 0, "recreational walk": 0, "transit access": 0}
    n = i = 0
    while i < len(rows):
        cur = rows[i]
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        if cur["_purpose"] == "Shopping":
            while i + 1 < len(rows) and rows[i + 1]["_purpose"] == "Shopping":
                i += 1
                folded["shopping"] += 1
        elif (cur["_mode"] == "Walk" and cur["_purpose"] == "Recreation"
              and nxt is not None and nxt["_mode"] == "Walk"
              and nxt["_purpose"] == "Returning Home"):
            i += 1
            folded["recreational walk"] += 1
        elif (cur["_purpose"] == STOP_PURPOSE
              and nxt is not None and nxt["_mode"] in TRANSIT_MODES):
            i += 1
            folded["transit access"] += 1
        n += 1
        i += 1
    return n, folded


def render(trips: pd.DataFrame, person_days: pd.DataFrame, title: str,
           out_png: str) -> dict:
    """One distribution PNG + the figures printed beneath it."""
    n_person_days = len(person_days)
    n_people = person_days["accessCode"].nunique()

    hours = trips["start_time"].map(rounded_hour)
    binned = hours.dropna()
    counts = binned.astype(int).value_counts().reindex(range(24), fill_value=0)

    ordered = trips.sort_values(["accessCode", "start_date", "start_time"])
    adjusted = 0
    folded = {"shopping": 0, "recreational walk": 0, "transit access": 0}
    for _key, day in ordered.groupby(["accessCode", "start_date"], sort=False):
        n, f = collapse_count(day)
        adjusted += n
        for k, v in f.items():
            folded[k] += v
    raw_n = len(trips)
    avg_raw, avg_adj = raw_n / n_person_days, adjusted / n_person_days

    fig, ax = plt.subplots(figsize=(11, 6.6))
    # the caption lives BELOW the axes: floated inside, its box hid hours 15-17,
    # which are the daily peak
    fig.subplots_adjust(top=0.90, bottom=0.34, left=0.075, right=0.98)

    ax.plot(counts.index, counts.values, marker="o", markersize=5, linewidth=2,
            color=SERIES, zorder=3)
    ax.fill_between(counts.index, counts.values, alpha=0.10, color=SERIES, zorder=2)

    peak = int(counts.idxmax())
    ax.annotate(f"peak {counts[peak]:,} trips at {peak}:00",
                xy=(peak, counts[peak]), xytext=(0, 12), textcoords="offset points",
                ha="center", fontsize=9, color=MUTED)

    ax.set_title(title, fontsize=14, color=INK, pad=12, loc="left")
    ax.set_xlabel("Hour of day (start time, rounded to nearest hour)",
                  fontsize=10, color=MUTED)
    ax.set_ylabel("Trips", fontsize=10, color=MUTED)
    ax.set_xticks(range(0, 24))
    ax.set_xlim(-0.5, 23.5)
    ax.set_ylim(0, counts.max() * 1.14)
    ax.grid(axis="y", color=GRID, linewidth=0.8, zorder=1)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)

    headline = (f"{avg_adj:.2f} trips per person per day"
                f"      ({raw_n:,} trips  ·  {n_people} participants"
                f"  ·  {n_person_days:,} person-days)")
    fig.text(0.075, 0.185, headline, fontsize=11.5, color=INK, va="top")
    fig.text(0.075, 0.135, (
        f"Unadjusted it is {avg_raw:.2f}. The adjusted figure counts one JOURNEY as one trip, "
        "folding together: consecutive shopping\n"
        "trips; a Walk for Recreation followed by a Walk Returning Home (the walk back "
        "is that outing's return leg); and a trip to a\n"
        "transit stop followed by the transit ride (the access leg of that journey). "
        "The cleaned dataset itself is unchanged.\n"
        "Trips exclude Q rows (reviewed activity gaps) and Z rows (no-travel days), neither "
        "being a journey; no-travel days still\n"
        "count as person-days, so the average is not inflated by dropping them."),
        fontsize=8.8, color=MUTED, va="top", linespacing=1.6)

    fig.savefig(out_png, dpi=160, facecolor="white")
    return {"png": out_png, "trips": raw_n, "adjusted": adjusted,
            "person_days": n_person_days, "people": n_people,
            "avg_raw": avg_raw, "avg_adj": avg_adj, "folded": folded,
            "unparsed": int(len(trips) - len(binned))}


def main() -> None:
    df = pd.read_excel(CLEANED)
    tid = df["trip_id"].astype(str)
    df["_mode"] = df["mode_of_travel"].map(norm)
    df["_purpose"] = df["purpose_of_travel"].map(norm)
    df["_weekday"] = pd.to_datetime(df["start_date"], errors="coerce").dt.weekday

    trips = df[~tid.str.startswith("Q") & ~tid.str.startswith("Z")].copy()
    # every surveyed person-day, INCLUDING the no-travel ones (a real zero)
    pdays = df.dropna(subset=["accessCode", "start_date"])[
        ["accessCode", "start_date", "_weekday"]].drop_duplicates()

    for label, suffix, keep in (
            ("all days", "", None),
            ("weekdays only (Mon-Fri)", "_weekday", True)):
        t = trips if keep is None else trips[trips["_weekday"] < 5]
        p = pdays if keep is None else pdays[pdays["_weekday"] < 5]
        r = render(t, p, f"Trip start times, cleaned Wave 2 dataset — {label}",
                   f"{OUT_DIR}/start_times_cleaned{suffix}.png")
        print(f"[{label}]")
        print(f"  wrote {r['png']}")
        print(f"  trips {r['trips']} | adjusted {r['adjusted']} "
              f"| person-days {r['person_days']} | people {r['people']}")
        print(f"  avg/person-day  raw {r['avg_raw']:.3f} | adjusted {r['avg_adj']:.3f}")
        print(f"  legs folded away: {sum(r['folded'].values())}  {r['folded']}")
        print(f"  unparseable start times excluded: {r['unparsed']}")


if __name__ == "__main__":
    main()
