"""The locked classification rule.

Responsibility: per-consecutive-pair classification (gate / residual / symmetric
mode-change guard / buffer+20min) and the per-trip zero_move rule, emitting
`Flag`s. Pure; depends on `velocity`, `geo`, `models`, `params`.

Units are human throughout (metres / km/h / minutes) to match the locked rule and
the reviewer's display. The only mixed-unit arithmetic — turning a distance and a
speed into a time — is confined to the two tiny helpers below, each unit-tested,
so the rule logic itself carries no loose conversion factors.

This module implements 2a (pair geometry + evidence). The decision ladder and
zero_move land in 2b.
"""

from __future__ import annotations

from engine.geo import haversine_m
from engine.models import PairEvidence, Trip
from engine.params import DEFAULT, Params
from engine.velocity import pair_speeds


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
