"""Speed computation.

Responsibility: per-segment speeds from a route, the glitch-filtered median
moving speed (`v_A` / `v_B` / per-trip), and the displacement/duration fallback
for sparse or all-glitch routes. Pure; depends on `geo`, `models`, `params`.

The leg speed is the **median of MOVING segments** — those between
`v_move_min_kmh` and the `glitch_cap_kmh` (150). When no segment falls in that
band we look at *why* before deciding:

* **Stationary jitter** — there are real (sub-150) segments but all are below the
  moving floor. The phone never actually travelled, so speed is ~0. "No moving
  segment" IS the zero_move signature, not a failure. (A round trip has ~0 net
  displacement but real moving segments, so it never lands here — it is spared.)
* **No usable route** — the route is too sparse to form a segment, or every
  segment was dropped as a >150 glitch. Here we fall back to the trip's declared
  displacement / duration, which is correct for both glitch-corrupted normal
  trips (~sane km/h) and genuine unlabelled flights (~hundreds of km/h).
"""

from __future__ import annotations

from statistics import median

from engine.geo import haversine_m, haversine_points
from engine.models import Trip
from engine.params import DEFAULT, Params


def segment_speeds(route: list) -> list[float]:
    """Speed (km/h) of each consecutive route segment with a positive `dt`.

    No filtering applied here — the caller decides which to keep. Sub-second
    bursts are already collapsed by `routes.parse_route`, but a non-positive
    `dt` is still skipped defensively (it would divide by zero).
    """
    speeds: list[float] = []
    for a, b in zip(route, route[1:]):
        dt_s = (b.ts - a.ts).total_seconds()
        if dt_s > 0:
            speeds.append(haversine_points(a, b) / dt_s * 3.6)
    return speeds


def trip_moving_speed(trip: Trip, params: Params = DEFAULT) -> float:
    """Median moving speed (km/h) of a trip's leg — the engine's one speed.

    Same definition used for `v_A`, `v_B`, and the zero_move check.
    """
    speeds = segment_speeds(trip.route)
    moving = [s for s in speeds if params.v_move_min_kmh <= s <= params.glitch_cap_kmh]
    if moving:
        return median(moving)

    # No moving segment: is there any real (non-glitch) movement at all?
    non_glitch = [s for s in speeds if s <= params.glitch_cap_kmh]
    if non_glitch:
        return 0.0  # real segments, all sub-floor -> stationary (zero_move signature)
    return _fallback_speed(trip)  # sparse or all-glitch -> declared displacement/duration


def pair_speeds(
    prev: Trip, cur: Trip, params: Params = DEFAULT
) -> "tuple[float, float]":
    """`(v_A, v_B)` for a consecutive pair: prev leg's and cur leg's moving speed."""
    return trip_moving_speed(prev, params), trip_moving_speed(cur, params)


def _fallback_speed(trip: Trip) -> float:
    """Declared displacement / duration (km/h); 0 for a non-positive duration."""
    duration_s = (trip.end_ts - trip.start_ts).total_seconds()
    if duration_s <= 0:
        return 0.0
    displacement_m = haversine_m(
        trip.start_lat, trip.start_lon, trip.end_lat, trip.end_lon
    )
    return displacement_m / duration_s * 3.6
