"""Decision log replay -> live state (the fixpoint).

Responsibility: turn the append-only event log (decision / manual_edit /
manual_add / revert / redo) into the `accepted_set`, honoring last-event-wins and
the revert/redo stacks, then (later) drive the engine's fixpoint recompute per
person-day. Pure given the events.

Step 4 part 2b implements `replay(events) -> accepted_set` for flag decisions
plus the global revert/redo (Rewind). `manual_*` events and the `fixpoint(...)`
cascade recompute land in the next step.
"""

from __future__ import annotations

from engine.classify import classify_day
from engine.ids import with_ids
from engine.params import DEFAULT
from engine.proposals import project

# Merge resolutions that combine two trips when applied: fragment AND an accepted
# mode_change (its resolution is Merge). zero_move removes, missing_trip stubs.
FIXPOINT_MERGE_TYPES = ("fragment", "mode_change")

# Hard ceiling on cascade rounds — convergence is normally 2-3; this only guards
# against a pathological non-terminating recompute.
_MAX_ROUNDS = 50

# A flag's resolution either APPLIES its change or WAIVES it. Per the locked
# design: fragment/missing_trip/zero_move accept; missing_activity is "reviewed";
# mode_change is "merge"; all of those apply. "waive" leaves the trip untouched.
APPLY_RESOLUTIONS = frozenset({"accept", "merge", "reviewed"})
WAIVE_RESOLUTIONS = frozenset({"waive"})
VALID_RESOLUTIONS = APPLY_RESOLUTIONS | WAIVE_RESOLUTIONS


def resolutions(events: list) -> dict:
    """Effective resolution per decided flag id, after undo/redo.

    Decisions are last-event-wins per flag id; `revert` / `redo` form a global
    LIFO over the decision events (a fresh decision clears the redo stack, so you
    cannot redo past a new action). Flags never decided (or whose decision was
    reverted) are simply absent from the returned map.
    """
    active: list = []  # decisions currently in effect, in chronological order
    redo: list = []    # decisions popped by revert, available to redo
    for e in events:
        kind = e.get("type")
        if kind == "decision":
            active.append(e)
            redo.clear()
        elif kind == "revert":
            if active:
                redo.append(active.pop())
        elif kind == "redo":
            if redo:
                active.append(redo.pop())
        # other event types (manual_*) are ignored here until the next step

    effective: dict = {}
    for e in active:
        effective[e["flag_id"]] = e["resolution"]  # last active decision wins
    return effective


def replay(events: list) -> set:
    """The set of currently-APPLIED flag ids (effective resolution applies the
    change: accept / merge / reviewed). Waived or undecided flags are absent."""
    return {fid for fid, res in resolutions(events).items() if res in APPLY_RESOLUTIONS}


def fixpoint(day_trips: list, accepted_ids: set, params=DEFAULT) -> tuple:
    """Live state for ONE person-day: apply accepted flags and re-classify until
    the trip set stops changing.

    Returns ``(live_trips, surfaced_flags)``: the converged projected trips, and
    every flag surfaced across the rounds (baseline + cascade-born), each with a
    stable content id. Applying an accepted change (merge/remove/stub) shifts the
    geometry, so the next round's `classify_day` can birth NEW flags on the
    freshly-adjacent neighbours — those are surfaced as PENDING (they are only
    applied if their id is itself in `accepted_ids`). The cascade never crosses
    the person-day boundary because the whole loop runs on one day's trips.
    """
    trips = list(day_trips)
    surfaced: dict = {}
    for _ in range(_MAX_ROUNDS):
        flags = with_ids(classify_day(trips, params))
        for f in flags:
            surfaced.setdefault(f.flag_id, f)  # first sighting wins (stable id)
        accepted_here = [f for f in flags if f.flag_id in accepted_ids]
        projected = project(trips, accepted_here, merge_types=FIXPOINT_MERGE_TYPES)
        if _signature(projected) == _signature(trips):
            break  # no accepted change left to apply -> fixpoint reached
        trips = projected
    return trips, list(surfaced.values())


def _signature(trips: list) -> tuple:
    """An order-independent identity of a trip set, for convergence detection."""
    return tuple(sorted(
        (t.raw_row, t.start_ts, t.end_ts,
         round(t.start_lat, 6), round(t.start_lon, 6),
         round(t.end_lat, 6), round(t.end_lon, 6))
        for t in trips
    ))
