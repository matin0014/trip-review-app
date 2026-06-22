"""Flags -> proposals + cleaned-file projection.

Responsibility: assemble classifier `Flag`s into the first-class Do-file `flags`
+ `rows` collections, and project the cleaned dataset by applying every proposal
(the "all auto-accepted" view). Pure; depends on `classify`/`ids`/`models`.

`apply_all` applies ingest flags ONCE, with NO cascade (live cascade/fixpoint is
Stage 5): fragment merges (transitively unioned per consecutive pair), zero_move
removals, and missing_trip stub inserts. zero_move takes precedence over a merge
that touches the removed trip — a removed trip never drags a neighbor into a
merge (boundary preservation). missing_activity / mode_change change no geometry.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict

from engine.ids import with_ids
from engine.models import Trip


def project(trips: list, flags: list, merge_types: tuple = ("fragment",)) -> list:
    """One apply pass (no cascade): remove zero_move trips -> merge the pairs in
    `merge_types` (transitive union, removed trips excluded) -> insert
    missing_trip stubs. Returns the projected trips sorted by (person, start_ts).

    `flags` is the set to APPLY (every flag for `apply_all`; accepted-only for the
    live fixpoint). `merge_types` lets the caller choose what counts as a merge —
    `apply_all` merges fragments only, while the fixpoint also merges an accepted
    `mode_change` (its resolution is Merge). missing_activity / reviewed change no
    geometry. Stub raw_rows go one below the lowest existing row, so re-projecting
    an already-stubbed trip set never collides (the linchpin for the fixpoint loop).
    """
    by_row = {t.raw_row: t for t in trips}
    removed = {f.trip for f in flags if f.flag_type == "zero_move"}

    roots = _merge_roots(flags, removed, merge_types)
    groups: dict = defaultdict(list)
    for t in trips:
        if t.raw_row in removed:
            continue
        groups[roots.get(t.raw_row, t.raw_row)].append(t)

    out: list = []
    for group in groups.values():
        out.append(group[0] if len(group) == 1 else _merge_group(group))

    next_stub = min([0] + [t.raw_row for t in trips]) - 1
    for f in flags:
        if f.flag_type == "missing_trip":
            a, b = by_row.get(f.left[0]), by_row.get(f.right[0])
            if a is None or b is None or f.left[0] in removed or f.right[0] in removed:
                continue
            out.append(_make_stub(a, b, next_stub))
            next_stub -= 1

    out.sort(key=lambda t: (str(t.person), t.start_ts))
    return out


def apply_all(trips: list, flags: list) -> list:
    """Project the cleaned trip set with every proposal applied once (no cascade).

    The "all auto-accepted" view used by Stage 2 / the distribution charts — a thin
    wrapper over `project` (fragment merges only, matching the locked Stage 2e
    behaviour)."""
    return project(trips, flags)


def build_dofile(trips: list, flags: list) -> dict:
    """The in-memory Do-file: `flags{}` keyed by flag_id + `rows{}` per raw row.

    Each row caches `is_manual` / `duration_min` and lists the `flag_ids` that
    touch it (a row can belong to two flags). Serialization to proposals.json is
    the app's job; this is the pure structure.
    """
    flags = with_ids(flags)
    rows: dict = {}
    for t in trips:
        rows[t.raw_row] = {
            "raw_row": t.raw_row,
            "person": t.person,
            "mode": t.mode,
            "purpose": t.purpose,
            "is_manual": bool(t.purpose),
            "duration_min": (t.end_ts - t.start_ts).total_seconds() / 60.0,
            "flag_ids": [],
        }

    flags_coll: dict = {}
    for f in flags:
        flags_coll[f.flag_id] = {
            "flag_id": f.flag_id,
            "flag_type": f.flag_type,
            "left": list(f.left),
            "right": list(f.right),
            "trip": f.trip,
            "group": f.group,
            "origin": f.origin,
            "evidence": asdict(f.evidence) if f.evidence is not None else None,
        }
        touched = list(f.left) + list(f.right)
        if f.trip is not None:
            touched.append(f.trip)
        for row in touched:
            if row in rows:
                rows[row]["flag_ids"].append(f.flag_id)

    return {"flags": flags_coll, "rows": rows}


def _merge_roots(flags: list, removed: set, merge_types: tuple = ("fragment",)) -> dict:
    """Union-find over merge-type pairs (skipping removed trips); maps each member
    raw_row to its group root (the lowest raw row in the group)."""
    parent: dict = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)  # root = lowest raw row

    for f in flags:
        if f.flag_type in merge_types:
            a, b = f.left[0], f.right[0]
            if a in removed or b in removed:
                continue
            union(a, b)
    return {row: find(row) for row in parent}


def _merge_group(group: list) -> Trip:
    """Merge a fragment group into one trip: A's start, B's end, concatenated
    routes in timestamp order; destination piece keeps mode/purpose."""
    g = sorted(group, key=lambda t: t.start_ts)
    first, last = g[0], g[-1]
    route = sorted((p for t in g for p in t.route), key=lambda p: p.ts)
    return Trip(
        raw_row=min(t.raw_row for t in g),
        person=first.person,
        mode=last.mode,
        purpose=last.purpose,
        start_ts=first.start_ts,
        end_ts=last.end_ts,
        start_lat=first.start_lat,
        start_lon=first.start_lon,
        end_lat=last.end_lat,
        end_lon=last.end_lon,
        route=route,
    )


def _make_stub(a: Trip, b: Trip, row: int) -> Trip:
    """A synthesized missing-trip stub bridging A.end -> B.start (empty route)."""
    return Trip(
        raw_row=row,
        person=a.person,
        mode="Unknown",
        purpose="",
        start_ts=a.end_ts,
        end_ts=b.start_ts,
        start_lat=a.end_lat,
        start_lon=a.end_lon,
        end_lat=b.start_lat,
        end_lon=b.start_lon,
        route=[],
    )
