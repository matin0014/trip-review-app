"""The locked classification rule.

Responsibility: per-consecutive-pair classification (gate / residual / symmetric
mode-change guard / buffer+20min) and the per-trip zero_move rule, emitting
`Flag`s. Pure; depends on `velocity`, `geo`, `models`, `params`.

Units are human throughout (metres / km/h / minutes) to match the locked rule and
the reviewer's display. The only mixed-unit arithmetic — turning a distance and a
speed into a time — is confined to the two tiny helpers below, each unit-tested,
so the rule logic itself carries no loose conversion factors.

This module implements 2a (pair geometry + evidence) and 2b (the decision ladder
+ zero_move).
"""

from __future__ import annotations

from engine.geo import haversine_m
from engine.models import Flag, PairEvidence, Trip, TripEvidence
from engine.params import DEFAULT, Params
from engine.velocity import pair_speeds, trip_moving_speed

# Implied-speed ceiling for the interim pathological guard: a pair whose gap could
# only be crossed faster than this is a duplicate/overlap/glitch, left untouched.
PATHOLOGICAL_KMH = 120.0


def expected_time_min(gap_m: float, v_kmh: float) -> float:
    """Time (minutes) to cover `gap_m` metres at `v_kmh` km/h.

    The one place metres and km/h meet: ``(gap_m/1000) km / v_kmh * 60``.
    Caller guarantees `v_kmh > 0` (the v_A / v_B guards handle zero upstream).
    """
    return (gap_m / 1000.0) / v_kmh * 60.0


def pair_geometry(prev: Trip, cur: Trip, params: Params = DEFAULT) -> PairEvidence:
    """Compute the locked-rule numbers for the consecutive pair (prev -> cur).

    `t`/`buffer`/`residual` need `v_a > 0`, and `slack_b` needs `v_b > 0`; when a
    speed is zero the corresponding fields are left None (the 2b guards turn such
    pairs into "keep, no flag"). `residual_min` is SIGNED — never abs().
    """
    gap_distance_m = haversine_m(
        prev.end_lat, prev.end_lon, cur.start_lat, cur.start_lon
    )
    gap_time_min = (cur.start_ts - prev.end_ts).total_seconds() / 60.0
    v_a, v_b = pair_speeds(prev, cur, params)

    t_min = buffer_min = residual_min = None
    if v_a > 0:
        t_min = expected_time_min(gap_distance_m, v_a)
        buffer_min = params.buffer_frac * t_min + params.buffer_const_min
        residual_min = gap_time_min - t_min  # SIGNED

    slack_b_min = None
    if v_b > 0:
        slack_b_min = gap_time_min - expected_time_min(gap_distance_m, v_b)

    return PairEvidence(
        gap_distance_m=gap_distance_m,
        gap_time_min=gap_time_min,
        v_a_kmh=v_a,
        v_b_kmh=v_b,
        t_min=t_min,
        buffer_min=buffer_min,
        residual_min=residual_min,
        slack_b_min=slack_b_min,
    )


def classify_pair(prev: Trip, cur: Trip, params: Params = DEFAULT) -> "Flag | None":
    """Classify a consecutive pair via the locked ladder; None = keep (no flag).

    Order: interim guards (pathological pair, then either leg speed undefined),
    the 200 m contiguity gate, then the residual ladder with the symmetric
    mode-change overturn inside the fragment branch.
    """
    ev = pair_geometry(prev, cur, params)

    # Interim pathological guard (deferred proper handling): negative gap time,
    # or an implied speed above the ceiling => duplicate/overlap/glitch -> keep.
    if ev.gap_time_min <= 0:
        return None
    if ev.gap_time_min < expected_time_min(ev.gap_distance_m, PATHOLOGICAL_KMH):
        return None

    # Either leg's speed must be positive: v_A anchors the expected time `t`, and
    # a degenerate v_B (a stationary cur = zero_move noise) would spuriously read
    # as a speed-class crossing in the mode-change guard. Either undefined leg
    # speed -> keep; the per-trip zero_move handles the stationary leg.
    if ev.v_a_kmh <= 0 or ev.v_b_kmh <= 0:
        return None

    # Contiguous: the chain is intact, nothing to propose.
    if ev.gap_distance_m <= params.gate_m:
        if ev.gap_time_min < params.short_gap_fragment_min:
            return Flag(
                flag_type="fragment",
                left=(prev.raw_row,),
                right=(cur.raw_row,),
                group=min(prev.raw_row, cur.raw_row),
                evidence=ev,
            )
        return None

    # v_A > 0 guaranteed these are set.
    assert ev.residual_min is not None and ev.buffer_min is not None

    if ev.residual_min < ev.buffer_min:  # travel-shaped at v_A
        if _mode_mismatch(ev.v_a_kmh, ev.v_b_kmh, params):
            flag_type, group = "mode_change", None  # different speed classes -> review
        else:
            flag_type, group = "fragment", min(prev.raw_row, cur.raw_row)  # merge
    elif ev.residual_min <= ev.buffer_min + params.missing_band_min:
        flag_type, group = "missing_trip", None
    else:
        flag_type, group = "missing_activity", None

    return Flag(
        flag_type=flag_type,
        left=(prev.raw_row,),
        right=(cur.raw_row,),
        group=group,
        evidence=ev,
    )


def classify_trip_zero_move(trip: Trip, params: Params = DEFAULT) -> "Flag | None":
    """Per-trip zero_move: net displacement < 200 m AND median moving <= 10 km/h."""
    displacement_m = haversine_m(
        trip.start_lat, trip.start_lon, trip.end_lat, trip.end_lon
    )
    speed_kmh = trip_moving_speed(trip, params)
    if displacement_m < params.zero_move_disp_m and speed_kmh <= params.zero_move_speed_kmh:
        return Flag(
            flag_type="zero_move",
            trip=trip.raw_row,
            evidence=TripEvidence(displacement_m=displacement_m, median_speed_kmh=speed_kmh),
        )
    return None


def _mode_mismatch(v_a_kmh: float, v_b_kmh: float, params: Params) -> bool:
    """Symmetric non-vehicular<->vehicular class crossing in either direction."""
    low, high = params.mode_low_kmh, params.mode_high_kmh
    return (v_a_kmh <= low and v_b_kmh >= high) or (v_a_kmh >= high and v_b_kmh <= low)


def group_person_days(trips: list) -> dict:
    """Group trips into person-days keyed by (person, start date), each sorted by
    start time. The classifier's unit of work — pairs never cross a boundary."""
    days: dict = {}
    for t in trips:
        days.setdefault((t.person, t.start_ts.date()), []).append(t)
    for day in days.values():
        day.sort(key=lambda t: t.start_ts)
    return days


def classify_day(day_trips: list, params: Params = DEFAULT) -> list:
    """All flags for one person-day: consecutive-pair flags + per-trip zero_move."""
    flags: list = []
    for prev, cur in zip(day_trips, day_trips[1:]):
        pair_flag = classify_pair(prev, cur, params)
        if pair_flag is not None:
            flags.append(pair_flag)
    for trip in day_trips:
        zm = classify_trip_zero_move(trip, params)
        if zm is not None:
            flags.append(zm)
    return flags


def classify_all(trips: list, params: Params = DEFAULT) -> list:
    """Flags for the whole dataset, classified independently per person-day."""
    flags: list = []
    for day in group_person_days(trips).values():
        flags.extend(classify_day(day, params))
    return flags
