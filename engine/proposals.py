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
from dataclasses import asdict, replace
from datetime import datetime

from engine.ids import with_ids
from engine.models import RoutePoint, Trip
from engine.timeparse import parse_ts

# Stable, position-derived raw_rows for synthesized manual trips. A split's B-piece
# is keyed off its origin row; an added trip is keyed off its per-day add index.
# Both are negative and well below any ingest/stub row so they never collide, and
# stable so the fixpoint loop (which re-applies ops each round) is idempotent.
_SPLIT_B_OFFSET = 1_000_000
_ADD_BASE = 2_000_000


def split_b_row(orig_row: int) -> int:
    """The B-piece raw_row for a split of `orig_row`."""
    return -(orig_row + _SPLIT_B_OFFSET)


def add_row(n: int) -> int:
    """The raw_row for the n-th (1-based) manual add within a day.

    `n` MUST be a stable identity assigned once when the add is first created
    (the event's ``add_seq``, a per-day counter of every add ever logged,
    reverted or not) -- never recomputed from how many adds are CURRENTLY
    active. Recomputing from position let a later add silently inherit an
    earlier (reverted) add's raw_row, and with it any `manual_time`/`mode`/
    `purpose` edits still active against that row -- a real bug, fixed
    2026-07-08."""
    return -(_ADD_BASE + n)


def is_add_row(raw_row: int) -> bool:
    """True when `raw_row` belongs to a manual add (its route is a SYNTHETIC OSM
    path with evenly-spread timestamps, not real GPS). Adds sit below `-_ADD_BASE`;
    split B-pieces (`> -_ADD_BASE`) and ingest/stub rows do not qualify."""
    return raw_row < -_ADD_BASE


def project(trips: list, flags: list, merge_types: tuple = ("fragment",),
            merge_wins: bool = False) -> list:
    """One apply pass (no cascade): remove zero_move trips -> merge the pairs in
    `merge_types` (transitive union, removed trips excluded) -> insert
    missing_trip stubs. Returns the projected trips sorted by (person, start_ts).

    `flags` is the set to APPLY (every flag for `apply_all`; accepted-only for the
    live fixpoint). `merge_types` lets the caller choose what counts as a merge —
    `apply_all` merges fragments only, while the fixpoint also merges an accepted
    `mode_change` (its resolution is Merge). missing_activity / reviewed change no
    geometry. Stub raw_rows go one below the lowest existing row, so re-projecting
    an already-stubbed trip set never collides (the linchpin for the fixpoint loop).

    `merge_wins` flips the zero_move-vs-merge precedence. By default zero_move
    removal wins (Stage-2 boundary preservation: junk never drags a neighbour into
    a merge). In the LIVE review path it is True: a trip that is a member of an
    accepted merge is NOT removed by its own zero_move — the human's "these pieces
    are one drive" dominates, the merge reconstitutes it, and the fixpoint then
    re-classifies the merged trip (re-flagging zero_move only if it STILL barely
    moved). This makes a fragment+zero_move pair resolve correctly in any order.
    """
    by_row = {t.raw_row: t for t in trips}
    removed = {f.trip for f in flags if f.flag_type == "zero_move"}
    if merge_wins:
        merge_members = {row for f in flags if f.flag_type in merge_types
                         for row in (f.left[0], f.right[0])}
        removed -= merge_members  # an accepted merge rescues its pieces from removal

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


def apply_manual_ops(trips: list, ops: list) -> list:
    """Apply manual edit operations in event order to a trip list.

    Handles ``manual_delete`` (drop the trip), ``manual_merge`` (combine N into
    one using the same _merge_group logic as fragment merges), ``manual_split``
    (divide one trip's route at a timestamp into A + B), ``manual_add`` (drop a
    hand-placed trip into the day), ``manual_mode`` (relabel one trip's travel
    mode; pure label, since the engine classifies on measured speed),
    ``manual_purpose`` (relabel one trip's purpose; pure label too), and
    ``manual_time`` (re-stamp one trip's start/end instants; unlike mode this
    feeds the cascade, since neighbour gaps depend on times), and
    ``manual_reposition`` (move one trip's start or end point; the op carries the
    COMPLETE final route -- kept breadcrumbs + a fresh routed patch to the new
    point, timestamps and all -- precomputed ONCE server-side at request time, so
    applying it here is a plain overwrite, not an incremental splice; see
    `app.server.api_manual_reposition` for how that value is built). Unknown op
    types are silently skipped. ``manual_add``'s raw_row uses the op's own
    ``add_seq`` (a stable per-day id assigned once at creation -- see `add_row`),
    falling back to positional numbering only for legacy events logged before that
    field existed.

    Every case is IDEMPOTENT: the live fixpoint re-applies the same op list on each
    cascade round, so split/add detect their already-materialised piece (by its
    stable negative raw_row) and no-op the second time through.
    """
    add_n = 0
    for op in ops:
        kind = op.get("type")
        if kind == "manual_delete":
            trips = [t for t in trips if t.raw_row != op["raw_row"]]
        elif kind == "manual_merge":
            raw_rows = set(op["raw_rows"])
            to_merge = [t for t in trips if t.raw_row in raw_rows]
            rest = [t for t in trips if t.raw_row not in raw_rows]
            if len(to_merge) >= 2:
                trips = sorted(rest + [_merge_group(to_merge)],
                               key=lambda t: (str(t.person), t.start_ts))
        elif kind == "manual_split":
            trips = _apply_split(trips, op)
        elif kind == "manual_add":
            add_n += 1  # legacy fallback only, for events logged before add_seq existed
            trips = _apply_add(trips, op, op.get("add_seq", add_n))
        elif kind == "manual_mode":
            row, mode = op["raw_row"], op["mode"]
            trips = [replace(t, mode=mode) if t.raw_row == row else t for t in trips]
        elif kind == "manual_purpose":
            row, purpose = op["raw_row"], op["purpose"]
            trips = [replace(t, purpose=purpose) if t.raw_row == row else t for t in trips]
        elif kind == "manual_time":
            row = op["raw_row"]
            out = []
            for t in trips:
                if t.raw_row == row:
                    tz = t.start_ts.tzinfo
                    new_start = _split_datetime(op["start_ts"], tz)
                    new_end = _split_datetime(op["end_ts"], tz)
                    t = replace(t, start_ts=new_start, end_ts=new_end,
                                route=_retime_route(t.route, new_start, new_end, row))
                out.append(t)
            # times changed -> re-sort so consecutive-pair classification sees order
            trips = sorted(out, key=lambda t: (str(t.person), t.start_ts))
        elif kind == "manual_reposition":
            row, end = op["raw_row"], op["end"]
            pts = _points_from_triplets(op["route"])
            fields = ({"start_lat": op["lat"], "start_lon": op["lon"]} if end == "start"
                      else {"end_lat": op["lat"], "end_lon": op["lon"]})
            trips = [replace(t, route=pts, **fields) if t.raw_row == row else t
                     for t in trips]
    return trips


def _points_from_triplets(triplets: list) -> list:
    """RoutePoints from ``[[lat, lon, iso_ts], ...]`` -- a `manual_reposition`
    op's precomputed, complete final route. Mirrors the raw file's routes-cell
    triplet shape, but travels as a plain JSON list (from decisions.jsonl), not
    a string, so no `ast.literal_eval` round-trip is needed here."""
    return [RoutePoint(float(lat), float(lon), parse_ts(ts)) for lat, lon, ts in triplets]


def _retime_route(route: list, start_ts, end_ts, raw_row: int) -> list:
    """Keep a trip's route consistent with an edited [start, end] window so the
    measured moving speed reflects the NEW duration, not the original one.

    A manual add carries a SYNTHETIC route (OSM geometry with evenly-spread fake
    timestamps), so re-spread the same coordinates across the new window: the full
    path is preserved and the speed tracks the new duration. Every other trip carries
    REAL GPS breadcrumbs, so instead CLIP to the points whose own timestamps fall
    inside the new window, never inventing or rescaling real observations. An empty
    route (a missing-trip stub) stays empty."""
    if not route:
        return route
    if is_add_row(raw_row):
        return _route_from_coords([[p.lat, p.lon] for p in route], start_ts, end_ts)
    return [p for p in route if start_ts <= p.ts <= end_ts]


def _split_datetime(s: str, tzinfo) -> datetime:
    """Parse a naive ``YYYY-MM-DDTHH:MM[:SS]`` split time and attach the trip's
    timezone so it is comparable to the (tz-aware) trip/route timestamps."""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None and tzinfo is not None:
        dt = dt.replace(tzinfo=tzinfo)
    return dt


def _apply_split(trips: list, op: dict) -> list:
    """Split one trip at ``split_ts`` into A (start..split) + B (split..end).

    A keeps the origin raw_row; B gets the stable `split_b_row`. The route splits
    by timestamp (<= split -> A, > split -> B); A's end / B's start snap to the
    last/first route breadcrumb on their side (falling back to the origin endpoint
    when a side has no breadcrumbs). B inherits the origin purpose (the destination
    semantics, matching how a merge keeps the last piece's purpose); A's is blank.
    Out-of-range split times and a missing origin are no-ops; an already-applied
    split (B present) is a no-op so the fixpoint loop converges.
    """
    orig_row = op["raw_row"]
    b_row = split_b_row(orig_row)
    by_row = {t.raw_row: t for t in trips}
    if b_row in by_row:
        return trips  # already split this pass
    orig = by_row.get(orig_row)
    if orig is None:
        return trips
    split_ts = _split_datetime(op["split_ts"], orig.start_ts.tzinfo)
    if not (orig.start_ts < split_ts < orig.end_ts):
        return trips  # out-of-range split -> no-op

    a_route = [p for p in orig.route if p.ts <= split_ts]
    b_route = [p for p in orig.route if p.ts > split_ts]
    a_end = (a_route[-1].lat, a_route[-1].lon) if a_route else (orig.start_lat, orig.start_lon)
    b_start = (b_route[0].lat, b_route[0].lon) if b_route else a_end

    trip_a = Trip(
        raw_row=orig_row, person=orig.person, mode=op.get("mode_a") or orig.mode,
        purpose="", start_ts=orig.start_ts, end_ts=split_ts,
        start_lat=orig.start_lat, start_lon=orig.start_lon,
        end_lat=a_end[0], end_lon=a_end[1], route=a_route,
    )
    trip_b = Trip(
        raw_row=b_row, person=orig.person, mode=op.get("mode_b") or orig.mode,
        purpose=orig.purpose, start_ts=split_ts, end_ts=orig.end_ts,
        start_lat=b_start[0], start_lon=b_start[1],
        end_lat=orig.end_lat, end_lon=orig.end_lon, route=b_route,
    )
    rest = [t for t in trips if t.raw_row != orig_row]
    return sorted(rest + [trip_a, trip_b], key=lambda t: (str(t.person), t.start_ts))


def _apply_add(trips: list, op: dict, n: int) -> list:
    """Materialise a hand-placed trip with the stable `add_row(n)` raw_row.

    If the op carries a ``route`` (a list of ``[lat, lon]`` from the OSM shortest
    path, computed server-side at add time) it becomes the trip's breadcrumbs with
    timestamps spread evenly across start..end; otherwise the trip has no route (a
    straight start->end line, like a missing-trip stub). Person and timezone are
    inferred from the day's existing trips when the op omits them. Already-present
    (re-applied) -> no-op for fixpoint convergence.
    """
    row = add_row(n)
    # Present as its own row, OR already merged into another trip (`absorbed`) — either
    # way do not re-materialise it, so a merge that swallowed this add stays converged.
    if any(t.raw_row == row or row in t.absorbed for t in trips):
        return trips
    tz = trips[0].start_ts.tzinfo if trips else None
    # Person: prefer an explicit op field, then the day_key (``person|date`` — always
    # present and unambiguous), and only then the surviving trips. The day_key
    # fallback matters when EVERY real trip on the day was removed (e.g. all
    # zero_move-accepted) before this add materialises: `trips` is then empty, so
    # inferring from `trips[0]` would leave the add person-less (blank accessCode in
    # the export, and unsortable into the participant's rows).
    person = op.get("person")
    if person is None:
        day_key = op.get("day_key") or ""
        person = day_key.split("|", 1)[0] if "|" in day_key else (trips[0].person if trips else None)
    start_ts = _split_datetime(op["start_ts"], tz)
    end_ts = _split_datetime(op["end_ts"], tz)
    trip = Trip(
        raw_row=row, person=person, mode=op.get("mode") or "Unknown",
        purpose=op.get("purpose", ""),
        start_ts=start_ts, end_ts=end_ts,
        start_lat=op["start_lat"], start_lon=op["start_lon"],
        end_lat=op["end_lat"], end_lon=op["end_lon"],
        route=_route_from_coords(op.get("route"), start_ts, end_ts),
    )
    return sorted(trips + [trip], key=lambda t: (str(t.person), t.start_ts))


def _route_from_coords(coords: list, start_ts, end_ts) -> list:
    """Turn a ``[[lat, lon], ...]`` shortest-path into timed `RoutePoint`s, spacing
    timestamps evenly across start..end. Fewer than 2 coords -> empty route."""
    if not coords or len(coords) < 2:
        return []
    step = (end_ts - start_ts) / (len(coords) - 1)
    return [RoutePoint(float(la), float(lo), start_ts + step * i)
            for i, (la, lo) in enumerate(coords)]


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
    routes in timestamp order; destination piece keeps mode/purpose.

    Identity raw_row = the lowest POSITIVE (workbook) member when the group has one,
    else the lowest (all-added groups keep their negative id). This anchors a
    logged+added merge to its real workbook row so the export shows ONE M-row
    instead of the logged row plus a stray A-row for the added leg."""
    g = sorted(group, key=lambda t: t.start_ts)
    first, last = g[0], g[-1]
    route = sorted((p for t in g for p in t.route), key=lambda p: p.ts)
    _rows = [t.raw_row for t in g]
    _pos = [r for r in _rows if r >= 0]
    absorbed = frozenset(r for t in g for r in ({t.raw_row} | set(t.absorbed)))
    return Trip(
        raw_row=min(_pos) if _pos else min(_rows),
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
        absorbed=absorbed,
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
