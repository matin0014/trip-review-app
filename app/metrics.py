"""Precision metrics.

Responsibility: TP (accept) / FP (waive) counts -> precision, for the four
scored detectors (zero_move, fragment, missing_trip, mode_change via its
apply-resolution "merge"), bucketing `origin=cascade` separately from
`origin=ingest`. No recall/TN/FN.
"""

from __future__ import annotations

from collections import defaultdict

from app.decisions import APPLY_RESOLUTIONS, WAIVE_RESOLUTIONS, fixpoint, manual_ops, resolutions
from engine.classify import group_person_days
from engine.loader import load_trips
from engine.params import DEFAULT

# The detectors whose accept/waive (or merge/waive for mode_change) carries a
# reliable TP/FP signal. missing_activity's "reviewed" resolution is an
# acknowledgment, not a judgment on whether the flag was right to fire, so it
# is deliberately excluded.
SCORED_TYPES = frozenset({"zero_move", "fragment", "missing_trip", "mode_change"})


def precision_table(raw_path: str, events: list, params=DEFAULT) -> dict:
    """``{(flag_type, origin): {"tp": n, "fp": n, "precision": float | None}}``

    Replays every person-day's fixpoint to see each scored flag's resolution,
    tallying accept/merge as TP and waive as FP. Flags never decided (pending)
    don't count either way. `precision` is ``None`` when tp+fp == 0.
    """
    res_map = resolutions(events)
    accepted = {fid for fid, res in res_map.items() if res in APPLY_RESOLUTIONS}
    ops = manual_ops(events)

    counts: dict = defaultdict(lambda: {"tp": 0, "fp": 0})
    for (person, date), day_trips in group_person_days(load_trips(raw_path)).items():
        day_key = f"{person}|{date}"
        day_ops = [op for op in ops if op.get("day_key") == day_key]
        _live, surfaced = fixpoint(day_trips, accepted, ops=day_ops, params=params)
        for f in surfaced:
            if f.flag_type not in SCORED_TYPES:
                continue
            res = res_map.get(f.flag_id)
            key = (f.flag_type, f.origin)
            if res in APPLY_RESOLUTIONS:
                counts[key]["tp"] += 1
            elif res in WAIVE_RESOLUTIONS:
                counts[key]["fp"] += 1

    table = {}
    for key, c in counts.items():
        tp, fp = c["tp"], c["fp"]
        table[key] = {"tp": tp, "fp": fp, "precision": tp / (tp + fp) if (tp + fp) else None}
    return table
