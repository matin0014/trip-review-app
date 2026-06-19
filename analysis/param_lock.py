"""Stage 3 — parameter-lock MEASUREMENT (read-only; no engine edits).

Loads the real wave2 data and reports the evidence behind the three provisional
params, as both console tables and PNG charts under analysis/output/:

  1. v_move_min_kmh        — global segment-speed histogram (low end)
  2. mode_low/high_kmh     — per-mode trip median-moving-speed distribution
  3. missing_band_min      — residual-minus-buffer distribution for gaps past
                             the fragment branch (missing_trip vs activity)

    python analysis/param_lock.py [raw_xlsx]

Nothing here writes to engine/params.py — we look at the numbers first, THEN lock.
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")  # headless; we only save PNGs
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.classify import (  # noqa: E402
    PATHOLOGICAL_KMH, expected_time_min, group_person_days, pair_geometry,
)
from engine.loader import load_trips  # noqa: E402
from engine.params import DEFAULT  # noqa: E402
from engine.velocity import segment_speeds, trip_moving_speed  # noqa: E402

DEFAULT_RAW = os.path.join("data", "app_data_wave2_raw.xlsx")
OUT = os.path.join("analysis", "output")
PCTS = [10, 25, 50, 75, 90]


def pct(xs: list, ps=PCTS) -> dict:
    a = np.asarray(xs, dtype=float)
    return {p: float(np.percentile(a, p)) for p in ps}


# ---------------------------------------------------------------- 1. v_move_min
def report_segment_speeds(trips: list) -> None:
    speeds = []
    for t in trips:
        speeds += [s for s in segment_speeds(t.route) if s <= DEFAULT.glitch_cap_kmh]
    a = np.asarray(speeds)
    print("\n=== 1. SEGMENT SPEEDS (v_move_min) - non-glitch segments ===")
    print(f"  segments: {a.size:,}   (current v_move_min = {DEFAULT.v_move_min_kmh} km/h)")
    print("  low-end 1 km/h bins (count, % of all):")
    for lo in range(0, 12):
        n = int(((a >= lo) & (a < lo + 1)).sum())
        print(f"    {lo:>2}-{lo+1:<2} km/h : {n:>8,}  {100*n/a.size:5.1f}%")

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.hist(a[a <= 20], bins=np.arange(0, 20.25, 0.25), color="#4c78a8")
    ax.axvline(DEFAULT.v_move_min_kmh, color="#e45756", lw=2,
               label=f"current v_move_min = {DEFAULT.v_move_min_kmh}")
    ax.set(xlabel="segment speed (km/h)", ylabel="segments",
           title="Segment speeds 0–20 km/h  (jitter floor → walking)")
    ax.legend()
    _save(fig, "param_lock_segment_speeds.png")


# ------------------------------------------------------- 2. mode bands (12/25)
def report_mode_speeds(trips: list) -> None:
    by_mode = defaultdict(list)          # mode -> list of (speed, is_manual)
    zeros = defaultdict(int)
    for t in trips:
        v = trip_moving_speed(t)
        manual = bool((t.purpose or "").strip())
        if v <= 0:
            zeros[t.mode] += 1            # stationary (zero_move) — not a travel speed
        else:
            by_mode[t.mode].append((v, manual))

    rows = sorted(by_mode.items(), key=lambda kv: np.median([s for s, _ in kv[1]]))
    print("\n=== 2. PER-MODE MEDIAN MOVING SPEED (mode bands 12/25) ===")
    print("  man% = share with a user-logged purpose;  >=hi% = share at/above mode_high")
    print(f"  {'mode':<18}{'n':>6}{'zero':>6}{'man%':>6}" +
          "".join(f"{'p'+str(p):>7}" for p in PCTS) + f"{'>=hi%':>7}")
    for mode, pairs in rows:
        xs = [s for s, _ in pairs]
        q = pct(xs)
        man = 100 * sum(1 for _, m in pairs if m) / len(pairs)
        fast = 100 * sum(1 for s in xs if s >= DEFAULT.mode_high_kmh) / len(xs)
        print(f"  {mode:<18}{len(xs):>6}{zeros[mode]:>6}{man:>6.0f}" +
              "".join(f"{q[p]:>7.1f}" for p in PCTS) + f"{fast:>7.0f}")

    # Drilldown on Matin's worry: are fast "Walk" trips user-logged or auto-only?
    walk = by_mode.get("Walk", [])
    for tag, sub in (("manual (user-logged)", [s for s, m in walk if m]),
                     ("auto (no purpose)", [s for s, m in walk if not m])):
        if not sub:
            continue
        a = np.asarray(sub)
        print(f"  Walk {tag:<22}: n={a.size:<5} median={np.median(a):4.1f}  "
              f">=12: {int((a>=12).sum())} ({100*(a>=12).mean():.0f}%)  "
              f">=25: {int((a>=25).sum())} ({100*(a>=25).mean():.0f}%)")

    fig, ax = plt.subplots(figsize=(9, 5.5))
    labels = [m for m, _ in rows]
    ax.boxplot([[s for s, _ in pairs] for _, pairs in rows], vert=False,
               tick_labels=labels, showfliers=False, whis=[10, 90])
    ax.axvline(DEFAULT.mode_low_kmh, color="#54a24b", lw=2, ls="--",
               label=f"mode_low = {DEFAULT.mode_low_kmh}")
    ax.axvline(DEFAULT.mode_high_kmh, color="#e45756", lw=2, ls="--",
               label=f"mode_high = {DEFAULT.mode_high_kmh}")
    ax.set(xlabel="trip median moving speed (km/h)", xlim=(0, 80),
           title="Per-mode median moving speed (box = p25–p75, whiskers p10–p90)")
    ax.legend(loc="lower right")
    _save(fig, "param_lock_mode_speeds.png")


# ------------------------------------------------- 3. missing band (20 min)
def report_missing_band(trips: list) -> None:
    over = []  # residual - buffer, for pairs past the fragment branch
    for day in group_person_days(trips).values():
        for prev, cur in zip(day, day[1:]):
            ev = pair_geometry(prev, cur)
            if ev.gap_time_min <= 0:
                continue
            if ev.gap_time_min < expected_time_min(ev.gap_distance_m, PATHOLOGICAL_KMH):
                continue
            if ev.v_a_kmh <= 0 or ev.v_b_kmh <= 0:
                continue
            if ev.gap_distance_m <= DEFAULT.gate_m:
                continue
            slack = ev.residual_min - ev.buffer_min
            if slack >= 0:                # beyond fragment -> missing_trip / activity
                over.append(slack)

    a = np.asarray(over)
    band = DEFAULT.missing_band_min
    n_trip = int((a <= band).sum())
    print("\n=== 3. RESIDUAL - BUFFER for gaps past the fragment branch (missing band) ===")
    print(f"  pairs in region: {a.size:,}   (current missing_band = {band} min)")
    print(f"  split at {band} min:  missing_trip {n_trip:,} ({100*n_trip/a.size:.0f}%) "
          f"| missing_activity {a.size-n_trip:,} ({100*(a.size-n_trip)/a.size:.0f}%)")
    q = pct(over)
    print("  percentiles (min over buffer): " +
          "  ".join(f"p{p}={q[p]:.1f}" for p in PCTS))

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.hist(a[a <= 120], bins=np.arange(0, 122, 5), color="#72b7b2")
    ax.axvline(band, color="#e45756", lw=2, label=f"current band = {band} min")
    ax.set(xlabel="residual − buffer (min)", ylabel="pairs",
           title="Gap excess past fragment branch (≤120 min)  → trip vs activity")
    ax.legend()
    _save(fig, "param_lock_missing_band.png")


def _save(fig, name: str) -> None:
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print(f"  -> chart: {path}")


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RAW
    trips = load_trips(path)
    print(f"loaded {len(trips):,} trips from {path}")
    report_segment_speeds(trips)
    report_mode_speeds(trips)
    report_missing_band(trips)


if __name__ == "__main__":
    main()
