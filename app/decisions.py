"""Decision log replay -> live state (the fixpoint).

Responsibility: turn the append-only event log (decision / manual_edit /
manual_add / revert / redo) into the `accepted_set`, honoring last-event-wins and
the revert/redo stacks, then drive the engine's fixpoint recompute per person-day.
Pure given the events.
"""

from __future__ import annotations

from engine.classify import classify_day
from engine.ids import with_ids
from engine.params import DEFAULT
from engine.proposals import apply_manual_ops, project

# Merge resolutions that combine two trips when applied: fragment AND an accepted
# mode_change (its resolution is Merge). zero_move removes, missing_trip stubs.
FIXPOINT_MERGE_TYPES = ("fragment", "mode_change")

# Hard ceiling on cascade rounds — convergence is normally 2-3; this only guards
# against a pathological non-terminating recompute.
_MAX_ROUNDS = 50

# A flag's resolution either APPLIES its change or WAIVES it.
APPLY_RESOLUTIONS = frozenset({"accept", "merge", "reviewed"})
WAIVE_RESOLUTIONS = frozenset({"waive"})
# "clear" un-decides a flag back to pending.
VALID_RESOLUTIONS = APPLY_RESOLUTIONS | WAIVE_RESOLUTIONS | frozenset({"clear"})

# Manual edit event types that participate in the undo/redo LIFO alongside
# flag decisions. Unknown types are ignored (forward-compatibility).
_MANUAL_TYPES = frozenset({
    "manual_delete", "manual_merge", "manual_split", "manual_add", "manual_mode",
    "manual_purpose", "manual_time",
})


def _active_events(events: list) -> list:
    """Shared undo/redo machinery: the events currently in effect, in order.

    ``decision`` and ``manual_*`` events are pushed by default and popped by
    ``revert``/``redo``. ``undo_event`` removes one active event by its stored
    serial number. A fresh decision also clears the redo stack.
    """
    active: list = []
    redo: list = []
    for e in events:
        kind = e.get("type")
        if kind == "decision" or kind in _MANUAL_TYPES:
            active.append(e)
            redo.clear()
        elif kind == "undo_event":
            target = e.get("target_seq")
            active = [a for a in active if a.get("seq") != target]
            redo.clear()
        elif kind == "revert":
            if active:
                redo.append(active.pop())
        elif kind == "redo":
            if redo:
                active.append(redo.pop())
    return active


def resolutions(events: list) -> dict:
    """Effective resolution per decided flag id, after undo/redo.

    Decisions are last-event-wins per flag id; ``revert`` / ``redo`` form a global
    LIFO over the decision events (a fresh decision clears the redo stack, so you
    cannot redo past a new action). Flags never decided (or whose decision was
    reverted) are simply absent from the returned map.
    """
    effective: dict = {}
    for e in _active_events(events):
        if e.get("type") != "decision":
            continue
        if e["resolution"] == "clear":
            effective.pop(e["flag_id"], None)
        else:
            effective[e["flag_id"]] = e["resolution"]
    return effective


def manual_ops(events: list) -> list:
    """Ordered currently-active manual edit ops (after undo/redo).

    Returns only ``manual_*`` events from the effective active stack, in the
    order they were originally applied. The caller filters by ``day_key`` to
    get one day's ops.
    """
    return [e for e in _active_events(events) if e.get("type") in _MANUAL_TYPES]


def replay(events: list) -> set:
    """The set of currently-APPLIED flag ids (effective resolution applies the
    change: accept / merge / reviewed). Waived or undecided flags are absent."""
    return {fid for fid, res in resolutions(events).items() if res in APPLY_RESOLUTIONS}


def fixpoint(day_trips: list, accepted_ids: set, ops: list = None,
             params=DEFAULT) -> tuple:
    """Live state for ONE person-day: apply accepted flags and re-classify until
    the trip set stops changing.

    ``ops`` is the ordered list of currently-active ``manual_*`` events for this
    day. They are applied BEFORE the cascade loop so the engine re-classifies the
    manually-edited trip set. Manual deletes and merges are handled; split/add
    are silently skipped until later steps implement them.

    Returns ``(live_trips, surfaced_flags)``: the converged projected trips, and
    the flags worth showing (pending cascade-born + accepted/consumed originals).
    """
    trips = list(day_trips)

    surfaced: dict = {}
    flags: list = []
    for _ in range(_MAX_ROUNDS):
        flags = with_ids(classify_day(trips, params))
        for f in flags:
            surfaced.setdefault(f.flag_id, f)
        accepted_here = [f for f in flags if f.flag_id in accepted_ids]
        projected = project(trips, accepted_here, merge_types=FIXPOINT_MERGE_TYPES,
                            merge_wins=True)
        if ops:
            projected = apply_manual_ops(projected, ops)
        if _signature(projected) == _signature(trips):
            break
        trips = projected

    final_ids = {f.flag_id for f in flags}
    consumed = [f for f in surfaced.values()
                if f.flag_id in accepted_ids and f.flag_id not in final_ids]
    return trips, flags + consumed


def _signature(trips: list) -> tuple:
    """An order-independent identity of a trip set, for convergence detection."""
    return tuple(sorted(
        (t.raw_row, t.start_ts, t.end_ts,
         round(t.start_lat, 6), round(t.start_lon, 6),
         round(t.end_lat, 6), round(t.end_lon, 6),
         t.mode, t.purpose)
        for t in trips
    ))
