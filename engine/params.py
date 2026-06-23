"""Engine thresholds — the single source of every tunable constant.

This module holds ONLY tunable parameters of the locked classification rule
(see the plan / `project_final_classification_rule` memory). It has no logic and
no dependencies, so every other engine module can import it freely.

Physical constants (e.g. Earth's radius) live with the code that uses them
(see `geo.py`), not here — this file is for *decisions*, not for physics.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Params:
    """Immutable bundle of engine thresholds.

    A frozen dataclass (not loose module globals) so a caller can construct an
    alternative parameter set for experiments/tests without mutating shared
    state. `DEFAULT` below is the production set.
    """

    # --- velocity / glitch handling ---
    glitch_cap_kmh: float = 150.0
    """Drop route segments faster than this (data-locked 2026-06-16). Keeps real
    highway, removes GPS glitches and non-ground travel."""

    v_move_min_kmh: float = 2.0
    """Floor for a segment to count as 'moving' when taking the median moving
    speed. DATA-LOCKED 2026-06-19: the 236k-segment speed histogram has a
    stationary-jitter spike at 0-1 km/h (7.0%), a trough at 1-2 km/h (4.4% — the
    minimum), then the walking mass rises (peak 4-5 km/h, 14.4%). 2.0 sits in the
    trough, dropping jitter without eating real slow walking (1.5-2.5 all viable)."""

    dedup_dt_s: float = 1.0
    """Collapse consecutive route points closer together in time than this
    (sub-second breadcrumb bursts)."""

    # --- pair classification (gap geometry) ---
    gate_m: float = 200.0
    """gap_distance <= this => contiguous (chain intact), no flag (unless gap time is short)."""

    short_gap_fragment_min: float = 3.0
    """If gap_distance <= gate_m AND gap_time < this, flag as a fragment instead of contiguous."""

    buffer_frac: float = 0.10
    """buffer = buffer_frac * t_expected + buffer_const_min."""

    buffer_const_min: float = 3.0
    """The `D` constant (minutes) in the buffer, and the `slack_B >= D` confirmer."""

    missing_band_min: float = 20.0
    """Width (minutes) of the missing-trip band above buffer; beyond it =
    missing activity. REVIEWED 2026-06-19: the residual-minus-buffer distribution
    over the 568 gaps past the fragment branch is smooth and long-tailed
    (p25=8, p50=45, p90=311 min) with NO natural break to snap to. 20 retained as
    a judgment floor ("long enough that an activity happened, not just travel");
    it splits the region 39% missing_trip / 61% missing_activity."""

    # --- mode-change guard (symmetric, locked 2026-06-17; bands data-validated 2026-06-19) ---
    mode_low_kmh: float = 12.0
    """Non-vehicular ceiling: a leg at or below this is the 'slow' class.
    DATA-VALIDATED 2026-06-19: per-mode median moving speeds cluster cleanly —
    walk/run ~5 km/h sit well below 12 (the classifier keys on measured speed,
    NOT the mode label, which is unreliable: 7-18% of 'Walk' trips run fast)."""

    mode_high_kmh: float = 25.0
    """Vehicular floor: a leg at or above this is the 'fast' class. The 12-25
    dead zone is deliberately ambiguous so the guard does not fire there.
    DATA-VALIDATED 2026-06-19: vehicle modes cluster above 25 (Auto Driver 27,
    Taxi 27, SkyTrain 40) while Bike (~15) sits squarely in the dead zone — the
    intended behaviour, so bike never trips a mode-change against either class."""

    # --- zero-move (per-trip) ---
    zero_move_disp_m: float = 200.0
    """Trip net displacement below this is a 'went nowhere' candidate."""

    zero_move_speed_kmh: float = 10.0
    """...AND median moving speed at/below this => stationary jitter (zero_move)."""


DEFAULT = Params()
"""The production parameter set."""
