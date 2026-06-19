"""Build the immutable proposals baseline from engine output.

Turns `load_trips` + `classify_all` into the per-person-day payload the server
serves and persists once as `proposals.json`: each day's trips (with rounded GPS
routes) and its flags (stable content id + evidence + the within-day `seq`s the
flag touches). This is presentation of engine output — it depends on `engine`
only and holds no review state (that arrives later via the decision log).
"""

from __future__ import annotations

from engine.classify import group_person_days
from engine.ids import with_ids

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

        trips_json = []
        for seq, t in enumerate(day_trips, start=1):
            route = [[_r(p.lat), _r(p.lon)] for p in t.route] or \
                    [[_r(t.start_lat), _r(t.start_lon)], [_r(t.end_lat), _r(t.end_lon)]]
            trips_json.append({
                "seq": seq, "id": t.raw_row, "mode": t.mode, "purpose": t.purpose or "",
                "start": t.start_ts.strftime("%H:%M"), "end": t.end_ts.strftime("%H:%M"),
                "dur": round((t.end_ts - t.start_ts).total_seconds() / 60.0, 1),
                "s": [_r(t.start_lat), _r(t.start_lon)],
                "e": [_r(t.end_lat), _r(t.end_lon)],
                "route": route,
            })

        flags_json = []
        for f in flags_by_day.get((person, date), []):
            seqs = [seq_of.get(f.trip)] if f.trip is not None \
                else [seq_of.get(f.left[0]), seq_of.get(f.right[0])]
            flags_json.append({
                "id": f.flag_id, "type": f.flag_type, "origin": f.origin,
                "seqs": seqs, "ev": _evidence(f.evidence),
            })

        days[f"{person}|{date}"] = {
            "person": person or "(unknown)", "date": str(date),
            "trips": trips_json, "flags": flags_json,
        }

    return {
        "raw_name": raw_name,
        "counts": {
            "trips": len(trips), "flags": len(flags),
            "persons": len({t.person for t in trips}), "person_days": len(days),
        },
        "days": days,
    }
