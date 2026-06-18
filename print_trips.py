"""Dev CLI: load a raw trip workbook and print parsed trips (Step 1b visibility).

    python print_trips.py [path] [--limit N]

Defaults to data/app_data_wave2_raw.xlsx. Engine-only (no Flask); prints a load
summary, the person-day breakdown, and a sample of fully-parsed trips so the
data-in pipeline can be eyeballed end to end.
"""

from __future__ import annotations

import argparse
from collections import defaultdict

from engine.loader import LoadReport, load_trips

DEFAULT_PATH = "data/app_data_wave2_raw.xlsx"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", nargs="?", default=DEFAULT_PATH)
    ap.add_argument("--limit", type=int, default=10, help="sample trips to print")
    args = ap.parse_args()

    report = LoadReport()
    trips = load_trips(args.path, report)

    persons = {t.person for t in trips}
    by_day: dict[tuple, int] = defaultdict(int)
    for t in trips:
        by_day[(t.person, t.start_ts.date())] += 1

    print(f"file:          {args.path}")
    print(f"rows:          {report.total_rows}")
    print(f"loaded trips:  {report.loaded}")
    print(f"skipped rows:  {report.skipped}")
    print(f"persons:       {len(persons)}")
    print(f"person-days:   {len(by_day)}")
    if by_day:
        sizes = sorted(by_day.values())
        print(f"trips/day:     min {sizes[0]}  median {sizes[len(sizes)//2]}  max {sizes[-1]}")

    print(f"\nsample of {min(args.limit, len(trips))} trips:")
    for t in trips[: args.limit]:
        dur_min = (t.end_ts - t.start_ts).total_seconds() / 60.0
        print(
            f"  row {t.raw_row:>4}  {t.person:<14}  {t.mode:<14}  "
            f"{t.start_ts:%Y-%m-%d %H:%M}->{t.end_ts:%H:%M}  "
            f"{dur_min:5.1f}min  route={len(t.route):>3}pts  "
            f"{'manual' if t.purpose else 'auto'}"
        )


if __name__ == "__main__":
    main()
