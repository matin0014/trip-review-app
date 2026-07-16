"""Start-time distribution series + export PNGs (raw vs live-cleaned).

Responsibility: one shared computation behind BOTH the in-app live chart
(GET /api/<sid>/start_times renders it as SVG client-side) and the two PNGs
added to the Generate-export zip. "cleaned" here is the reviewer's CURRENT
live state: each day's fixpoint projection (accepted flags + manual edits),
with excluded (out-of-region non-recovered, or hand-removed) days dropped
entirely. Raw is every loaded trip, excluded days included, so the gap shows
the reviewer's full cleaning effect. The binning rule and PNG style are
copied from the offline QC script `analysis/distributions.py` (dev-only, not
bundled, so it cannot be imported from here). matplotlib is imported lazily
inside the PNG writer only; the series math needs none of it.
"""

from __future__ import annotations

import os

from app import regions
from app.decisions import (APPLY_RESOLUTIONS, excluded_days, fixpoint, manual_ops,
                           resolutions)

# (all-days label, weekdays-only label) keyed the way the JSON response is.
_SCOPES = {"all": "all days", "weekday": "weekdays only (Mon-Fri)"}


def rounded_hour(ts) -> int:
    """Start time rounded to the NEAREST hour: minute >= 30 rounds up, else down.
    Wraps 24 -> 0 so a 23:30+ trip lands at midnight (hour-of-day is circular)."""
    return (ts.hour + (1 if ts.minute >= 30 else 0)) % 24


def _bins(timestamps: list) -> list:
    counts = [0] * 24
    for ts in timestamps:
        counts[rounded_hour(ts)] += 1
    return counts


def _variant(raw_ts: list, cleaned_ts: list, weekday_only: bool) -> dict:
    if weekday_only:
        raw_ts = [ts for ts in raw_ts if ts.weekday() < 5]
        cleaned_ts = [ts for ts in cleaned_ts if ts.weekday() < 5]
    return {"raw": _bins(raw_ts), "cleaned": _bins(cleaned_ts),
            "n_raw": len(raw_ts), "n_cleaned": len(cleaned_ts)}


def start_time_series(days: dict, events: list) -> dict:
    """``{"all": {raw, cleaned, n_raw, n_cleaned}, "weekday": {...}}`` for one
    session: 24 hourly bins per series.

    ``days`` = ``{day_key: [Trip]}`` (``state.all_days(sid)`` for the live
    endpoint; ``group_person_days(load_trips(...))`` reshaped for the export --
    both already in memory, nothing is re-read here). ``events`` = the session's
    full decision log; the live cleaned projection is derived exactly the way
    the viewer and the export do it (same fixpoint, same excluded-days rule).
    """
    res = resolutions(events)
    accepted = {fid for fid, r in res.items() if r in APPLY_RESOLUTIONS}
    ops_all = manual_ops(events)
    auto_out = regions.out_of_region_days(days, regions.load_regions())
    excluded = excluded_days(events, auto_out)

    raw_ts: list = []
    cleaned_ts: list = []
    for key, trips in days.items():
        raw_ts += [t.start_ts for t in trips]
        if key in excluded:
            continue  # dropped day: contributes to raw only
        ops = [op for op in ops_all if op.get("day_key") == key]
        live, _ = fixpoint(trips, accepted, ops=ops)
        cleaned_ts += [t.start_ts for t in live]

    return {"all": _variant(raw_ts, cleaned_ts, weekday_only=False),
            "weekday": _variant(raw_ts, cleaned_ts, weekday_only=True)}


def write_start_time_pngs(series: dict, out_dir: str) -> list:
    """Render the two QC-style PNGs (``start_times.png`` all days,
    ``start_times_weekday.png`` weekdays only) into ``out_dir``; return their
    paths. Visual style matches the original offline charts: raw and cleaned
    lines with point markers, hour ticks 0-23, legend with n=. matplotlib is
    imported here (not at module top) so the app and its tests only pay for it
    when a PNG is actually written."""
    import matplotlib

    matplotlib.use("Agg")  # headless: write files, no display
    import matplotlib.pyplot as plt

    paths = []
    for scope_key, fname in (("all", "start_times.png"),
                             ("weekday", "start_times_weekday.png")):
        v = series[scope_key]
        fig, ax = plt.subplots(figsize=(11, 5))
        ax.plot(range(24), v["raw"], marker="o", color="tab:blue",
                label=f"raw (n={v['n_raw']})")
        ax.plot(range(24), v["cleaned"], marker="o", color="tab:orange",
                label=f"cleaned (n={v['n_cleaned']})")
        ax.set_xticks(range(24))
        ax.set_xlabel("hour of day (start time rounded to nearest hour)")
        ax.set_ylabel("number of trips")
        ax.set_title(f"Trip start times, {_SCOPES[scope_key]} (raw vs cleaned)")
        ax.grid(alpha=0.3)
        ax.legend()
        fig.tight_layout()
        path = os.path.join(out_dir, fname)
        fig.savefig(path, dpi=110)
        plt.close(fig)
        paths.append(path)
    return paths
