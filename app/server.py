"""Flask endpoints (thin controllers).

Responsibility: HTTP routing only — delegates all logic to `session`,
`baseline`, and `engine`. Stage 4 part 1 implements the upload->engine->serve
path: drag-drop a raw workbook, run the classifier, persist the baseline, then
serve per-person-day proposals as JSON. Review actions (accept/waive, cascade,
manual edits, generate, metrics) arrive in later parts.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

from flask import Flask, abort, jsonify, render_template, request, send_file, url_for

from app import baseline, charts, decisions, generate, regions, routing, session, state
from engine.classify import classify_all
from engine.proposals import _route_from_coords, split_b_row
from engine.loader import load_socio, load_trips

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # generous cap for a raw wave workbook


def _app_dir() -> str:
    """The ``app`` package directory templates/static live under, resolved
    explicitly rather than left to Flask's ``__name__``-based default: under a
    PyInstaller build, ``sys._MEIPASS`` (set for both one-folder and one-file)
    is where the bundled ``app/templates`` and ``app/static`` datas land
    (see the spec's ``datas`` mapping); in dev it's just this file's directory."""
    base = getattr(sys, "_MEIPASS", None)
    if base is not None:
        return os.path.join(base, "app")
    return os.path.dirname(os.path.abspath(__file__))


# Realistic typical speed range (low, high) km/h per travel mode: a fixed
# yardstick the reviewer can judge a trip's label against, NOT an average measured
# from this dataset. high == low for a single-value reference. Values follow
# Canadian commute/transit references; car speed (urban..highway) is reused for
# every motor-vehicle mode (auto driver/passenger, taxi, vehicle, etc.).
_CAR = (30, 120)
_REF_SPEED_KMH = {
    "Walk": (5, 6), "Running": (10, 12),
    "Bike": (15, 24), "Shared e-scooter": (15, 24),
    "Bus": (20, 25), "Transit": (20, 25), "Transit Bus": (20, 25),
    "SkyTrain": (30, 45), "SeaBus": (15, 15),
    "Train": (54, 54),
    "Auto Driver": _CAR, "Auto Passenger": _CAR, "Taxi": _CAR, "Vehicle": _CAR,
    "Car Share": _CAR, "Motorcycle": _CAR,
}


def _fmt_ref(rng) -> "str | None":
    if rng is None:
        return None
    lo, hi = rng
    return f"~{lo} km/h" if lo == hi else f"~{lo} to {hi} km/h"


def _estimate_duration_min(mode: str, distance_m: float) -> "float | None":
    """Suggested travel time (minutes) for a manual add: distance / the mode's
    canonical reference speed (the SAME _REF_SPEED_KMH yardstick shown to the
    reviewer elsewhere), using the midpoint of its (low, high) km/h range.
    Deliberately NOT OSRM's own duration estimate -- OSRM only has foot/bike/
    driving profiles, so it would score a SkyTrain trip as if it were a car on
    that road route. None when the mode has no reference speed or the two
    points coincide (distance 0), so the caller leaves the end time blank
    rather than guessing."""
    rng = _REF_SPEED_KMH.get(mode)
    if not rng or distance_m <= 0:
        return None
    avg_speed_kmh = (rng[0] + rng[1]) / 2
    return (distance_m / 1000.0) / avg_speed_kmh * 60.0


def _mode_speeds(sid: str) -> list:
    """Reference speed per travel mode PRESENT in the workbook.

    `ref` is a realistic typical speed (or range) for the mode, a fixed lookup, not
    an average of this dataset, giving the reviewer a sane yardstick for a trip's
    label. Modes with no reference are listed with `ref = None`. Sorted fastest
    first by the top of the range."""
    modes = {t.mode for trips in state.all_days(sid).values() for t in trips}
    rows = [{"mode": m, "rng": _REF_SPEED_KMH.get(m)} for m in modes]
    rows.sort(key=lambda r: (r["rng"] is None, -(r["rng"][1] if r["rng"] else 0), r["mode"]))
    return [{"mode": r["mode"], "ref": _fmt_ref(r["rng"])} for r in rows]


def _live_pending_counts(sid: str) -> dict:
    """Map each person-day key -> number of flags still PENDING in the live
    projection (current engine + this session's decisions). This is what the day
    dropdown's ⚠ badge shows, so it matches the open day and decrements as flags
    are resolved, unlike the frozen baseline count, which can also be stale if the
    session was built by an older engine."""
    all_events = session.read_events(sid)
    res = decisions.resolutions(all_events)
    accepted = {fid for fid, r in res.items() if r in decisions.APPLY_RESOLUTIONS}
    ops_all = decisions.manual_ops(all_events)
    resolved = decisions.APPLY_RESOLUTIONS | decisions.WAIVE_RESOLUTIONS

    counts: dict = {}
    for key, trips in state.all_days(sid).items():
        ops = [op for op in ops_all if op.get("day_key") == key]
        _, surfaced = decisions.fixpoint(trips, accepted, ops=ops)
        counts[key] = sum(1 for f in surfaced if res.get(f.flag_id) not in resolved)
    return counts


def _live_trip(sid: str, day_key: str, raw_row: int):
    """The LIVE (post-fixpoint) trip with `raw_row` on this day, or None.

    Used by the edits that must record what they were aimed at: a merged trip
    inherits its lowest member's raw_row, so `trip.absorbed` is what
    distinguishes "the merged trip" from "the un-merged leg" when the op is
    replayed (see `engine.proposals._op_targets`)."""
    trips = state.day_trips(sid, day_key)
    if not trips:
        return None
    events = session.read_events(sid)
    accepted = {fid for fid, r in decisions.resolutions(events).items()
                if r in decisions.APPLY_RESOLUTIONS}
    ops = [op for op in decisions.manual_ops(events) if op.get("day_key") == day_key]
    live_trips, _ = decisions.fixpoint(trips, accepted, ops=ops)
    return next((t for t in live_trips if t.raw_row == int(raw_row)), None)


def _day_pending_count(sid: str, day_key: str) -> int:
    """Flags still PENDING in the live projection for ONE person-day -- the same
    rule as `_live_pending_counts`, scoped to a single day so checking the
    "cleaned" box can gate on it without recomputing every day in the session."""
    trips = state.day_trips(sid, day_key)
    if not trips:
        return 0
    all_events = session.read_events(sid)
    res = decisions.resolutions(all_events)
    accepted = {fid for fid, r in res.items() if r in decisions.APPLY_RESOLUTIONS}
    resolved = decisions.APPLY_RESOLUTIONS | decisions.WAIVE_RESOLUTIONS
    ops = [op for op in decisions.manual_ops(all_events) if op.get("day_key") == day_key]
    _, surfaced = decisions.fixpoint(trips, accepted, ops=ops)
    return sum(1 for f in surfaced if res.get(f.flag_id) not in resolved)


def _region_status(sid: str) -> tuple:
    """``(auto_out, excluded)`` day-key sets for a session: the out-of-region days
    (auto-removed by default) and the effective excluded set after the reviewer's
    recover / hand-remove overrides. The day index's ⊘ marker and the export's drop
    read the same two sets, so the screen and the deliverable always agree."""
    auto_out = regions.out_of_region_days(state.all_days(sid), regions.load_regions())
    excluded = decisions.excluded_days(session.read_events(sid), auto_out)
    return auto_out, excluded


def _build_reposition_route(trip, end: str, lat: float, lon: float) -> list:
    """The trip's COMPLETE final route after moving its `end` ("start" or
    "end") point to (lat, lon): existing breadcrumbs are KEPT untouched, and a
    fresh patch fills the gap between the kept portion and the new point
    (falling back to no patch -- a straight line, via the existing mapview
    start/end-marker convention -- if routing is unreachable, same graceful
    degradation as everywhere else this app routes). Only the position
    changes; the trip's start_ts/end_ts are untouched, so the patch's
    timestamps are squeezed into whatever window sits between the kept portion
    and the trip's own (fixed) start/end instant.

    The patch comes from `routing.route_between`, the same call a manual ADD
    uses, so a SkyTrain / SeaBus / Kelowna-bus trip is patched along the real
    network rather than the road: OSRM has no rail profile, so dragging a
    SkyTrain endpoint used to draw the correction over surface streets and
    bridges the train never touches. It is directional, and returns a path
    running from the first point to the second, so the patch still meets the
    kept breadcrumbs at one end and the reviewer's new point at the other.

    Returns ``[[lat, lon, iso_ts], ...]``, ready to store in a
    `manual_reposition` op -- see `engine.proposals.apply_manual_ops`, which
    just overwrites the trip's route with this precomputed value every round.
    """
    route = trip.route
    if end == "end":
        if route:
            anchor_lat, anchor_lon, window_start = route[-1].lat, route[-1].lon, route[-1].ts
        else:
            anchor_lat, anchor_lon, window_start = trip.start_lat, trip.start_lon, trip.start_ts
        coords = routing.route_between(trip.mode, anchor_lat, anchor_lon, lat, lon)
        patch = _route_from_coords(coords, window_start, trip.end_ts) if coords else []
        if route and patch:
            patch = patch[1:]  # drop the leading point (duplicates the last kept breadcrumb)
        combined = list(route) + patch
    else:
        if route:
            anchor_lat, anchor_lon, window_end = route[0].lat, route[0].lon, route[0].ts
        else:
            anchor_lat, anchor_lon, window_end = trip.end_lat, trip.end_lon, trip.end_ts
        coords = routing.route_between(trip.mode, lat, lon, anchor_lat, anchor_lon)
        patch = _route_from_coords(coords, trip.start_ts, window_end) if coords else []
        if route and patch:
            patch = patch[:-1]  # drop the trailing point (duplicates the first kept breadcrumb)
        combined = patch + list(route)
    return [[p.lat, p.lon, p.ts.isoformat()] for p in combined]


def _combine_dt(day_key: str, hms: str) -> str:
    """Rebuild a naive ISO instant from a day_key's date and an ``HH:MM[:SS]``
    time typed in the UI (the engine attaches the day's timezone)."""
    date = day_key.split("|", 1)[1]
    hms = str(hms).strip()
    if hms.count(":") == 1:
        hms = f"{hms}:00"
    return f"{date}T{hms}"


def _time_conflict(sid: str, day_key: str, start_ts: str, end_ts: str):
    """The first LIVE trip on this day whose window overlaps ``[start_ts,
    end_ts]`` (both naive ISO strings sharing the day_key's date), or None if
    there's no conflict -- blocks a manual add that would double-book the
    person. Overlap is strict: one trip ending exactly when another starts is
    NOT a conflict, that's an ordinary back-to-back day. `state.day_trips`
    returning None (unknown day) or an empty live day both mean nothing to
    conflict with."""
    trips = state.day_trips(sid, day_key)
    if not trips:
        return None
    events = session.read_events(sid)
    accepted = {fid for fid, r in decisions.resolutions(events).items()
                if r in decisions.APPLY_RESOLUTIONS}
    ops = [op for op in decisions.manual_ops(events) if op.get("day_key") == day_key]
    live_trips, _ = decisions.fixpoint(trips, accepted, ops=ops)
    if not live_trips:
        return None
    tz = live_trips[0].start_ts.tzinfo
    s = datetime.fromisoformat(start_ts).replace(tzinfo=tz)
    e = datetime.fromisoformat(end_ts).replace(tzinfo=tz)
    for t in live_trips:
        if s < t.end_ts and t.start_ts < e:
            return t
    return None


def _next_add_seq(sid: str, day_key: str) -> int:
    """The next stable per-day add identity: 1 + every ``manual_add`` EVER logged
    for this day (reverted ones included, so a number is never reused). Assigned
    once here at creation time and carried in the event forever after -- the
    engine must not recompute it from how many adds are currently active (see
    `engine.proposals.add_row`)."""
    return 1 + sum(1 for e in session.read_events(sid)
                   if e.get("type") == "manual_add" and e.get("day_key") == day_key)


def _time_part(iso_ts: object) -> str:
    text = str(iso_ts or "")
    return text.split("T", 1)[1][:5] if "T" in text else text[:5]


def _edit_label(op: dict) -> str:
    """Human-readable one-liner for one manual op, shown in the day's Edited list."""
    kind = op.get("type")
    if kind == "manual_delete":
        return f"Deleted trip row {op.get('raw_row')}"
    if kind == "manual_merge":
        return "Merged rows " + ", ".join(str(r) for r in op.get("raw_rows") or [])
    if kind == "manual_split":
        return f"Split row {op.get('raw_row')} at {_time_part(op.get('split_ts'))}"
    if kind == "manual_add":
        return f"Added trip {_time_part(op.get('start_ts'))}-{_time_part(op.get('end_ts'))}"
    if kind == "manual_mode":
        return f"Changed row {op.get('raw_row')} mode to {op.get('mode')}"
    if kind == "manual_purpose":
        purpose = op.get("purpose") or "blank"
        return f"Changed row {op.get('raw_row')} purpose to {purpose}"
    if kind == "manual_time":
        return f"Changed row {op.get('raw_row')} time to {_time_part(op.get('start_ts'))}-{_time_part(op.get('end_ts'))}"
    if kind == "manual_reposition":
        return f"Moved row {op.get('raw_row')}'s {op.get('end')} point"
    return kind or "Edit"


def _edits_for_day(events: list, day_key: str) -> list:
    """The day's manual edits in log order, each tagged with its serial number
    so the UI's Undo button can target one specific edit via `undo_event`."""
    edits = []
    for op in decisions.manual_ops(events):
        if op.get("day_key") != day_key:
            continue
        edits.append({
            "seq": op.get("seq"),
            "type": op.get("type"),
            "label": _edit_label(op),
            "ts": op.get("ts", ""),
        })
    return edits


def create_app() -> Flask:
    base = _app_dir()
    app = Flask(__name__, template_folder=os.path.join(base, "templates"),
                static_folder=os.path.join(base, "static"))
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.post("/upload")
    def upload():
        file = request.files.get("file")
        if file is None or not file.filename:
            abort(400, "no file uploaded")

        raw = file.read()                       # read once: needed to hash + to save
        sha = session.content_sha(raw)
        # Resume-on-reupload: unless the user chose "start new", offer the existing
        # session for this exact workbook instead of forking a duplicate.
        if request.form.get("force_new") != "1":
            existing = session.find_session_by_hash(sha)
            if existing:
                return jsonify({"duplicate": True, "existing": existing,
                                "raw_name": file.filename})

        sid = session.new_id()
        path = session.save_bytes(sid, file.filename, raw)
        try:
            trips = load_trips(path)
            flags = classify_all(trips)
            people = load_socio(path)  # optional home/work/vehicles; {} if absent
        except Exception as exc:  # surface a bad workbook as a 400, not a 500
            abort(400, f"could not process workbook: {exc}")

        data = baseline.build_baseline(trips, flags, os.path.basename(path))
        data["people"] = people
        data["sha"] = sha
        session.write_proposals(sid, data)
        return jsonify({"sid": sid, "redirect": url_for("review", sid=sid)})

    @app.get("/api/sessions")
    def api_sessions():
        """Resumable sessions for the landing page, newest-edited first."""
        return jsonify(session.list_sessions())

    @app.post("/api/<sid>/delete")
    def api_delete_session(sid):
        """Permanently delete one session (raw file + baseline + decision log)."""
        if not session.exists(sid):
            abort(404)
        session.delete_session(sid)
        state.clear(sid)   # drop the gone session's cached trips
        return jsonify({"deleted": sid})

    @app.get("/s/<sid>")
    def review(sid):
        if not session.exists(sid):
            abort(404)
        return render_template("session.html", sid=sid)

    @app.get("/api/<sid>/days")
    def api_days(sid):
        if not session.exists(sid):
            abort(404)
        data = session.read_proposals(sid)
        pending = _live_pending_counts(sid)
        auto_out, excluded = _region_status(sid)
        cleaned = decisions.cleaned_days(session.read_events(sid))
        days = [
            {"key": k, "person": v["person"], "date": v["date"],
             "n_trips": len(v["trips"]), "n_flags": pending.get(k, 0),
             "out_of_region": k in auto_out, "excluded": k in excluded,
             "cleaned": cleaned.get(k, False)}
            for k, v in data["days"].items()
        ]
        days.sort(key=lambda d: (d["person"], d["date"]))
        return jsonify({"raw_name": data["raw_name"], "counts": data["counts"],
                        "days": days, "mode_speeds": _mode_speeds(sid),
                        "people": data.get("people", {}),
                        "n_excluded": len(excluded),
                        "last_viewed": session.get_last_viewed(sid)})

    @app.get("/api/<sid>/day")
    def api_day(sid):
        if not session.exists(sid):
            abort(404)
        day = session.read_proposals(sid)["days"].get(request.args.get("key", ""))
        if day is None:
            abort(404)
        return jsonify(day)

    def _state(sid):
        """Current review state: which flag ids are applied, derived from the log."""
        events = session.read_events(sid)
        accepted = decisions.replay(events)
        return jsonify({"accepted": sorted(accepted),
                        "n_accepted": len(accepted), "n_events": len(events)})

    @app.get("/api/<sid>/live-day")
    def api_live_day(sid):
        if not session.exists(sid):
            abort(404)
        key = request.args.get("key", "")
        trips = state.day_trips(sid, key)
        if trips is None:
            abort(404)
        session.set_last_viewed(sid, key)   # "where the reviewer left off"

        all_events = session.read_events(sid)
        res = decisions.resolutions(all_events)
        accepted = {fid for fid, r in res.items() if r in decisions.APPLY_RESOLUTIONS}
        ops = [op for op in decisions.manual_ops(all_events) if op.get("day_key") == key]
        live_trips, surfaced = decisions.fixpoint(trips, accepted, ops=ops)
        live_trips = [routing.route_trip(t) for t in live_trips]  # OSM path for stubs
        live_trips = [routing.fill_route_gaps(t) for t in live_trips]  # route a merge seam

        seq_of = {t.raw_row: i for i, t in enumerate(live_trips, start=1)}

        # A trip whose split_b_row is ALSO live right now is one half of a still-
        # active manual split (once merged, one side is absorbed and this is empty
        # for that raw_row -- exactly when the pairing no longer matters).
        live_rows = set(seq_of)
        sibling_of = {}
        for row in live_rows:
            if row >= 0 and split_b_row(row) in live_rows:
                sibling_of[row] = split_b_row(row)
                sibling_of[split_b_row(row)] = row

        def status(flag_id):
            r = res.get(flag_id)
            if r in decisions.APPLY_RESOLUTIONS:
                return "accepted"
            return "waived" if r in decisions.WAIVE_RESOLUTIONS else "pending"

        # {raw_row: note} for THIS day only; the client keys them off each trip's id
        comments = {str(rr): txt for (dk, rr), txt
                    in decisions.trip_comments(all_events).items() if dk == key}
        return jsonify({
            "trips": [baseline.serialize_trip(t, i, sibling_of.get(t.raw_row))
                     for i, t in enumerate(live_trips, start=1)],
            "flags": [baseline.serialize_flag(f, seq_of, status(f.flag_id)) for f in surfaced],
            "edits": _edits_for_day(all_events, key),
            "comments": comments,
        })

    @app.get("/api/<sid>/state")
    def api_state(sid):
        if not session.exists(sid):
            abort(404)
        return _state(sid)

    @app.post("/api/<sid>/decision")
    def api_decision(sid):
        if not session.exists(sid):
            abort(404)
        body = request.get_json(silent=True) or {}
        flag_id, resolution = body.get("flag_id"), body.get("resolution")
        if not flag_id or resolution not in decisions.VALID_RESOLUTIONS:
            abort(400, "require flag_id and a valid resolution "
                       f"({', '.join(sorted(decisions.VALID_RESOLUTIONS))})")
        session.append_event(sid, {"type": "decision", "flag_id": flag_id,
                                   "resolution": resolution})
        return _state(sid)

    @app.post("/api/<sid>/manual_delete")
    def api_manual_delete(sid):
        if not session.exists(sid):
            abort(404)
        body = request.get_json(silent=True) or {}
        raw_row, day_key = body.get("raw_row"), body.get("day_key")
        if raw_row is None or not day_key:
            abort(400, "require raw_row and day_key")
        session.append_event(sid, {"type": "manual_delete", "raw_row": int(raw_row),
                                   "day_key": day_key})
        return _state(sid)

    @app.post("/api/<sid>/remove_no_purpose")
    def api_remove_no_purpose(sid):
        """Delete every LIVE trip on the day whose purpose is blank ("no purpose"),
        in one shot -- one `manual_delete` per row, so each is individually
        undoable like a hand delete. Returns the count removed."""
        if not session.exists(sid):
            abort(404)
        body = request.get_json(silent=True) or {}
        day_key = body.get("day_key")
        if not day_key:
            abort(400, "require day_key")
        trips = state.day_trips(sid, day_key)
        if not trips:
            return jsonify({"removed": 0})
        events = session.read_events(sid)
        accepted = {fid for fid, r in decisions.resolutions(events).items()
                    if r in decisions.APPLY_RESOLUTIONS}
        ops = [op for op in decisions.manual_ops(events) if op.get("day_key") == day_key]
        live_trips, _ = decisions.fixpoint(trips, accepted, ops=ops)
        removed = 0
        for t in live_trips:
            if not (t.purpose or "").strip():
                session.append_event(sid, {"type": "manual_delete",
                                           "raw_row": int(t.raw_row), "day_key": day_key})
                removed += 1
        return jsonify({"removed": removed})

    @app.post("/api/<sid>/manual_mode")
    def api_manual_mode(sid):
        if not session.exists(sid):
            abort(404)
        body = request.get_json(silent=True) or {}
        raw_row, mode, day_key = body.get("raw_row"), body.get("mode"), body.get("day_key")
        if raw_row is None or not mode or not day_key:
            abort(400, "require raw_row, mode and day_key")
        session.append_event(sid, {"type": "manual_mode", "raw_row": int(raw_row),
                                   "mode": str(mode), "day_key": day_key})
        return _state(sid)

    @app.post("/api/<sid>/manual_purpose")
    def api_manual_purpose(sid):
        if not session.exists(sid):
            abort(404)
        body = request.get_json(silent=True) or {}
        raw_row, purpose, day_key = body.get("raw_row"), body.get("purpose"), body.get("day_key")
        if raw_row is None or purpose is None or not day_key:
            abort(400, "require raw_row, purpose and day_key")
        session.append_event(sid, {"type": "manual_purpose", "raw_row": int(raw_row),
                                   "purpose": str(purpose), "day_key": day_key})
        return _state(sid)

    @app.post("/api/<sid>/manual_merge")
    def api_manual_merge(sid):
        if not session.exists(sid):
            abort(404)
        body = request.get_json(silent=True) or {}
        raw_rows, day_key = body.get("raw_rows"), body.get("day_key")
        if not raw_rows or len(raw_rows) < 2 or not day_key:
            abort(400, "require raw_rows (>=2 items) and day_key")
        session.append_event(sid, {"type": "manual_merge",
                                   "raw_rows": [int(r) for r in raw_rows],
                                   "day_key": day_key})
        return _state(sid)

    @app.post("/api/<sid>/manual_split")
    def api_manual_split(sid):
        if not session.exists(sid):
            abort(404)
        body = request.get_json(silent=True) or {}
        raw_row, day_key = body.get("raw_row"), body.get("day_key")
        split_time = body.get("split_time")
        if raw_row is None or not day_key or not split_time:
            abort(400, "require raw_row, split_time and day_key")
        session.append_event(sid, {
            "type": "manual_split", "raw_row": int(raw_row),
            "split_ts": _combine_dt(day_key, split_time),
            "mode_a": body.get("mode_a"), "mode_b": body.get("mode_b"),
            "day_key": day_key})
        return _state(sid)

    @app.get("/api/route_estimate")
    def api_route_estimate():
        """Suggested duration for a manual-add's end-time field: real routed
        distance (OSRM, falling back to straight-line if unreachable) divided by
        the mode's canonical reference speed. A pure calculation endpoint (no
        session/day needed) -- the frontend adds `duration_min` to the start time
        it already has to get a suggested (editable) end time."""
        mode = request.args.get("mode", "")
        try:
            s_lat = float(request.args["start_lat"])
            s_lon = float(request.args["start_lon"])
            e_lat = float(request.args["end_lat"])
            e_lon = float(request.args["end_lon"])
        except (KeyError, ValueError):
            abort(400, "require start_lat, start_lon, end_lat, end_lon")

        distance_m = routing.osrm_distance_m(s_lat, s_lon, e_lat, e_lon, mode)
        routed = distance_m is not None
        if distance_m is None:
            distance_m = routing.straight_line_distance_m(s_lat, s_lon, e_lat, e_lon)
        duration_min = _estimate_duration_min(mode, distance_m)
        return jsonify({
            "distance_m": round(distance_m, 1),
            "routed": routed,
            "duration_min": round(duration_min, 1) if duration_min is not None else None,
        })

    @app.post("/api/<sid>/manual_add")
    def api_manual_add(sid):
        if not session.exists(sid):
            abort(404)
        body = request.get_json(silent=True) or {}
        day_key = body.get("day_key")
        coords = ("start_lat", "start_lon", "end_lat", "end_lon")
        times = ("start_time", "end_time")
        if not day_key or any(body.get(k) is None for k in coords + times):
            abort(400, "require day_key, start/end times and start/end coordinates")
        start_ts = _combine_dt(day_key, body["start_time"])
        end_ts = _combine_dt(day_key, body["end_time"])
        if end_ts <= start_ts:  # ISO instants share the day_key date -> string compare is chronological
            abort(400, "end time must be after start time")
        conflict = _time_conflict(sid, day_key, start_ts, end_ts)
        if conflict is not None:
            abort(400, f"overlaps an existing trip "
                       f"({conflict.start_ts.strftime('%H:%M')}-{conflict.end_ts.strftime('%H:%M')})")
        s_lat, s_lon = float(body["start_lat"]), float(body["start_lon"])
        e_lat, e_lon = float(body["end_lat"]), float(body["end_lon"])
        mode = body.get("mode") or "Unknown"
        # Path between the two clicked points, computed once and stored in the op so
        # the pure engine just materialises it (empty -> straight line): the real
        # SkyTrain track for a SkyTrain trip, else the OSM street path.
        route = routing.route_between(mode, s_lat, s_lon, e_lat, e_lon)
        session.append_event(sid, {
            "type": "manual_add",
            "add_seq": _next_add_seq(sid, day_key),
            "start_ts": start_ts,
            "end_ts": end_ts,
            "mode": mode, "purpose": body.get("purpose", ""),
            "start_lat": s_lat, "start_lon": s_lon,
            "end_lat": e_lat, "end_lon": e_lon,
            "route": route,
            "day_key": day_key})
        return _state(sid)

    @app.post("/api/<sid>/manual_time")
    def api_manual_time(sid):
        if not session.exists(sid):
            abort(404)
        body = request.get_json(silent=True) or {}
        raw_row, day_key = body.get("raw_row"), body.get("day_key")
        start_time, end_time = body.get("start_time"), body.get("end_time")
        if raw_row is None or not day_key or not start_time or not end_time:
            abort(400, "require raw_row, start_time, end_time and day_key")
        start_ts = _combine_dt(day_key, start_time)
        end_ts = _combine_dt(day_key, end_time)
        if end_ts <= start_ts:  # ISO instants share the day_key date -> string compare is chronological
            abort(400, "end time must be after start time")
        target = _live_trip(sid, day_key, raw_row)
        session.append_event(sid, {
            "type": "manual_time", "raw_row": int(raw_row),
            "start_ts": start_ts, "end_ts": end_ts, "day_key": day_key,
            # pins WHICH trip this window was typed for: a merged trip inherits
            # its lowest member's raw_row, so without this the op would later
            # shrink the merge back to this leg (engine.proposals._op_targets)
            "merged_rows": sorted(target.absorbed) if target is not None else []})
        return _state(sid)

    @app.post("/api/<sid>/manual_reposition")
    def api_manual_reposition(sid):
        """Move one trip's start or end point. Precomputes the COMPLETE final
        route ONCE here (kept breadcrumbs + a fresh OSRM patch to the new point)
        and stores that whole value in the op, so the pure engine's replay is a
        stable overwrite every fixpoint round -- see `_build_reposition_route`."""
        if not session.exists(sid):
            abort(404)
        body = request.get_json(silent=True) or {}
        raw_row, day_key, end = body.get("raw_row"), body.get("day_key"), body.get("end")
        if raw_row is None or not day_key or end not in ("start", "end"):
            abort(400, "require raw_row, day_key, end ('start' or 'end')")
        try:
            lat, lon = float(body["lat"]), float(body["lon"])
        except (KeyError, TypeError, ValueError):
            abort(400, "require lat, lon")

        target = _live_trip(sid, day_key, raw_row)
        if target is None:
            abort(404, "unknown trip")

        route_triplets = _build_reposition_route(target, end, lat, lon)
        session.append_event(sid, {
            "type": "manual_reposition", "raw_row": int(raw_row), "end": end,
            "lat": lat, "lon": lon, "route": route_triplets, "day_key": day_key,
            # the target's merge membership right now: a merged trip inherits its
            # lowest member's raw_row, so this pins WHICH trip the op meant (see
            # engine.proposals._op_targets)
            "merged_rows": sorted(target.absorbed)})
        return _state(sid)

    @app.post("/api/<sid>/day_include")
    def api_day_include(sid):
        if not session.exists(sid):
            abort(404)
        body = request.get_json(silent=True) or {}
        day_key, include = body.get("day_key"), body.get("include")
        if not day_key or not isinstance(include, bool):
            abort(400, "require day_key and include (bool)")
        session.append_event(sid, {"type": "day_include", "day_key": day_key,
                                   "include": include})
        return _state(sid)

    @app.post("/api/<sid>/day_cleaned")
    def api_day_cleaned(sid):
        """Reviewer's manual "cleaned" checkbox for one person-day. Sticky, like
        day_include -- see `decisions.cleaned_days`. Checking it off is refused
        while the day still has PENDING flags: every flag must be addressed
        (accepted or waived) first. Unchecking is always allowed."""
        if not session.exists(sid):
            abort(404)
        body = request.get_json(silent=True) or {}
        day_key, cleaned = body.get("day_key"), body.get("cleaned")
        if not day_key or not isinstance(cleaned, bool):
            abort(400, "require day_key and cleaned (bool)")
        if cleaned and _day_pending_count(sid, day_key) > 0:
            abort(400, "Flags must be addressed (accepted or waived) before marking this day as cleaned.")
        session.append_event(sid, {"type": "day_cleaned", "day_key": day_key,
                                   "cleaned": cleaned})
        return _state(sid)

    @app.post("/api/<sid>/comment")
    def api_comment(sid):
        """Attach (or clear) a free-text note on ONE trip. Sticky like day_include,
        outside the undo/redo LIFO -- see `decisions.trip_comments`. An empty
        string clears the note. Accepts any raw_row, including a manually-added
        trip's negative one."""
        if not session.exists(sid):
            abort(404)
        body = request.get_json(silent=True) or {}
        day_key, raw_row = body.get("day_key"), body.get("raw_row")
        comment = body.get("comment")
        if not day_key or raw_row is None or not isinstance(comment, str):
            abort(400, "require day_key, raw_row and comment (string)")
        session.append_event(sid, {"type": "trip_comment", "day_key": day_key,
                                   "raw_row": int(raw_row), "comment": comment.strip()})
        return _state(sid)

    @app.post("/api/<sid>/undo")
    def api_undo(sid):
        if not session.exists(sid):
            abort(404)
        session.append_event(sid, {"type": "revert"})
        return _state(sid)

    @app.post("/api/<sid>/redo")
    def api_redo(sid):
        if not session.exists(sid):
            abort(404)
        session.append_event(sid, {"type": "redo"})
        return _state(sid)

    @app.post("/api/<sid>/undo_event")
    def api_undo_event(sid):
        """Undo one specific edit from the day's Edited list, by its log serial
        number -- unlike /undo (which only pops the most recent action), this
        can remove an older edit while later ones stay in effect."""
        if not session.exists(sid):
            abort(404)
        body = request.get_json(silent=True) or {}
        seq = body.get("seq")
        try:
            seq = int(seq)
        except (TypeError, ValueError):
            abort(400, "require seq")
        session.append_event(sid, {"type": "undo_event", "target_seq": seq})
        return _state(sid)

    @app.get("/api/<sid>/start_times")
    def api_start_times(sid):
        """Start-time distribution, raw vs the session's LIVE cleaned state, both
        variants (all days / weekdays only) in one response so the panel's toggle
        needs no second fetch. Recomputed per call from the cached trips + the
        decision log -- same live projection the viewer shows."""
        if not session.exists(sid):
            abort(404)
        return jsonify(charts.start_time_series(state.all_days(sid),
                                                session.read_events(sid)))

    @app.get("/api/<sid>/generate")
    def api_generate(sid):
        if not session.exists(sid):
            abort(404)
        path = generate.generate_bundle(sid)   # cleaned.xlsx + dofile.json, zipped
        return send_file(path, as_attachment=True, download_name="trip_review_export.zip")

    @app.get("/api/<sid>/distributions")
    def api_distributions(sid):
        """Plain-browser dev fallback for the session case; the packaged app's
        pywebview path calls distributions.build_distributions_zip_for_session
        directly instead (see launch.py's Api.save_distributions)."""
        if not session.exists(sid):
            abort(404)
        from app import distributions
        out = os.path.abspath(os.path.join(session.session_dir(sid), "distributions.zip"))
        try:
            distributions.build_distributions_zip_for_session(sid, out)
        except Exception as exc:
            abort(500, f"could not build distributions ZIP: {exc}")
        # mimetype pinned: the OS registry decides otherwise, and Windows
        # answers application/x-zip-compressed where other platforms say
        # application/zip
        return send_file(out, as_attachment=True, mimetype="application/zip",
                         download_name="trip_review_distributions.zip")

    @app.post("/api/distributions/from_file")
    def api_distributions_from_file():
        """Standalone case (no session), plain-browser: build and return the ZIP
        bytes in one request."""
        file = request.files.get("file")
        if file is None or not file.filename:
            abort(400, "no file uploaded")
        import tempfile

        import pandas as pd

        from app import distributions
        try:
            df = pd.read_excel(file)
        except Exception as exc:
            abort(400, f"could not read workbook: {exc}")
        fd, tmp_path = tempfile.mkstemp(prefix="triprev_dist_", suffix=".zip")
        os.close(fd)
        try:
            distributions.build_distributions_zip(df, tmp_path)
        except ValueError as exc:
            abort(400, str(exc))
        except Exception as exc:
            abort(500, f"could not build distributions ZIP: {exc}")
        return send_file(tmp_path, as_attachment=True, mimetype="application/zip",
                         download_name="trip_review_distributions.zip")

    @app.post("/api/distributions/from_file/build")
    def api_distributions_from_file_build():
        """Standalone case, pywebview path: same generation as above, but hands
        back a server-local path instead of bytes -- a raw uploaded File object
        can't cross the pywebview JS bridge, so a second Api call (see
        launch.py's save_distributions_from_path) does the Save-As step using
        this path rather than the file itself."""
        file = request.files.get("file")
        if file is None or not file.filename:
            abort(400, "no file uploaded")
        import tempfile

        import pandas as pd

        from app import distributions
        try:
            df = pd.read_excel(file)
        except Exception as exc:
            abort(400, f"could not read workbook: {exc}")
        tmp_dir = tempfile.mkdtemp(prefix="triprev_dist_")
        out = os.path.join(tmp_dir, "distributions.zip")
        try:
            distributions.build_distributions_zip(df, out)
        except ValueError as exc:
            abort(400, str(exc))
        except Exception as exc:
            abort(500, f"could not build distributions ZIP: {exc}")
        return jsonify({"path": out})

    return app


app = create_app()
