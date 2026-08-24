"""Distributions ZIP: the QC charts for a cleaned dataset, one PNG each.

Responsibility: turn a cleaned-file-shaped DataFrame into a ZIP of PNG charts,
from either a live session's current state or an arbitrary loaded
`cleaned.xlsx`. Ported (not imported) from the dev-only `analysis/chartkit.py`
and `analysis/final_charts.py` -- `analysis/` is not bundled by PyInstaller, so
nothing in the shipped app can import from it (see the comment on this in
`app/generate.py`). matplotlib is imported lazily, only inside the functions
that need it, so the app's startup and non-chart tests stay light -- the same
convention `app/charts.py` already follows.

Definitions match `analysis/final_charts.py` so a chart drawn here means the
same thing as the equivalent offline QC chart:

  * A TRIP is a plain / M / A / S row. Q rows mark a reviewed gap in ACTIVITY
    between two trips and Z rows stand for a whole day with no travel; neither
    is a journey, so neither is ever counted as one.
  * An ACTIVITY is the gap from one trip's END to the next trip's START within
    one person-day, so overnight is not an activity. Its purpose is the
    EARLIER trip's purpose: you travel *in order to* do the thing you then do.
  * Mode and purpose are normalised before any grouping -- the survey embeds
    zero-width characters and a trailing parenthetical prompt into these
    columns, which would otherwise split one real category into several.
"""

from __future__ import annotations

import re

REQUIRED_COLUMNS = {"trip_id", "mode_of_travel", "purpose_of_travel", "start_date",
                    "start_time", "end_date", "end_time", "accessCode"}

# --- ink (ported from analysis/chartkit.py) ---------------------------------
# White surface. Every text, grid and spine colour is still set explicitly:
# matplotlib's defaults assume a white ground too, but leaving any of them
# implicit means a later theme change silently breaks them.
SURFACE = "#ffffff"
INK, MUTED, FAINT, GRID = "#12151b", "#5b6478", "#c3c8d3", "#e5e7eb"
# One series only (a single dataset, not a wave-vs-wave comparison), so ACCENT
# is used purely as a within-series highlight -- the "6h+" overflow bar, the
# >7-days bars -- never as a second identity-bearing series.
SERIES, ACCENT, MISSING = "#16a34a", "#d97706", "#8b93a7"

_INVISIBLE = re.compile(r"[​‌‍﻿\xa0]")
_TZ = re.compile(r"([+-]\d{2}:?\d{2})$")
_PAREN = re.compile(r"\s*\([^)]*\)\s*$")

STOP_PURPOSE = "Travel to transit stop"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday"]
# "Returning Home" is not an out-of-home activity: the gap after a homeward
# trip is time spent AT HOME until the next departure. "Not recorded" is the
# absence of an answer. Both stay in the overall duration histogram, which is
# about gaps between trips rather than activity types.
EXCLUDED_PURPOSES = {"Returning Home", "Not recorded"}


# --- styling helpers (ported from analysis/chartkit.py) --------------------

def _figure(*args, **kwargs):
    """`plt.subplots` with the ground already painted onto figure and axes."""
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(*args, **kwargs)
    fig.patch.set_facecolor(SURFACE)
    for a in (ax.flat if hasattr(ax, "flat") else [ax]):
        a.set_facecolor(SURFACE)
    return fig, ax


def _png_saver(out_dir: str):
    """Returns ``save(fig, name)``, writing one PNG per chart into `out_dir`.

    `savefig` defaults to a white ground regardless of the figure's own, so the
    facecolor is passed explicitly: that is a no-op while SURFACE is white, and
    the thing that keeps the export correct if it ever is not again. Each figure
    is closed as it is written, so building a whole set never holds eight open
    figures in matplotlib's global registry.
    """
    import os
    import matplotlib.pyplot as plt

    def save(fig, name: str) -> str:
        path = os.path.join(out_dir, name)
        fig.savefig(path, dpi=170, facecolor=SURFACE)
        plt.close(fig)
        return path

    return save


def _style(ax, title=None, xlabel=None, ylabel=None, axis="y"):
    """Recessive frame: the data should be the only thing with weight."""
    ax.set_facecolor(SURFACE)
    if title:
        ax.set_title(title, fontsize=15, color=INK, pad=16, loc="left", weight="600")
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=10.5, color=MUTED, labelpad=8)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=10.5, color=MUTED, labelpad=8)
    ax.grid(axis=axis, color=GRID, linewidth=1, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=10, length=0)


def _headline(fig, text, y=0.03, x=0.07):
    fig.text(x, y, text, fontsize=13, color=INK, va="bottom", weight="bold")


def _hour_of_day(series):
    """Start times as fractional hours (08:30 -> 8.5), minutes kept. Anything
    unparseable is dropped rather than coerced to midnight, which would pile
    phantom trips onto hour 0."""
    import numpy as np

    out = []
    for v in series.dropna():
        m = re.match(r"^(\d{1,2}):(\d{2})", str(v))
        if m:
            out.append(int(m.group(1)) + int(m.group(2)) / 60.0)
    return np.asarray(out, dtype=float)


def _circular_kde(hours, bw: float = 0.7, n: int = 721):
    """``(grid, density)`` over 0..24 with the day treated as a circle -- see
    `analysis/chartkit.py` for the full rationale (no bin edges, wraps at
    midnight the short way round the clock)."""
    import numpy as np

    grid = np.linspace(0, 24, n)
    if hours.size == 0:
        return grid, np.zeros_like(grid)
    d = np.abs(grid[:, None] - hours[None, :])
    d = np.minimum(d, 24 - d)
    dens = np.exp(-0.5 * (d / bw) ** 2).sum(axis=1)
    dens /= bw * (2 * 3.141592653589793) ** 0.5 * hours.size
    return grid, dens


def _hour_axis(ax, step: int = 3):
    ax.set_xticks(range(0, 25, step))
    ax.set_xticklabels([f"{h:02d}" if h < 24 else "24" for h in range(0, 25, step)])
    ax.set_xlim(0, 24)


# --- data shaping (ported from analysis/final_charts.py) -------------------

def _norm(s) -> str:
    """Survey text with invisible characters and the trailing prompt stripped,
    so the short and long spellings of one category group together."""
    import pandas as pd

    if pd.isna(s):
        return ""
    return _PAREN.sub("", _INVISIBLE.sub("", str(s)).strip()).strip()


def _stamp(date, tm):
    import pandas as pd

    if pd.isna(date) or pd.isna(tm):
        return pd.NaT
    return pd.to_datetime(f"{str(date)[:10]} {_TZ.sub('', str(tm)).strip()}",
                          errors="coerce")


def _trips_per_hour(trips, bw: float = 0.7):
    """Smooth start-time curve in TRIPS per hour, not density -- multiplying the
    normalised density back out by the trip count puts the axis back in the
    unit a reader thinks in ('about 300 trips start around 5pm')."""
    hours = _hour_of_day(trips["start_time"])
    grid, dens = _circular_kde(hours, bw=bw)
    return grid, dens * hours.size


def _collapse_count(day) -> int:
    """Trips on one person-day belonging to one JOURNEY counted once:
    consecutive shopping; a Walk for Recreation then a Walk Returning Home. A
    trip whose purpose is travel to a transit stop is not counted at all: it
    exists only to reach the transit ride that follows, not as a trip in its
    own right."""
    rows = day.to_dict("records")
    n = i = 0
    while i < len(rows):
        cur = rows[i]
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        if cur["_purpose"] == STOP_PURPOSE:
            i += 1
            continue
        if cur["_purpose"] == "Shopping":
            while i + 1 < len(rows) and rows[i + 1]["_purpose"] == "Shopping":
                i += 1
        elif (cur["_mode"] == "Walk" and cur["_purpose"] == "Recreation"
              and nxt is not None and nxt["_mode"] == "Walk"
              and nxt["_purpose"] == "Returning Home"):
            i += 1
        n += 1
        i += 1
    return n


def _adjusted_trips(trips) -> int:
    ordered = trips.sort_values(["accessCode", "start_date", "start_time"])
    return int(sum(_collapse_count(d) for _k, d in
                   ordered.groupby(["accessCode", "start_date"], sort=False)))


def _activity_records(trips):
    """One row per within-day consecutive trip pair: the gap, and the purpose
    of the earlier trip (what the person travelled in order to do)."""
    import pandas as pd

    t = trips.dropna(subset=["_start", "_end"]).sort_values(
        ["accessCode", "start_date", "_start"])
    recs = []
    for (person, date), day in t.groupby(["accessCode", "start_date"], sort=False):
        rows = day.to_dict("records")
        for a, b in zip(rows, rows[1:]):
            mins = (b["_start"] - a["_end"]).total_seconds() / 60.0
            if mins < 0:
                continue  # next trip starts before this one ends: a logging error
            purpose = a["_purpose"]
            if not purpose or purpose.lower() == "empty":
                purpose = "Not recorded"
            recs.append({"accessCode": person, "start_date": date,
                         "purpose": purpose, "hours": mins / 60.0})
    return pd.DataFrame(recs)


# --- charts ------------------------------------------------------------------

def _chart_start_times(trips, pdays, title, save, name):
    """Smoothed, not binned -- see `_circular_kde`'s docstring for why."""
    import numpy as np

    grid, curve = _trips_per_hour(trips)
    fig, ax = _figure(figsize=(12, 6.2))
    fig.subplots_adjust(top=0.89, bottom=0.17, left=0.08, right=0.97)
    ax.fill_between(grid, curve, color=SERIES, alpha=0.12, zorder=2)
    ax.plot(grid, curve, color=SERIES, linewidth=3, zorder=3, solid_capstyle="round")

    am = slice(0, int(len(grid) * 11 / 24))
    pm = slice(int(len(grid) * 11 / 24), len(grid))
    for window, dy in ((am, 12), (pm, 12)):
        i = window.start + int(np.argmax(curve[window]))
        h = grid[i]
        ax.plot([h], [curve[i]], "o", color=SERIES, markersize=6, zorder=4)
        ax.annotate(f"{int(h):02d}:{int(round((h % 1) * 60)):02d}",
                    xy=(h, curve[i]), xytext=(0, dy), textcoords="offset points",
                    ha="center", fontsize=10.5, color=SERIES, weight="700")

    _style(ax, title, "Hour of day", "Trips starting per hour")
    _hour_axis(ax)
    ax.set_ylim(0, curve.max() * 1.16 if curve.max() > 0 else 1)
    n_days = len(pdays) or 1
    _headline(fig, f"{len(trips):,} trips   ·   "
                  f"{_adjusted_trips(trips) / n_days:.2f} trips per person per day")
    save(fig, name)


def _chart_start_times_by_weekday(trips, pdays, save):
    """Seven panels on ONE shared y-scale: the point is comparing days, which a
    per-panel scale would quietly defeat."""
    fig, axes = _figure(2, 4, figsize=(15, 7.6), sharey=True, sharex=True)
    fig.subplots_adjust(top=0.85, bottom=0.14, left=0.05, right=0.98,
                        hspace=0.38, wspace=0.12)
    grid, week = _trips_per_hour(trips)
    week = week / 7.0
    peak = week.max()
    for i, day in enumerate(WEEKDAYS):
        ax = axes[i // 4][i % 4]
        sub = trips[trips["_dayname"] == day]
        ax.plot(grid, week, color=FAINT, linewidth=1.2, zorder=2, linestyle=(0, (3, 2)))
        g, curve = _trips_per_hour(sub, bw=0.85)   # thinner slice, more smoothing
        peak = max(peak, curve.max())
        ax.fill_between(g, curve, color=SERIES, alpha=0.12, zorder=3)
        ax.plot(g, curve, color=SERIES, linewidth=2.2, zorder=4, solid_capstyle="round")
        _style(ax)
        ax.set_title(f"{day}   ({len(sub):,} trips)", fontsize=11, color=INK,
                     loc="left", pad=6, weight="600")
        ax.set_xticks(range(0, 25, 6))
        ax.set_xlim(0, 24)
    # `sharey` means whichever panel autoscaled LAST sets the limit for all
    # seven, which clips the busiest days; set the shared limit explicitly.
    axes[0][0].set_ylim(0, peak * 1.10 if peak > 0 else 1)
    axes[1][3].axis("off")
    # Thursday sits above the hidden 8th cell, so `sharex` leaves its column
    # with no hour labels anywhere -- put them back on the panel itself.
    axes[0][3].tick_params(labelbottom=True)
    fig.suptitle("Trip start times by weekday", fontsize=15, color=INK, x=0.05,
                 ha="left", y=0.965, weight="600")
    _headline(fig, f"{len(trips):,} trips   ·   shared vertical scale, so panels "
                  f"compare directly", y=0.045, x=0.05)
    save(fig, "start_times_by_weekday.png")


def _chart_activity_hist(recs, save):
    import numpy as np

    # Cut at 6h rather than 12: beyond about three hours every bar is a flat
    # sliver, so half the chart's width was spent on a few percent of the data.
    CAP = 6
    hours = recs["hours"].values
    edges = np.arange(0, CAP + 0.5, 0.5)
    counts, _ = np.histogram(np.clip(hours, 0, CAP - 1e-9), bins=edges)
    over = int((hours >= CAP).sum())
    fig, ax = _figure(figsize=(12, 6.2))
    fig.subplots_adjust(top=0.89, bottom=0.17, left=0.08, right=0.97)
    ax.bar(edges[:-1] + 0.25, counts, width=0.42, color=SERIES, zorder=3)
    if over:
        ax.bar([CAP + 0.25], [over], width=0.42, color=ACCENT, zorder=3)
        ax.annotate(f"{over:,}", xy=(CAP + 0.25, over), xytext=(0, 6),
                    textcoords="offset points", ha="center", fontsize=9.5,
                    color=ACCENT, weight="600")
    med = float(np.median(hours)) if len(hours) else 0.0
    ax.axvline(med, color=INK, linewidth=1.3, linestyle=(0, (4, 3)), zorder=4)
    ax.annotate(f"median {med * 60:.0f} min",
                xy=(med, counts.max() * 0.88 if counts.max() > 0 else 0),
                xytext=(9, 0), textcoords="offset points", fontsize=10.5,
                color=INK, weight="600")
    _style(ax, "Activity duration between consecutive trips",
          "Hours (30-minute bins)", "Activities")
    ax.set_xticks(list(range(0, CAP)) + [CAP + 0.25])
    ax.set_xticklabels([str(h) for h in range(0, CAP)] + [f"{CAP}h+"])
    ax.set_xlim(-0.35, CAP + 0.85)
    under_hour = (hours < 1).mean() if len(hours) else 0.0
    _headline(fig, f"{len(hours):,} activities   ·   median "
                  f"{med * 60:.0f} min   ·   {under_hour:.0%} under an hour")
    save(fig, "activity_duration.png")


def _purpose_bars(series, counts, title, xlabel, save, name, headline_text):
    order = series.sort_values(ascending=True)
    fig, ax = _figure(figsize=(11, 7.4))
    fig.subplots_adjust(top=0.91, bottom=0.13, left=0.30, right=0.97)
    colors = [MISSING if p == "Not recorded" else SERIES for p in order.index]
    ax.barh(order.index, order.values, height=0.68, color=colors, zorder=3)
    for p, v in order.items():
        ax.text(v + order.max() * 0.012, p, f"{v:.1f} h   (n={counts[p]:,})",
                va="center", fontsize=9, color=MUTED)
    _style(ax, title, xlabel, axis="x")
    ax.set_xlim(0, order.max() * 1.28 if order.max() > 0 else 1)
    _headline(fig, headline_text)
    save(fig, name)


def _chart_activity_by_purpose(recs, save):
    recs = recs[~recs["purpose"].isin(EXCLUDED_PURPOSES)]
    episode = recs.groupby("purpose")["hours"].mean()
    n_ep = recs.groupby("purpose")["hours"].size()
    _purpose_bars(episode, n_ep, "Average activity duration per episode, by purpose",
                  "Hours per episode", save, "activity_by_purpose_episode.png",
                  f"{len(recs):,} episodes   ·   overall mean "
                  f"{recs['hours'].mean():.1f} h per episode" if len(recs) else "No activities")

    # total hours of one purpose on one person-day, then averaged across the
    # days that purpose actually occurred -- averaging over ALL days would
    # answer a different question (how much of a typical day goes to it,
    # zeros included).
    per_day = (recs.groupby(["accessCode", "start_date", "purpose"])["hours"].sum()
               .reset_index())
    daily = per_day.groupby("purpose")["hours"].mean()
    n_days = per_day.groupby("purpose")["hours"].size()
    n_person_days = len(recs[["accessCode", "start_date"]].drop_duplicates())
    _purpose_bars(daily, n_days, "Average hours per day spent on each activity, by purpose",
                  "Hours per day on which the activity occurred", save,
                  "activity_by_purpose.png",
                  f"{n_person_days:,} person-days   ·   each purpose averaged only "
                  f"over the days it occurred")


def _chart_days_per_participant(days, save):
    per_person = days.groupby("accessCode").size()
    counts = per_person.value_counts().sort_index()
    fig, ax = _figure(figsize=(11, 6.0))
    fig.subplots_adjust(top=0.90, bottom=0.15, left=0.075, right=0.98)
    ax.bar(counts.index, counts.values, width=0.72,
           color=[ACCENT if d > 7 else SERIES for d in counts.index], zorder=3)
    for d, v in counts.items():
        ax.text(d, v, str(v), ha="center", va="bottom", fontsize=8, color=MUTED)
    _style(ax, "Days recorded per participant", "Days recorded", "Participants")
    ax.set_xticks(range(int(counts.index.min()), int(counts.index.max()) + 1))
    ax.set_ylim(0, counts.max() * 1.15)
    over7 = int((per_person > 7).sum())
    n_people = len(per_person) or 1
    _headline(fig, f"{per_person.mean():.2f} days per participant   ·   {over7} of "
                  f"{n_people} ({over7 / n_people:.0%}) recorded more than 7 days")
    save(fig, "days_per_participant.png")


def _chart_modes(trips, save):
    modes = trips["_mode"].replace("", "Not recorded")
    counts = modes.value_counts().sort_values(ascending=True)
    total = int(counts.sum()) or 1
    fig, ax = _figure(figsize=(11, 7.4))
    fig.subplots_adjust(top=0.91, bottom=0.13, left=0.18, right=0.97)
    ax.barh(counts.index, counts.values, height=0.68,
            color=[MISSING if m in ("Unknown", "Not recorded") else SERIES
                   for m in counts.index], zorder=3)
    for m, v in counts.items():
        ax.text(v + total * 0.006, m, f"{v:,}  ({v / total:.1%})",
                va="center", fontsize=9, color=MUTED)
    _style(ax, "Trips by mode of travel", "Trips", axis="x")
    ax.set_xlim(0, counts.max() * 1.16 if len(counts) else 1)
    car = int(counts.get("Auto Driver", 0) + counts.get("Auto Passenger", 0))
    _headline(fig, f"{total:,} trips   ·   {car / total:.0%} by car   ·   "
                  f"{counts.get('Walk', 0) / total:.0%} on foot")
    save(fig, "modes.png")


# --- entry points ------------------------------------------------------------

def build_distributions_zip(df, out_path: str) -> str:
    """Write the distribution charts as a ZIP of PNGs at `out_path`.

    One PNG per chart rather than one multi-page PDF: a reader wanting a single
    chart in a slide or a document can take it straight out of the archive,
    which a PDF page cannot give them without a screenshot.

    Raises ValueError if the required columns aren't present -- a friendly
    failure instead of a KeyError three helper functions deep, e.g. if this is
    pointed at a differently-shaped export.
    """
    import os
    import shutil
    import tempfile
    import zipfile

    import pandas as pd
    import matplotlib
    matplotlib.use("Agg")  # headless: write a file, never try to display

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"not a cleaned trip-review file (missing columns: {sorted(missing)})")

    work = df.copy()
    tid = work["trip_id"].astype(str)
    work["_mode"] = work["mode_of_travel"].map(_norm)
    work["_purpose"] = work["purpose_of_travel"].map(_norm)
    dt = pd.to_datetime(work["start_date"], errors="coerce")
    work["_weekday"] = dt.dt.weekday
    work["_dayname"] = dt.dt.day_name()

    trips = work[~tid.str.startswith("Q") & ~tid.str.startswith("Z")].copy()
    trips["_start"] = [_stamp(d, t) for d, t in zip(trips["start_date"], trips["start_time"])]
    trips["_end"] = [_stamp(d, t) for d, t in zip(trips["end_date"], trips["end_time"])]
    days = work.dropna(subset=["accessCode", "start_date"])[
        ["accessCode", "start_date", "_weekday"]].drop_duplicates()
    recs = _activity_records(trips)

    # Charts are rendered into a scratch directory and zipped at the end, so a
    # failure part-way through leaves no half-written archive at `out_path`.
    tmp_dir = tempfile.mkdtemp(prefix="tripreview_dist_")
    try:
        save = _png_saver(tmp_dir)
        _chart_start_times(trips, days, "Trip start times, all days", save,
                           "start_times.png")
        wd = trips[trips["_weekday"] < 5]
        _chart_start_times(wd, days[days["_weekday"] < 5],
                           "Trip start times, weekdays (Mon-Fri)", save,
                           "start_times_weekday.png")
        _chart_start_times_by_weekday(trips, days, save)
        _chart_activity_hist(recs, save)
        _chart_activity_by_purpose(recs, save)
        _chart_days_per_participant(
            days[["accessCode", "start_date"]].drop_duplicates(), save)
        _chart_modes(trips, save)

        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in sorted(os.listdir(tmp_dir)):
                zf.write(os.path.join(tmp_dir, name), arcname=name)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return out_path


def build_distributions_zip_for_session(sid: str, out_path: str) -> str:
    """Session case: 'cleaned' is the reviewer's CURRENT live state, reusing
    `generate.generate_cleaned_and_dofile` so it is never computed a second,
    possibly-divergent way."""
    import pandas as pd
    from app import generate as _generate

    cleaned_path, _dofile_path = _generate.generate_cleaned_and_dofile(sid)
    return build_distributions_zip(pd.read_excel(cleaned_path), out_path)
