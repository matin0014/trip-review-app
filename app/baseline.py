"""Build the immutable proposals baseline from engine output.

Turns `load_trips` + `classify_all` into the per-person-day payload the server
serves and persists once as `proposals.json`: each day's trips (with rounded GPS
routes) and its flags (stable content id + evidence + the within-day `seq`s the
flag touches). This is presentation of engine output — it depends on `engine`
only and holds no review state (that arrives later via the decision log).
"""

from __future__ import annotations

from engine.classify import group_person_days
from engine.geo import haversine_m
from engine.ids import with_ids
from engine.velocity import trip_moving_speed

# Evidence fields worth surfacing (human units already; PairEvidence / TripEvidence).
_EV_FIELDS = ("gap_distance_m", "gap_time_min", "v_a_kmh", "v_b_kmh",
              "t_min", "buffer_min", "residual_min", "slack_b_min",
              "displacement_m", "median_speed_kmh")


def _r(x: float) -> float:
    return round(x, 5)  # ~1 m, keeps embedded routes small


def _evidence(ev: object) -> dict:
    out: dict = {}
    if ev is None:
        return out
    for k in _EV_FIELDS:
        v = getattr(ev, k, None)
        if v is not None:
            out[k] = round(v, 1)
    return out


def serialize_trip(t, seq: int) -> dict:
    """One trip -> JSON (rounded route; falls back to start/end if no breadcrumbs).

    Shared by the baseline and the live recompute so the two never drift.

    `dist_m` is the distance actually travelled (summed over GPS breadcrumbs);
    `disp_m` is the straight-line start->end displacement; `r0`/`rN` are the first
    and last raw breadcrumb (None when the trip carries no route). The reviewer
    uses them to spot a reported endpoint that sits far from the GPS track."""
    pts = t.route
    route = [[_r(p.lat), _r(p.lon)] for p in pts] or \
            [[_r(t.start_lat), _r(t.start_lon)], [_r(t.end_lat), _r(t.end_lon)]]
    dist_m = sum(haversine_m(a.lat, a.lon, b.lat, b.lon)
                 for a, b in zip(pts, pts[1:])) if len(pts) >= 2 else None
    return {
        "seq": seq, "id": t.raw_row, "mode": t.mode, "purpose": t.purpose or "",
        "start": t.start_ts.strftime("%H:%M"), "end": t.end_ts.strftime("%H:%M"),
        "dur": round((t.end_ts - t.start_ts).total_seconds() / 60.0, 1),
        "speed": round(trip_moving_speed(t), 1),   # median moving speed (km/h)
        "manual": bool((t.purpose or "").strip()),  # user logged a purpose
        "s": [_r(t.start_lat), _r(t.start_lon)],
        "e": [_r(t.end_lat), _r(t.end_lon)],
        "dist_m": round(dist_m, 1) if dist_m is not None else None,
        "disp_m": round(haversine_m(t.start_lat, t.start_lon, t.end_lat, t.end_lon), 1),
        "r0": [_r(pts[0].lat), _r(pts[0].lon)] if pts else None,
        "rN": [_r(pts[-1].lat), _r(pts[-1].lon)] if pts else None,
        "npts": len(pts),
        "route": route,
    }


def serialize_flag(f, seq_of: dict, status: str = None) -> dict:
    """One flag -> JSON. `seq_of` maps raw_row -> within-day seq (None if the
    member is no longer a live trip, e.g. a consumed merge). `status` is set for
    the live view (accepted / waived / pending), omitted for the baseline."""
    seqs = [seq_of.get(f.trip)] if f.trip is not None \
        else [seq_of.get(f.left[0]), seq_of.get(f.right[0])]
    out = {"id": f.flag_id, "type": f.flag_type, "origin": f.origin,
           "seqs": seqs, "ev": _evidence(f.evidence)}
    if status is not None:
        out["status"] = status
    return out


def build_baseline(trips: list, flags: list, raw_name: str) -> dict:
    """Assemble the proposals baseline keyed by ``"<person>|<date>"``."""
    flags = with_ids(flags)
    by_row = {t.raw_row: t for t in trips}
    day_groups = group_person_days(trips)

    flags_by_day: dict = {}
    for f in flags:
        member = f.trip if f.trip is not None else f.left[0]
        t = by_row.get(member)
        if t is None:
            continue
        flags_by_day.setdefault((t.person, t.start_ts.date()), []).append(f)

    days: dict = {}
    for (person, date), day_trips in day_groups.items():
        seq_of = {t.raw_row: i for i, t in enumerate(day_trips, start=1)}
        days[f"{person}|{date}"] = {
            "person": person or "(unknown)", "date": str(date),
            "trips": [serialize_trip(t, seq) for seq, t in enumerate(day_trips, start=1)],
            "flags": [serialize_flag(f, seq_of) for f in flags_by_day.get((person, date), [])],
        }

    return {
        "raw_name": raw_name,
        "counts": {
            "trips": len(trips), "flags": len(flags),
            "persons": len({t.person for t in trips}), "person_days": len(days),
        },
        "days": days,
    }
