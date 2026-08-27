"""The deck's weekday/weekend start-time charts: raw, and the cleaned twin.

    .venv\\Scripts\\python.exe analysis\\problem_charts.py

Writes two sets, drawn by the same code so a before/after pair put side by side
differs only in the data behind it:

    distributions/problem/<Wave>_wkday_wkend.png     raw
    distributions/after/<Wave>_wkday_wkend.png       cleaned, four curves

The CHARTS are unchanged from the versions already on the slide: same size,
same blue/orange weekday/weekend pair, same axes and legend placement. Only the
trips-per-person-per-day figure in each legend entry is recomputed, so that the
number quoted against the raw data means the same thing as the number quoted
against the cleaned data elsewhere in the deck.

Conditions, matching `final_charts.collapse_count` exactly:

  * consecutive shopping legs count as one journey
  * a Walk for Recreation followed by a Walk Returning Home counts as one
  * a trip whose purpose is travel to a transit stop is not counted at all
  * wave 2 drops weekend days carrying 10 or more trips
  * wave 3 counts labelled rows only, since 61.9% of its raw rows are unlabelled
    GPS fragments rather than reported trips

Purpose strings are normalised with the survey's own prompt text stripped
("Recreation (e.g., Active sports...)" -> "Recreation"). Waves 2 and 3 carry no
such suffixes, so the stripping is a no-op for them; it stays because it costs
nothing and makes the collapse rules robust to a wave that does carry them.

This writes PNGs only. Swapping them into the deck is left to the user, so a
locked or concurrently-edited pptx can never cost a run.
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
from matplotlib.lines import Line2D

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from final_charts import collapse_count

BASE = (r"P:/UiTR/Shared Folder/1. Current Student/Matin/"
        r"4. Smartphone App Data Cleaning/Raw")
DIST = r"C:/Users/matin001/projects/TripReview/final/distributions"
OUT_DIR = f"{DIST}/problem"
AFTER_DIR = f"{DIST}/after"
EXPORTS = r"C:/Users/matin001/projects/TripReview/final/exports"

# the deck's own palette, kept verbatim so a regenerated chart drops into the
# slide beside the untouched ones without reading as a different chart
WHITE, INK, MUTED, GRID = "#FFFFFF", "#0B2244", "#5B6B82", "#E7E6E6"
WEEKDAY_C, WEEKEND_C = "#4472C4", "#ED7D31"

# The before/after chart carries four curves. Hue keeps weekday apart from
# weekend and lightness keeps before apart from after, so the pairing is
# readable as two pairs rather than four unrelated series. Before is also
# DASHED: hue-plus-lightness alone would collapse for a colour-blind reader,
# and the dash carries the before/after split without relying on colour.
WEEKDAY_RAW_C, WEEKEND_RAW_C = "#9DC3E6", "#F8C79B"

_TZ = re.compile(r"([+-]\d{2}:?\d{2})$")
_INVISIBLE = re.compile(r"[\u200b\u200c\u200d\ufeff\xa0]")
_PAREN = re.compile(r"\s*\([^)]*\)\s*$")
BLANK = {"", "empty", "unknown", "not selected", "none"}


def norm(s) -> str:
    if pd.isna(s):
        return ""
    return _PAREN.sub("", _INVISIBLE.sub("", str(s)).strip()).strip()


def circular_kde(hours: np.ndarray, bw: float = 0.7, n: int = 721):
    grid = np.linspace(0, 24, n)
    if hours.size == 0:
        return grid, np.zeros_like(grid)
    d = np.abs(grid[:, None] - hours[None, :])
    d = np.minimum(d, 24 - d)
    dens = np.exp(-0.5 * (d / bw) ** 2).sum(axis=1)
    dens /= bw * np.sqrt(2 * np.pi) * hours.size
    return grid, dens


def trips_per_hour(hours: np.ndarray, bw: float = 0.7):
    grid, dens = circular_kde(hours, bw=bw)
    return grid, dens * hours.size


def _stamp(date, tm):
    if pd.isna(date) or pd.isna(tm):
        return pd.NaT
    return pd.to_datetime(f"{str(date)[:10]} {_TZ.sub('', str(tm)).strip()}",
                          errors="coerce")


def load_app_schema(path: str, is_csv: bool) -> pd.DataFrame:
    df = pd.read_csv(path, low_memory=False) if is_csv else pd.read_excel(path)
    t = df.dropna(subset=["start_date", "start_time"]).copy()
    t["start_dt"] = [_stamp(d, s) for d, s in zip(t["start_date"], t["start_time"])]
    t = t.dropna(subset=["start_dt"])
    t["_mode"] = t["mode_of_travel"].map(norm)
    t["_purpose"] = t["purpose_of_travel"].map(norm)
    return t[["accessCode", "start_dt", "_mode", "_purpose"]]


def load_cleaned(wave: str) -> tuple:
    """``(trips, days)`` for a cleaned export.

    Q rows mark a reviewed activity gap and Z rows a day with no travel, so
    neither is a trip. Z days ARE person-days though, so the denominator comes
    from the FULL frame before those rows are dropped, matching
    `final_charts.main`. Returning the two separately is what makes that
    possible: unlike a raw file, a cleaned one has days carrying no trip at all,
    so person-days cannot be recovered from the trip frame.
    """
    df = pd.read_excel(f"{EXPORTS}/{wave}/cleaned.xlsx")
    tid = df["trip_id"].astype(str)
    df["start_dt"] = [_stamp(d, s) for d, s in
                      zip(df["start_date"], df["start_time"])]
    df["_mode"] = df["mode_of_travel"].map(norm)
    df["_purpose"] = df["purpose_of_travel"].map(norm)

    days = df.dropna(subset=["accessCode", "start_date"])[
        ["accessCode", "start_date"]].drop_duplicates().copy()
    days["date"] = pd.to_datetime(days["start_date"], errors="coerce").dt.date
    days["weekday"] = pd.to_datetime(days["start_date"],
                                     errors="coerce").dt.weekday

    trips = df[~tid.str.startswith("Q") & ~tid.str.startswith("Z")]
    trips = trips.dropna(subset=["start_dt"])
    return trips[["accessCode", "start_dt", "_mode", "_purpose"]], days


def cleaned_rate(trips: pd.DataFrame, days: pd.DataFrame,
                 drop_heavy: bool = False) -> float:
    """Journey-collapsed trips over person-days, with the denominator supplied
    rather than inferred, so no-travel days still count as days."""
    if trips.empty or days.empty:
        return 0.0
    t = trips.assign(date=trips["start_dt"].dt.date, start_time=trips["start_dt"])
    if drop_heavy:
        weekend = t[t["start_dt"].dt.weekday >= 5]
        counts = weekend.groupby(["accessCode", "date"]).size()
        heavy = set(counts[counts >= 10].index)
        t = t[[k not in heavy for k in zip(t["accessCode"], t["date"])]]
        days = days[[k not in heavy for k in zip(days["accessCode"], days["date"])]]
    ordered = t.sort_values(["accessCode", "date", "start_time"])
    n = sum(collapse_count(day) for _k, day in
            ordered.groupby(["accessCode", "date"], sort=False))
    return n / len(days)


def summarize_cleaned(trips: pd.DataFrame, days: pd.DataFrame,
                      wave: str) -> dict:
    t = trips.copy()
    t["weekday"] = t["start_dt"].dt.weekday
    t["hour"] = t["start_dt"].dt.hour + t["start_dt"].dt.minute / 60.0

    out = {}
    for label, keep in (("weekday", lambda w: w < 5), ("weekend", lambda w: w >= 5)):
        sub = t[keep(t["weekday"])]
        grid, curve = trips_per_hour(sub["hour"].to_numpy())
        heavy = wave == "Wave 2" and label == "weekend"
        out[label] = {"grid": grid, "curve": curve,
                      "avg_per_person_day": cleaned_rate(
                          sub, days[keep(days["weekday"])], heavy)}
    out["all"] = {"avg_per_person_day": cleaned_rate(t, days,
                                                     wave == "Wave 2")}
    return out


def per_person_day(sub: pd.DataFrame, drop_heavy: bool = False) -> float:
    """Journey-collapsed trips per person-day, the cleaned charts' definition.

    `collapse_count` reads `start_time`, so the frame is handed the column it
    expects rather than being sorted on a differently-named one.

    `drop_heavy` drops WEEKEND days carrying 10 or more trips. The weekend
    restriction matters on the all-days frame: without it the rule also strikes
    heavy weekdays, which pulled wave 2's all-days rate below both its own
    weekday and weekend rates, an impossibility for a weighted average.
    """
    if sub.empty:
        return 0.0
    sub = sub.assign(date=sub["start_dt"].dt.date,
                     start_time=sub["start_dt"])
    if drop_heavy:
        weekend = sub[sub["start_dt"].dt.weekday >= 5]
        counts = weekend.groupby(["accessCode", "date"]).size()
        heavy = set(counts[counts >= 10].index)
        keys = list(zip(sub["accessCode"], sub["date"]))
        sub = sub[[k not in heavy for k in keys]]
        if sub.empty:
            return 0.0
    ordered = sub.sort_values(["accessCode", "date", "start_time"])
    groups = list(ordered.groupby(["accessCode", "date"], sort=False))
    return sum(collapse_count(day) for _k, day in groups) / len(groups)


def summarize(t: pd.DataFrame, wave: str) -> dict:
    t = t.copy()
    t["weekday"] = t["start_dt"].dt.weekday
    t["hour"] = t["start_dt"].dt.hour + t["start_dt"].dt.minute / 60.0

    out = {}
    for label, mask in (("weekday", t["weekday"] < 5),
                        ("weekend", t["weekday"] >= 5)):
        sub = t[mask]
        grid, curve = trips_per_hour(sub["hour"].to_numpy())
        # the heavy-weekend rule only ever applied to wave 2's weekend days
        heavy = wave == "Wave 2" and label == "weekend"
        out[label] = {"grid": grid, "curve": curve,
                      "avg_per_person_day": per_person_day(sub, heavy)}

    # All days is NOT the two figures averaged: each is a ratio over its own
    # person-days, and there are far more weekdays than weekend days, so the
    # combined rate has to be recomputed over the whole frame.
    out["all"] = {"avg_per_person_day": per_person_day(t, wave == "Wave 2")}
    return out


def chart_wave(title: str, data: dict, out_path: str) -> None:
    fig, ax = plt.subplots(figsize=(9.5, 4.6), dpi=200)
    fig.patch.set_facecolor(WHITE)
    ax.set_facecolor(WHITE)

    for label, color in (("weekday", WEEKDAY_C), ("weekend", WEEKEND_C)):
        d = data[label]
        ax.plot(d["grid"], d["curve"], color=color, linewidth=2.8,
                solid_capstyle="round", zorder=3,
                label=f"{label.capitalize()}  "
                      f"({d['avg_per_person_day']:.2f} trips/person/day)")
        ax.fill_between(d["grid"], d["curve"], color=color, alpha=0.10, zorder=2)

    ax.set_title(title, fontsize=15, color=INK, loc="left", weight="600", pad=10)
    ax.set_xlabel("Hour of day", fontsize=10.5, color=MUTED)
    ax.set_ylabel("Trips starting per hour", fontsize=10.5, color=MUTED)
    ax.set_xticks(range(0, 25, 3))
    ax.set_xticklabels([f"{h:02d}" for h in range(0, 25, 3)])
    ax.set_xlim(0, 24)
    ax.set_ylim(bottom=0)
    ax.grid(axis="y", color=GRID, linewidth=1, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9.5, length=0)

    # The all-days rate joins the legend as a third row with an INVISIBLE
    # handle: it is a summary of both curves rather than a curve of its own, and
    # drawing it would add a third line (the sum of the other two) that changes
    # the y-scale and the shape of a chart meant to stay as it was.
    handles, labels = ax.get_legend_handles_labels()
    handles.append(Line2D([], [], linestyle="none"))
    labels.append(f"All days  ({data['all']['avg_per_person_day']:.2f} "
                  f"trips/person/day)")
    ax.legend(handles, labels, frameon=False, loc="upper left", fontsize=9.5,
              labelcolor=INK)

    fig.tight_layout()
    fig.savefig(out_path, facecolor=WHITE)
    plt.close(fig)


def chart_before_after(title: str, raw: dict, clean: dict, out_path: str) -> None:
    """One wave, four curves: weekday and weekend, before and after cleaning.

    No fill under any curve. With four overlapping series the shaded areas stack
    into a single muddy block and stop conveying anything, which is exactly what
    the fill is for on the two-curve version.
    """
    fig, ax = plt.subplots(figsize=(9.5, 4.6), dpi=200)
    fig.patch.set_facecolor(WHITE)
    ax.set_facecolor(WHITE)

    series = [
        ("Weekday before", raw["weekday"], WEEKDAY_RAW_C, (4, 2)),
        ("Weekend before", raw["weekend"], WEEKEND_RAW_C, (4, 2)),
        ("Weekday after", clean["weekday"], WEEKDAY_C, None),
        ("Weekend after", clean["weekend"], WEEKEND_C, None),
    ]
    for label, d, color, dashes in series:
        line, = ax.plot(d["grid"], d["curve"], color=color, zorder=3,
                        linewidth=2.4 if dashes else 2.8,
                        solid_capstyle="round",
                        label=f"{label}  ({d['avg_per_person_day']:.2f} "
                              f"trips/person/day)")
        if dashes:
            line.set_dashes(dashes)

    ax.set_title(title, fontsize=15, color=INK, loc="left", weight="600", pad=10)
    ax.set_xlabel("Hour of day", fontsize=10.5, color=MUTED)
    ax.set_ylabel("Trips starting per hour", fontsize=10.5, color=MUTED)
    ax.set_xticks(range(0, 25, 3))
    ax.set_xticklabels([f"{h:02d}" for h in range(0, 25, 3)])
    ax.set_xlim(0, 24)
    # headroom for the two-column legend: at a bare ylim the tallest curve (the
    # raw weekday one) runs straight through the legend text
    tallest = max(float(d["curve"].max()) for _l, d, _c, _s in series)
    ax.set_ylim(0, tallest * 1.34)
    ax.grid(axis="y", color=GRID, linewidth=1, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9.5, length=0)

    handles, labels = ax.get_legend_handles_labels()
    handles.append(Line2D([], [], linestyle="none"))
    labels.append(f"All days  ({raw['all']['avg_per_person_day']:.2f} before, "
                  f"{clean['all']['avg_per_person_day']:.2f} after)")
    ax.legend(handles, labels, frameon=False, loc="upper left", fontsize=9,
              labelcolor=INK, ncol=2, columnspacing=1.6)

    fig.tight_layout()
    fig.savefig(out_path, facecolor=WHITE)
    plt.close(fig)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)

    w3 = load_app_schema(f"{BASE}/merged wave 3 with coordinates fixed.csv", True)
    w3 = w3[~(w3["_mode"].str.lower().isin(BLANK)
              & w3["_purpose"].str.lower().isin(BLANK))]

    waves = {
        "Wave 2": load_app_schema(
            f"{BASE}/Wave2 data with SD_coordinates_updated.xlsx", False),
        "Wave 3": w3,
    }

    def emit(wave, data, out):
        """`out=None` reports the numbers without drawing the two-curve chart,
        for a wave whose PNG is the four-curve before/after version instead."""
        if out is not None:
            chart_wave(wave, data, out)
        print(f"  {wave}: weekday {data['weekday']['avg_per_person_day']:.2f} | "
              f"weekend {data['weekend']['avg_per_person_day']:.2f} | "
              f"all days {data['all']['avg_per_person_day']:.2f}")

    print(f"RAW -> {OUT_DIR}")
    raw_summary = {}
    for wave, trips in waves.items():
        raw_summary[wave] = summarize(trips, wave)
        emit(wave, raw_summary[wave],
             f"{OUT_DIR}/{wave.replace(' ', '')}_wkday_wkend.png")

    os.makedirs(AFTER_DIR, exist_ok=True)
    print(f"BEFORE AND AFTER -> {AFTER_DIR}")
    for wave in ("Wave 2", "Wave 3"):
        trips, days = load_cleaned(wave.replace(" ", ""))
        clean = summarize_cleaned(trips, days, wave)
        emit(wave, clean, None)
        chart_before_after(
            wave, raw_summary[wave], clean,
            f"{AFTER_DIR}/{wave.replace(' ', '')}_wkday_wkend.png")


if __name__ == "__main__":
    main()
