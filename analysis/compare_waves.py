"""Wave 2 and wave 3, side by side.

    .venv\\Scripts\\python.exe analysis\\compare_waves.py

Four charts: start times (all days), start times (weekdays only), hours per day
by activity purpose, and mode share.

Everything is plotted as a SHARE or a per-day average, never a count. The two
waves differ substantially in trip volume (wave 3 has roughly 35% more trips
than wave 2), so counts would show that size difference in every chart and bury
the thing being compared, which is the shape of each distribution.

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
                      figure, hour_axis, hour_of_day, save, style)

FINAL = r"C:/Users/matin001/projects/TripReview/final"
OUT_DIR = f"{FINAL}/distributions/comparison"
WAVES = {"Wave 2": f"{FINAL}/exports/Wave2/cleaned.xlsx",
         "Wave 3": f"{FINAL}/exports/Wave3/cleaned.xlsx"}
COLOR = {"Wave 2": W2, "Wave 3": W3}

_INVISIBLE = re.compile(r"[\u200b\u200c\u200d\ufeff\xa0]")
_TZ = re.compile(r"([+-]\d{2}:?\d{2})$")
_PAREN = re.compile(r"\s*\([^)]*\)\s*$")

# Dropped from the by-purpose chart. "Returning Home" is not an out-of-home
# activity: the gap after a homeward trip is time spent AT HOME until the next
# departure. "Not recorded" is the absence of an answer. "Working at
# local/neighborhood workspaces", "Not Selected", "Other" and "Travel to
# transit stop" are dropped at the user's request (low-signal / not a
# destination purpose in its own right).
# Short titles only. Each chart names what it shows and nothing else: the
# interpretation belongs in what the presenter says over the slide, and a bold
# claim burnt into the PNG goes stale the moment the underlying numbers move.
ALL_TITLE = "Start times"
WEEKDAY_TITLE = "Start times - Weekday"
PURPOSE_TITLE = "Activity duration"
MODE_TITLE = "Mode share"

EXCLUDED_PURPOSES = {"Returning Home", "Not recorded",
                     "Working at local/neighborhood workspaces",
                     "Not Selected", "Other", "Travel to transit stop"}


def norm(s) -> str:
    """Survey text with the invisible characters stripped and the survey's own
    prompt text dropped, so every wave's labels line up."""
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


def _bar_offsets(n: int, h: float = None, gap: float = 0.03):
    """Per-series (height, y-offset-per-series) for `n` bars sharing one category
    row, centred on the row. `h` scales down as `n` grows so the group's total
    ink stays comparable regardless of how many waves are being compared (tuned
    at n=2 -> h=0.38, matching the original 2-wave chart exactly)."""
    if h is None:
        h = 0.76 / n
    offsets = [(k - (n - 1) / 2) * (h + gap) for k in range(n)]
    return h, offsets


# --- charts ----------------------------------------------------------------

def chart_start_times(data: dict, out: str, title: str) -> None:
    """Density, always -- never a raw count. The waves differ substantially in
    trip volume, so a raw count would show that size difference on every panel
    and bury the thing being compared here, which is the shape of the curve.

    Each curve gets its own light fill under its own line (no cross-curve
    difference shading -- that reads clearly for exactly two curves but turns to
    clutter once a third overlaps both), and identity is carried by the legend
    plus each curve's own colour, matching chartkit's colour-blind-safe rule
    that identity is never colour alone."""
    fig, ax = figure(figsize=(12, 6.4))
    fig.subplots_adjust(top=0.86, bottom=0.15, left=0.08, right=0.97)
    for wave, t in data.items():
        hours = hour_of_day(t["start_time"])
        grid, dens = circular_kde(hours)
        ax.fill_between(grid, dens, color=COLOR[wave], alpha=0.10, zorder=2)
        ax.plot(grid, dens, color=COLOR[wave], linewidth=3, zorder=3, label=wave,
                solid_capstyle="round")

    style(ax, title, "Hour of day", "Share of trips (density per hour)")
    hour_axis(ax)
    ax.set_ylim(bottom=0)
    ax.legend(frameon=False, loc="upper left", fontsize=10.5, labelcolor=MUTED)
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

    fig, ax = figure(figsize=(13, 8.4))
    # bottom margin no longer reserves a band for a headline under the axes
    fig.subplots_adjust(top=0.92, bottom=0.08, left=0.30, right=0.97)
    y = np.arange(len(order))
    h, offsets = _bar_offsets(len(per_wave))
    for off, (wave, vals_by_purpose) in zip(offsets, per_wave.items()):
        vals = [vals_by_purpose.get(p, np.nan) for p in order]
        yy = y + off
        ax.barh(yy, [0 if pd.isna(v) else v for v in vals], height=h,
                color=COLOR[wave], label=wave, zorder=3)
        for yi, v, p in zip(yy, vals, order):
            # a purpose absent from one wave gets "n/a", not a silent zero bar
            txt = "n/a" if pd.isna(v) else f"{v:.1f} h  (n={n_days[wave][p]:,})"
            ax.text((0 if pd.isna(v) else v) + 0.08, yi, txt, va="center",
                    fontsize=8.5, color=COLOR[wave], weight="600")

    style(ax, PURPOSE_TITLE,
          "Hours per day on which the activity occurred", axis="x")
    ax.set_yticks(y)
    ax.set_yticklabels(order)
    ax.set_ylim(-0.7, len(order) - 0.3)
    ax.set_xlim(0, float(np.nanmax(combined.values)) * 1.35)
    ax.legend(frameon=False, loc="lower right", fontsize=10.5, labelcolor=MUTED)

    save(fig, out)
    plt.close(fig)


def chart_mode_share(data: dict, out: str, floor: float = 0.01) -> None:
    shares = {}
    for wave, t in data.items():
        shares[wave] = t["_mode"].replace("", "Not recorded").value_counts(normalize=True)

    # Modes under `floor` in EVERY wave are pooled. A dozen of them draw as bars a
    # pixel wide, so they cost a lot of the chart's height and say nothing; the
    # pooled bar keeps them honestly in the total instead of dropping them.
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

    fig, ax = figure(figsize=(12, 7.2))
    fig.subplots_adjust(top=0.91, bottom=0.09, left=0.22, right=0.97)
    y = np.arange(len(order))
    h, offsets = _bar_offsets(len(shares))
    for off, (wave, s) in zip(offsets, shares.items()):
        vals = [s.get(m, 0.0) for m in order]
        yy = y + off
        ax.barh(yy, vals, height=h, color=COLOR[wave], label=wave, zorder=3)
        for yi, v in zip(yy, vals):
            # ONE DECIMAL, not a whole percent: the bars partition the trips
            # exactly, but rounding each to a whole number made the printed
            # labels sum to 97% in one wave and 101% in another, which reads
            # as an error in the data rather than in the rounding.
            ax.text(v + 0.005, yi, f"{v:.1%}", va="center", fontsize=8.5,
                    color=COLOR[wave], weight="600")
    style(ax, MODE_TITLE, "Share of trips", axis="x")
    ax.set_yticks(y)
    ax.set_yticklabels(order)
    ax.set_ylim(-0.7, len(order) - 0.3)
    ax.set_xlim(0, max(max(s) for s in shares.values()) * 1.18)
    ax.xaxis.set_major_formatter(lambda v, _p: f"{v:.0%}")
    ax.legend(frameon=False, loc="lower right", fontsize=10.5, labelcolor=MUTED)

    for wave, s in shares.items():                      # the labels must partition
        assert abs(s.sum() - 1.0) < 1e-9, (wave, s.sum())
    save(fig, out)
    plt.close(fig)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    data = {w: load(p) for w, p in WAVES.items()}
    for w, t in data.items():
        print(f"{w}: {len(t):,} trips | {t['accessCode'].nunique()} participants")

    chart_start_times(data, f"{OUT_DIR}/compare_start_times.png", ALL_TITLE)

    weekdays = {w: t[t["_weekday"] < 5] for w, t in data.items()}
    print("weekday-only trips: "
          + "  ".join(f"{w} {len(t):,}" for w, t in weekdays.items()))
    chart_start_times(
        weekdays, f"{OUT_DIR}/compare_start_times_weekday.png", WEEKDAY_TITLE)
    chart_purpose_per_day(data, f"{OUT_DIR}/compare_activity_by_purpose_per_day.png")
    chart_mode_share(data, f"{OUT_DIR}/compare_mode_share.png")

    print(f"\nwrote 4 comparison PNGs to {OUT_DIR}")


if __name__ == "__main__":
    main()
