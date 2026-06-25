"""Trip start-hour distribution before and after analysis.

Usage:
    python analysis/distribution.py [raw_xlsx]

Rounds each trip's start timestamp to the nearest clock hour, then plots trip
count by rounded start hour for weekdays and weekends, before analysis and after
the engine's all-auto-accepted analysis projection. It also plots after-cleaning
start-hour distributions as separate lines for each day of week.
"""

from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict
from datetime import timedelta

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.loader import LoadReport, load_trips  # noqa: E402
from engine.classify import classify_all  # noqa: E402
from engine.proposals import apply_all  # noqa: E402

DEFAULT_RAW = os.path.join("data", "app_data_wave2_raw.xlsx")
OUT = os.path.join("analysis", "output")
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def rounded_hour(dt) -> int:
    """Return nearest hour of day, with 30+ minutes rounded up."""
    if dt.minute >= 30:
        dt = dt + timedelta(hours=1)
    return dt.hour


def trip_counts_by_hour(trips: list, weekend: bool) -> list[int]:
    counts = Counter(
        rounded_hour(t.start_ts)
        for t in trips
        if (t.start_ts.weekday() >= 5) == weekend
    )
    return [counts.get(hour, 0) for hour in range(24)]


def trips_per_person_day(trips: list, weekend: bool) -> float:
    by_person_day = defaultdict(int)
    for trip in trips:
        if (trip.start_ts.weekday() >= 5) != weekend:
            continue
        by_person_day[(trip.person, trip.start_ts.date())] += 1
    if not by_person_day:
        return 0.0
    return sum(by_person_day.values()) / len(by_person_day)


def trip_counts_by_hour_for_day(trips: list, day: int) -> list[int]:
    counts = Counter(rounded_hour(t.start_ts) for t in trips if t.start_ts.weekday() == day)
    return [counts.get(hour, 0) for hour in range(24)]


def analyze(path: str) -> tuple[dict[str, dict], dict[str, dict[str, list[int]]], LoadReport]:
    report = LoadReport()
    trips = load_trips(path, report)
    analyzed_trips = apply_all(trips, classify_all(trips))
    counts = {
        "weekdays": _period_stats(trips, analyzed_trips, weekend=False),
        "weekend": _period_stats(trips, analyzed_trips, weekend=True),
    }
    by_day_counts = {
        "weekdays": {
            DAY_NAMES[day]: trip_counts_by_hour_for_day(analyzed_trips, day)
            for day in range(5)
        },
        "weekend": {
            DAY_NAMES[day]: trip_counts_by_hour_for_day(analyzed_trips, day)
            for day in range(5, 7)
        },
    }
    return counts, by_day_counts, report


def _period_stats(before_trips: list, after_trips: list, weekend: bool) -> dict:
    return {
        "before_counts": trip_counts_by_hour(before_trips, weekend=weekend),
        "after_counts": trip_counts_by_hour(after_trips, weekend=weekend),
        "before_trips_per_person_day": trips_per_person_day(before_trips, weekend=weekend),
        "after_trips_per_person_day": trips_per_person_day(after_trips, weekend=weekend),
    }


def plot_counts(name: str, title: str, stats: dict) -> str:
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, f"trip_start_hour_distribution_{name}.png")

    fig, ax = plt.subplots(figsize=(10, 5.5))
    hours = list(range(24))
    before = stats["before_counts"]
    after = stats["after_counts"]
    ax.plot(hours, before, color="#4c78a8", marker="o", linewidth=2.2, label="Before analysis")
    ax.plot(hours, after, color="#f28e2b", marker="o", linewidth=2.2, label="After analysis")
    ax.set_title(title)
    ax.set_xlabel("Rounded start hour")
    ax.set_ylabel("Trip count")
    ax.set_xticks(hours)
    ax.set_xlim(-0.6, 23.6)
    ax.grid(axis="y", alpha=0.25)
    ax.legend()

    ax.text(
        0.99,
        0.98,
        (
            f"before n = {sum(before):,}"
            f"\nafter n = {sum(after):,}"
            f"\nbefore trips/person-day = {stats['before_trips_per_person_day']:.2f}"
            f"\nafter trips/person-day = {stats['after_trips_per_person_day']:.2f}"
        ),
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=10,
    )
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def plot_after_cleaning_day_lines(name: str, title: str, series: dict[str, list[int]]) -> str:
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, f"after_cleaning_start_hour_distribution_by_day_{name}.png")

    fig, ax = plt.subplots(figsize=(10, 5.5))
    hours = list(range(24))
    for day, counts in series.items():
        ax.plot(hours, counts, marker="o", linewidth=2.0, label=day)
    ax.set_title(title)
    ax.set_xlabel("Rounded start hour")
    ax.set_ylabel("Trip count after cleaning")
    ax.set_xticks(hours)
    ax.set_xlim(-0.6, 23.6)
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    ax.text(
        0.99,
        0.98,
        f"after n = {sum(sum(counts) for counts in series.values()):,}",
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=10,
    )
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RAW
    counts, by_day_counts, report = analyze(path)
    outputs = [
        plot_counts("weekdays", "Trip Start Hour Distribution - Weekdays", counts["weekdays"]),
        plot_counts("weekend", "Trip Start Hour Distribution - Weekend", counts["weekend"]),
        plot_after_cleaning_day_lines(
            "weekdays",
            "After-Cleaning Start Hour Distribution by Day - Weekdays",
            by_day_counts["weekdays"],
        ),
        plot_after_cleaning_day_lines(
            "weekend",
            "After-Cleaning Start Hour Distribution by Day - Weekend",
            by_day_counts["weekend"],
        ),
    ]

    print(f"loaded {report.loaded:,} trips from {path}")
    if report.skipped:
        print(f"skipped {report.skipped:,} rows without usable times/geometry")
    for label, stats in counts.items():
        before = stats["before_counts"]
        after = stats["after_counts"]
        print(f"\n{label}")
        print(
            "trips_per_person_day,"
            f"{stats['before_trips_per_person_day']:.3f},"
            f"{stats['after_trips_per_person_day']:.3f}"
        )
        print("rounded_hour,before_analysis,after_analysis")
        for hour, (before_count, after_count) in enumerate(zip(before, after)):
            print(f"{hour:02d},{before_count},{after_count}")
    for label, series in by_day_counts.items():
        print(f"\nafter_cleaning_start_hour_by_day_{label}")
        print("day,rounded_hour,trip_count")
        for day, day_counts in series.items():
            for hour, count in enumerate(day_counts):
                print(f"{day},{hour:02d},{count}")
    for out in outputs:
        print(f"chart: {out}")


if __name__ == "__main__":
    main()
