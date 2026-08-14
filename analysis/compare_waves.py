"""Wave 2 vs Wave 3, side by side.

    .venv\\Scripts\\python.exe analysis\\compare_waves.py

Four charts: start times (all days), start times (weekdays only), hours per day
by activity purpose, and mode share.

Everything is plotted as a SHARE or a per-day average, never a count. Wave 3 has
~35% more trips than wave 2, so counts would show that size difference in every
chart and bury the thing being compared, which is the shape of each distribution.

Written to final/distributions/comparison/.
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
from chartkit import (FAINT, GRID, INK, MUTED, SURFACE, W2, W3, circular_kde,
                      figure, headline, hour_axis, hour_of_day, save, style)

FINAL = r"C:/Users/matin001/projects/TripReview/final"
OUT_DIR = f"{FINAL}/distributions/comparison"
WAVES = {"Wave 2": f"{FINAL}/exports/Wave2/cleaned.xlsx",
         "Wave 3": f"{FINAL}/exports/Wave3/cleaned.xlsx"}
COLOR = {"Wave 2": W2, "Wave 3": W3}

_INVISIBLE = re.compile(r"[\u200b\u200c\u200d\ufeff\xa0]")
_TZ = re.compile(r"([+-]\d{2}:?\d{2})$")
_PAREN = re.compile(r"\s*\([^)]*\)\s*$")

# Dropped from the by-purpose chart, matching `final_charts.EXCLUDED_PURPOSES`.
# "Returning Home" is not an out-of-home activity: the gap after a homeward trip
# is time spent AT HOME until the next departure. "Not recorded" is the absence
# of an answer.
EXCLUDED_PURPOSES = {"Returning Home", "Not recorded"}


def norm(s) -> str:
    """Survey text with the invisible characters stripped and the survey's own
    prompt text dropped, so the two waves' labels line up."""
    if pd.isna(s):
        return ""
    return _PAREN.sub("", _INVISIBLE.sub("", str(s)).strip()).strip()


def stamp(date, tm):
    if pd.isna(date) or pd.isna(tm):
        return pd.NaT
    return pd.to_datetime(f"{str(date)[:10]} {_TZ.sub('', str(tm)).strip()}",
                          errors="coerce")


def load(path: str) -> pd.DataFrame:
    """Trips only: Q rows mark a reviewed activity gap and Z rows a day with no
    travel, and neither is a journey."""
    df = pd.read_excel(path)
    tid = df["trip_id"].astype(str)
    t = df[~tid.str.startswith("Q") & ~tid.str.startswith("Z")].copy()
    t["_mode"] = t["mode_of_travel"].map(norm)
    t["_purpose"] = t["purpose_of_travel"].map(norm)
    t["_start"] = [stamp(d, x) for d, x in zip(t["start_date"], t["start_time"])]
    t["_end"] = [stamp(d, x) for d, x in zip(t["end_date"], t["end_time"])]
    t["_weekday"] = pd.to_datetime(t["start_date"], errors="coerce").dt.weekday
    return t


def activity_records(t: pd.DataFrame) -> pd.DataFrame:
    """One row per within-day consecutive trip pair: the gap, and the purpose of
    the earlier trip (what the person travelled in order to do)."""
    t = t.dropna(subset=["_start", "_end"]).sort_values(
        ["accessCode", "start_date", "_start"])
    recs = []
    for (person, date), day in t.groupby(["accessCode", "start_date"], sort=False):
        rows = day.to_dict("records")
        for a, b in zip(rows, rows[1:]):
            mins = (b["_start"] - a["_end"]).total_seconds() / 60.0
            if mins < 0:
                continue          # next trip starts before this one ends: a logging error
            # "empty" is the survey's own placeholder for an unanswered purpose,
            # not a purpose, so it joins the blanks
            purpose = a["_purpose"]
            if not purpose or purpose.lower() == "empty":
                purpose = "Not recorded"
            recs.append({"accessCode": person, "start_date": date,
                         "purpose": purpose, "hours": mins / 60.0})
    return pd.DataFrame(recs)


# --- charts ----------------------------------------------------------------

def chart_start_times(data: dict, out: str, title: str, note: str) -> None:
    fig, ax = figure(figsize=(12, 6.4))
    fig.subplots_adjust(top=0.86, bottom=0.15, left=0.08, right=0.97)
    curves = {}
    for wave, t in data.items():
        grid, dens = circular_kde(hour_of_day(t["start_time"]))
        curves[wave] = (grid, dens)

    # No overlapping translucent fills: two of them blend into a third colour
    # that reads as a category of its own, and the overlap is most of the day
    # here. Shade only the gap, in whichever wave is ahead.
    (g, d2), (_g, d3) = curves["Wave 2"], curves["Wave 3"]
    ax.fill_between(g, d2, d3, where=d2 >= d3, color=W2, alpha=0.18, zorder=1,
                    interpolate=True)
    ax.fill_between(g, d2, d3, where=d3 > d2, color=W3, alpha=0.18, zorder=1,
                    interpolate=True)
    for wave, (grid, dens) in curves.items():
        ax.plot(grid, dens, color=COLOR[wave], linewidth=3, zorder=3, label=wave,
                solid_capstyle="round")

    # Label each curve where they are FURTHEST apart, not at their peaks -- the
    # peaks are minutes apart, so labels there would sit on top of each other
    sep = int(np.argmax(np.abs(d2 - d3)))
    for wave, (grid, dens) in curves.items():
        ax.annotate(wave, xy=(grid[sep], dens[sep]),
                    xytext=(0, 11 if wave == "Wave 2" else -20),
                    textcoords="offset points", ha="center", fontsize=11.5,
                    color=COLOR[wave], weight="700")

    style(ax, title, "Hour of day", "Share of trips (density per hour)")
    hour_axis(ax)
    ax.set_ylim(bottom=0)
    ax.legend(frameon=False, loc="upper left", fontsize=10.5, labelcolor=MUTED)
    headline(fig, note)
    save(fig, out)
    plt.close(fig)


def chart_purpose_per_day(data: dict, out: str) -> None:
    """Hours spent on each purpose on a day it happened at all.

    Averaging over ALL days would answer a different question (how much of a
    typical day goes to it, zeros included) and would then be driven by how often
    the purpose comes up rather than how long it lasts.
    """
    per_wave, n_days = {}, {}
    for wave, t in data.items():
        recs = activity_records(t)
        recs = recs[~recs["purpose"].isin(EXCLUDED_PURPOSES)]
        daily = (recs.groupby(["accessCode", "start_date", "purpose"])["hours"]
                 .sum().reset_index())
        per_wave[wave] = daily.groupby("purpose")["hours"].mean()
        n_days[wave] = daily.groupby("purpose")["hours"].size()

    combined = pd.concat(per_wave, axis=1)
    order = combined.mean(axis=1).sort_values(ascending=True).index.tolist()

    fig, ax = figure(figsize=(12.5, 8.0))
    fig.subplots_adjust(top=0.90, bottom=0.13, left=0.30, right=0.97)
    y = np.arange(len(order))
    h = 0.38
    for k, wave in enumerate(per_wave):
        vals = [per_wave[wave].get(p, np.nan) for p in order]
        offs = y + (k - 0.5) * (h + 0.03)
        ax.barh(offs, [0 if pd.isna(v) else v for v in vals], height=h,
                color=COLOR[wave], label=wave, zorder=3)
        for yi, v, p in zip(offs, vals, order):
            # a purpose absent from one wave gets "n/a", not a silent zero bar
            txt = "n/a" if pd.isna(v) else f"{v:.1f} h  (n={n_days[wave][p]:,})"
            ax.text((0 if pd.isna(v) else v) + 0.08, yi, txt, va="center",
                    fontsize=9, color=COLOR[wave], weight="600")

    style(ax, "How long each activity lasts, on a day it happens",
          "Hours per day on which the activity occurred", axis="x")
    ax.set_yticks(y)
    ax.set_yticklabels(order)
    ax.set_ylim(-0.7, len(order) - 0.3)
    ax.set_xlim(0, float(np.nanmax(combined.values)) * 1.30)
    ax.legend(frameon=False, loc="lower right", fontsize=10.5, labelcolor=MUTED)

    work = {w: per_wave[w].get("Work", np.nan) for w in per_wave}
    # the claim is measured, not assumed: only the three work/school purposes
    # clear three hours, and Social is the tallest of the rest
    long_ones = {p for p in order if combined.loc[p].max() >= 3.0}
    rest = combined.drop(index=list(long_ones)).max(axis=1).max()
    headline(fig, "Work anchors both waves at "
                  + " and ".join(f"{v:.1f} h" for v in work.values())
                  + f" a day; outside work and school nothing clears {rest:.1f} h")
    save(fig, out)
    plt.close(fig)


def chart_mode_share(data: dict, out: str, floor: float = 0.01) -> None:
    shares = {}
    for wave, t in data.items():
        shares[wave] = t["_mode"].replace("", "Not recorded").value_counts(normalize=True)

    # Modes under `floor` in BOTH waves are pooled. Eight of them draw as bars a
    # pixel wide, so they cost half the chart's height and say nothing; the pooled
    # bar keeps them honestly in the total instead of dropping them.
    combined = pd.concat(shares, axis=1).fillna(0)
    small = combined[(combined < floor).all(axis=1)].index.tolist()
    if small:
        for wave in shares:
            kept = shares[wave].drop(labels=[m for m in small if m in shares[wave].index])
            kept[f"Other ({len(small)} modes)"] = shares[wave].reindex(small).fillna(0).sum()
            shares[wave] = kept
    order = (pd.concat(shares, axis=1).fillna(0).mean(axis=1)
             .sort_values(ascending=True).index.tolist())
    # keep the pooled bar at the bottom of the ranking, not mixed into it
    pooled = [m for m in order if m.startswith("Other (")]
    order = pooled + [m for m in order if m not in pooled]

    fig, ax = figure(figsize=(12, 6.6))
    fig.subplots_adjust(top=0.88, bottom=0.15, left=0.22, right=0.97)
    y = np.arange(len(order))
    h = 0.38
    for k, (wave, s) in enumerate(shares.items()):
        vals = [s.get(m, 0.0) for m in order]
        # a small gap between the paired bars stops them reading as one block
        ax.barh(y + (k - 0.5) * (h + 0.03), vals, height=h, color=COLOR[wave],
                label=wave, zorder=3)
        for yi, v in zip(y + (k - 0.5) * (h + 0.03), vals):
            # ONE DECIMAL, not a whole percent: the bars partition the trips
            # exactly, but rounding each to a whole number made the printed
            # labels sum to 97% in one wave and 101% in the other, which reads
            # as an error in the data rather than in the rounding.
            ax.text(v + 0.005, yi, f"{v:.1%}", va="center", fontsize=9.5,
                    color=COLOR[wave], weight="600")
    style(ax, "How people travel", "Share of trips", axis="x")
    ax.set_yticks(y)
    ax.set_yticklabels(order)
    ax.set_ylim(-0.7, len(order) - 0.3)
    ax.set_xlim(0, max(max(s) for s in shares.values()) * 1.18)
    ax.xaxis.set_major_formatter(lambda v, _p: f"{v:.0%}")
    ax.legend(frameon=False, loc="lower right", fontsize=10.5, labelcolor=MUTED)

    car = {w: s.get("Auto Driver", 0) + s.get("Auto Passenger", 0)
           for w, s in shares.items()}
    pas = {w: s.get("Auto Passenger", 0) for w, s in shares.items()}
    for wave, s in shares.items():                      # the labels must partition
        assert abs(s.sum() - 1.0) < 1e-9, (wave, s.sum())
    headline(fig, "Car share slips "
                  + " to ".join(f"{v:.0%}" for v in car.values())
                  + ", almost entirely because passenger trips halve ("
                  + " to ".join(f"{v:.0%}" for v in pas.values()) + ")")
    save(fig, out)
    plt.close(fig)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    data = {w: load(p) for w, p in WAVES.items()}
    for w, t in data.items():
        print(f"{w}: {len(t):,} trips | {t['accessCode'].nunique()} participants")

    chart_start_times(
        data, f"{OUT_DIR}/compare_start_times.png", "When people travel",
        "Wave 2 concentrates in the late afternoon; wave 3 spreads its travel "
        "more evenly across the day")
    weekdays = {w: t[t["_weekday"] < 5] for w, t in data.items()}
    print("weekday-only trips: "
          + "  ".join(f"{w} {len(t):,}" for w, t in weekdays.items()))
    chart_start_times(
        weekdays, f"{OUT_DIR}/compare_start_times_weekday.png",
        "When people travel on a weekday (Mon-Fri)",
        "Stripped of weekends, both waves show the morning commute peak the "
        "all-days view flattens")
    chart_purpose_per_day(data, f"{OUT_DIR}/compare_activity_by_purpose_per_day.png")
    chart_mode_share(data, f"{OUT_DIR}/compare_mode_share.png")
    print(f"\nwrote 4 comparison PNGs to {OUT_DIR}")


if __name__ == "__main__":
    main()
