"""The nine network indicators, each computed BEFORE and AFTER cleaning.

Every function takes the shared `Dataset` and returns a plain JSON-serialisable
dict (counts / percentages / series) that `render` hands to the charts. No plotting
here, only the numbers, so each metric is unit-testable in isolation. Structure
mirrors the reference dashboard; the new twist is that each indicator carries a
``raw`` and a ``cleaned`` series (before vs after), plus a per-region breakdown.

Indicators:
  1. trips_per_person_per_day     target ~4; overall + weekday + per-weekday, by region
  2. mode_share                   trips per mode (top modes), by region
  3. trip_start_times_by_hour     count/% by hour, weekday / Saturday / Sunday, by region
  4. purpose_and_mode_coverage    % of trips with a mode and with a purpose, by region
  5. trip_duration_distribution   0-10 / 10-30 / 30-60 / 1-2h / >2h buckets + avg, by region
  6. activity_duration_by_purpose avg activity (between-trip) hours per purpose, by region
  7. weekday_coverage             users with vs without data for each day of week
  8. completion_rate_by_days      cumulative (X+ days) and exact day completion, by region
  9. purpose_distribution_by_hour purpose mix by hour, weekday / Saturday / Sunday
"""

from __future__ import annotations

from .pipeline import Dataset

# Fixed bucket edges (minutes) for indicator 5, matching the reference dashboard.
DURATION_BUCKETS = ("0-10 min", "10-30 min", "30-60 min", "1-2 hrs", ">2 hrs")


def trips_per_person_per_day(ds: Dataset) -> dict:
    """Indicator 1. Average trips per person per day: overall, weekday average, and
    one value per weekday, raw vs cleaned, with a per-region split."""
    raise NotImplementedError


def mode_share(ds: Dataset) -> dict:
    """Indicator 2. Trip count per travel mode (ordered), raw vs cleaned, by region."""
    raise NotImplementedError


def trip_start_times_by_hour(ds: Dataset) -> dict:
    """Indicator 3. Trip starts per hour-of-day (count and %), split weekday /
    Saturday / Sunday, raw vs cleaned, with a per-region overlay."""
    raise NotImplementedError


def purpose_and_mode_coverage(ds: Dataset) -> dict:
    """Indicator 4. Share of trips carrying a mode and carrying a purpose, raw vs
    cleaned, by region."""
    raise NotImplementedError


def trip_duration_distribution(ds: Dataset) -> dict:
    """Indicator 5. Trip counts across `DURATION_BUCKETS` plus overall average
    duration, weekday / Saturday / Sunday, raw vs cleaned, by region."""
    raise NotImplementedError


def activity_duration_by_purpose(ds: Dataset) -> dict:
    """Indicator 6. Average activity duration (gap between a trip's end and the next
    trip's start within a person-day) in hours per purpose, raw vs cleaned, by
    region. Shares the activity definition with distributions.py."""
    raise NotImplementedError


def weekday_coverage(ds: Dataset) -> dict:
    """Indicator 7. For each day of the week, users WITH vs WITHOUT reported data
    (raw vs cleaned)."""
    raise NotImplementedError


def completion_rate_by_days(ds: Dataset) -> dict:
    """Indicator 8. Cumulative completion (share of users with X+ days of data) and
    exact-day completion, raw vs cleaned, by region."""
    raise NotImplementedError


def purpose_distribution_by_hour(ds: Dataset) -> dict:
    """Indicator 9. Trip-purpose mix by hour-of-day (count and %), weekday /
    Saturday / Sunday, raw vs cleaned."""
    raise NotImplementedError


# Registry: (id, title, subtitle, fn). `build`/`render` iterate this so adding an
# indicator is one entry + one function.
INDICATORS = (
    ("trips_per_day", "Trips per Person per Day", "Target: ~4 trips/day", trips_per_person_per_day),
    ("mode_share", "Mode Share", "Auto-driver should have highest share", mode_share),
    ("start_times", "Trip Start Time by Hour", "Weekday: morning (6-9) and afternoon (3-5) peaks", trip_start_times_by_hour),
    ("coverage", "Trip Purpose & Mode Coverage", "All trips should have purposes and modes", purpose_and_mode_coverage),
    ("duration", "Trip Duration Distribution", "10-30 min should have the highest share", trip_duration_distribution),
    ("activity_duration", "Activity Duration by Purpose", "Work/School 4-8 hrs; Shopping/Recreation 0-2 hrs", activity_duration_by_purpose),
    ("weekday_coverage", "Weekday Coverage Distribution", "Users reporting data for each day of the week", weekday_coverage),
    ("completion", "User Completion Rate by Days", "Share of users completing X days or more", completion_rate_by_days),
    ("purpose_by_hour", "Trip Purpose Distribution by Hour", "How trip purposes shift through the day", purpose_distribution_by_hour),
)


def compute_all(ds: Dataset) -> dict:
    """Run every registered indicator and return ``{id: series-dict}`` for render."""
    raise NotImplementedError
