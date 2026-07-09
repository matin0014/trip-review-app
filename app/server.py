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

from flask import Flask, abort, jsonify, render_template, request, send_file, url_for

from app import baseline, decisions, generate, regions, routing, session, state
from engine.classify import classify_all
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


def _region_status(sid: str) -> tuple:
    """``(auto_out, excluded)`` day-key sets for a session: the out-of-region days
    (auto-removed by default) and the effective excluded set after the reviewer's
    recover / hand-remove overrides. The day index's ⊘ marker and the export's drop
    read the same two sets, so the screen and the deliverable always agree."""
    auto_out = regions.out_of_region_days(state.all_days(sid), regions.load_regions())
    excluded = decisions.excluded_days(session.read_events(sid), auto_out)
    return auto_out, excluded


def _combine_dt(day_key: str, hms: str) -> str:
    """Rebuild a naive ISO instant from a day_key's date and an ``HH:MM[:SS]``
    time typed in the UI (the engine attaches the day's timezone)."""
    date = day_key.split("|", 1)[1]
    hms = str(hms).strip()
    if hms.count(":") == 1:
        hms = f"{hms}:00"
    return f"{date}T{hms}"


def _next_add_seq(sid: str, day_key: str) -> int:
    """The next stable per-day add identity: 1 + every ``manual_add`` EVER logged
    for this day (reverted ones included, so a number is never reused). Assigned
    once here at creation time and carried in the event forever after -- the
    engine must not recompute it from how many adds are currently active (see
    `engine.proposals.add_row`)."""
    return 1 + sum(1 for e in session.read_events(sid)
                   if e.get("type") == "manual_add" and e.get("day_key") == day_key)


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
        days = [
            {"key": k, "person": v["person"], "date": v["date"],
             "n_trips": len(v["trips"]), "n_flags": pending.get(k, 0),
             "out_of_region": k in auto_out, "excluded": k in excluded}
            for k, v in data["days"].items()
        ]
        days.sort(key=lambda d: (d["person"], d["date"]))
        return jsonify({"raw_name": data["raw_name"], "counts": data["counts"],
                        "days": days, "mode_speeds": _mode_speeds(sid),
                        "people": data.get("people", {}),
                        "n_excluded": len(excluded)})

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

        all_events = session.read_events(sid)
        res = decisions.resolutions(all_events)
        accepted = {fid for fid, r in res.items() if r in decisions.APPLY_RESOLUTIONS}
        ops = [op for op in decisions.manual_ops(all_events) if op.get("day_key") == key]
        live_trips, surfaced = decisions.fixpoint(trips, accepted, ops=ops)
        live_trips = [routing.route_trip(t) for t in live_trips]  # OSM path for stubs

        seq_of = {t.raw_row: i for i, t in enumerate(live_trips, start=1)}

        def status(flag_id):
            r = res.get(flag_id)
            if r in decisions.APPLY_RESOLUTIONS:
                return "accepted"
            return "waived" if r in decisions.WAIVE_RESOLUTIONS else "pending"

        return jsonify({
            "trips": [baseline.serialize_trip(t, i) for i, t in enumerate(live_trips, start=1)],
            "flags": [baseline.serialize_flag(f, seq_of, status(f.flag_id)) for f in surfaced],
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
        s_lat, s_lon = float(body["start_lat"]), float(body["start_lon"])
        e_lat, e_lon = float(body["end_lat"]), float(body["end_lon"])
        mode = body.get("mode") or "Unknown"
        # OSM shortest path between the two clicked points, computed once and stored
        # in the op so the pure engine just materialises it (empty -> straight line).
        route = routing.osrm_route(s_lat, s_lon, e_lat, e_lon, mode)
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
        session.append_event(sid, {
            "type": "manual_time", "raw_row": int(raw_row),
            "start_ts": start_ts, "end_ts": end_ts, "day_key": day_key})
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

    @app.get("/api/<sid>/generate")
    def api_generate(sid):
        if not session.exists(sid):
            abort(404)
        path = generate.generate_bundle(sid)   # cleaned.xlsx + dofile.json, zipped
        return send_file(path, as_attachment=True, download_name="trip_review_export.zip")

    return app


app = create_app()
