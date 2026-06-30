"""Flask endpoints (thin controllers).

Responsibility: HTTP routing only — delegates all logic to `session`,
`baseline`, and `engine`. Stage 4 part 1 implements the upload->engine->serve
path: drag-drop a raw workbook, run the classifier, persist the baseline, then
serve per-person-day proposals as JSON. Review actions (accept/waive, cascade,
manual edits, generate, metrics) arrive in later parts.
"""

from __future__ import annotations

import hashlib
import os
from datetime import date, time

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for

from app import baseline, decisions, demographics, generate, places, routing, session, state
from engine.classify import classify_all
from engine.geo import haversine_m
from engine.loader import load_trips
from engine.velocity import trip_moving_speed

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # generous cap for a raw wave workbook
PURPOSE_PLACE_BUFFER_M = 200.0


def _mode_speeds(sid: str) -> list:
    """Average moving speed (km/h) per travel mode across the whole workbook.

    A read-only reference for the reviewer: it answers "what speed is normal for
    this mode" so a trip's measured speed can be sanity-checked against its label.
    Sorted fastest first; `n` is how many trips back each average."""
    from collections import defaultdict
    acc: dict = defaultdict(list)
    for trips in state.all_days(sid).values():
        for t in trips:
            acc[t.mode].append(trip_moving_speed(t))
    out = [{"mode": m, "avg_kmh": round(sum(v) / len(v), 1), "n": len(v)}
           for m, v in acc.items() if v]
    out.sort(key=lambda r: -r["avg_kmh"])
    return out


def _purpose_options(sid: str) -> list:
    """Distinct non-empty purpose labels from the uploaded workbook."""
    vals = {
        (t.purpose or "").strip()
        for trips in state.all_days(sid).values()
        for t in trips
        if (t.purpose or "").strip()
    }
    return sorted(vals, key=str.casefold)


def _participant_info(sid: str, data: dict) -> dict:
    """Participant demographics from the saved baseline, with old-session fallback."""
    if "participant_info" in data:
        return data.get("participant_info") or {}
    try:
        raw_path = os.path.join(session.session_dir(sid), data["raw_name"])
        return demographics.extract_participant_info(raw_path)
    except Exception:
        return {}


def _participant_value(data: dict, person: str, key: str) -> str:
    for row in (data.get("participant_info") or {}).get(person, []):
        if row.get("key") == key:
            return str(row.get("value") or "").strip()
    return ""


def _place(day: dict, kind: str) -> "dict | None":
    for p in day.get("places") or []:
        if p.get("type") == kind and p.get("lat") is not None and p.get("lon") is not None:
            return p
    return None


def _should_suggest_morning_commute(data: dict, day: dict, live_trips: list) -> bool:
    day_date = _day_date(day)
    if day_date is not None and day_date.weekday() >= 5:
        return False
    arrangement = _participant_value(data, day.get("person"), "workArrangements").casefold()
    if arrangement not in {"everyday travel to work", "mostly travel to work"}:
        return False
    if _place(day, "home") is None or _place(day, "work") is None:
        return False
    return not any(t.start_ts.time() < time(10, 0) for t in live_trips)


def _day_date(day: dict) -> "date | None":
    try:
        return date.fromisoformat(str(day.get("date")))
    except (TypeError, ValueError):
        return None


def _morning_commute_flag(day_key: str, data: dict, day: dict, live_trips: list) -> "dict | None":
    if not _should_suggest_morning_commute(data, day, live_trips):
        return None
    home, work = _place(day, "home"), _place(day, "work")
    dist_m = haversine_m(home["lat"], home["lon"], work["lat"], work["lon"])
    fid_key = ("morning_commute", day_key, round(home["lat"], 6), round(home["lon"], 6),
               round(work["lat"], 6), round(work["lon"], 6))
    return {
        "id": hashlib.sha1(repr(fid_key).encode("utf-8")).hexdigest()[:16],
        "type": "morning_commute",
        "origin": "demographics",
        "seqs": [],
        "status": "pending",
        "ev": {
            "gap_distance_m": round(dist_m, 1),
            "start_time": "08:30",
            "start": [round(home["lat"], 5), round(home["lon"], 5)],
            "end": [round(work["lat"], 5), round(work["lon"], 5)],
        },
    }


def _purpose_place_flags(day_key: str, day: dict, live_trips: list) -> list:
    out = []
    specs = (("work", "Work", "purpose_work"), ("home", "Home", "purpose_home"))
    for t in live_trips:
        if (t.purpose or "").strip():
            continue
        for kind, purpose, flag_type in specs:
            place = _place(day, kind)
            if place is None:
                continue
            dist_m = haversine_m(t.end_lat, t.end_lon, place["lat"], place["lon"])
            if dist_m > PURPOSE_PLACE_BUFFER_M:
                continue
            fid_key = (flag_type, day_key, t.raw_row, round(place["lat"], 6), round(place["lon"], 6))
            out.append({
                "id": hashlib.sha1(repr(fid_key).encode("utf-8")).hexdigest()[:16],
                "type": flag_type,
                "origin": "places",
                "seqs": [],
                "trip_id": t.raw_row,
                "purpose": purpose,
                "status": "pending",
                "ev": {
                    "displacement_m": round(dist_m, 1),
                    "buffer_m": PURPOSE_PLACE_BUFFER_M,
                    "place": purpose,
                },
            })
            break
    return out


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
    data = session.read_proposals(sid)

    counts: dict = {}
    for key, trips in state.all_days(sid).items():
        ops = [op for op in ops_all if op.get("day_key") == key]
        live_trips, surfaced = decisions.fixpoint(trips, accepted, ops=ops)
        count = sum(1 for f in surfaced if res.get(f.flag_id) not in resolved)
        commute = _morning_commute_flag(key, data, data["days"].get(key, {}), live_trips)
        if commute is not None and res.get(commute["id"]) not in resolved:
            count += 1
        for f in _purpose_place_flags(key, data["days"].get(key, {}), live_trips):
            if res.get(f["id"]) not in resolved:
                count += 1
        counts[key] = count
    return counts


def _combine_dt(day_key: str, hms: str) -> str:
    """Rebuild a naive ISO instant from a day_key's date and an ``HH:MM[:SS]``
    time typed in the UI (the engine attaches the day's timezone)."""
    date = day_key.split("|", 1)[1]
    hms = str(hms).strip()
    if hms.count(":") == 1:
        hms = f"{hms}:00"
    return f"{date}T{hms}"


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.post("/upload")
    def upload():
        file = request.files.get("file")
        if file is None or not file.filename:
            abort(400, "no file uploaded")

        sid = session.new_id()
        path = session.save_upload(sid, file)
        try:
            trips = load_trips(path)
            flags = classify_all(trips)
            places_by_person = places.extract_places(path)
            participant_info_by_person = demographics.extract_participant_info(path)
        except Exception as exc:  # surface a bad workbook as a 400, not a 500
            abort(400, f"could not process workbook: {exc}")

        data = baseline.build_baseline(
            trips, flags, os.path.basename(path), places_by_person, participant_info_by_person)
        session.write_proposals(sid, data)
        return redirect(url_for("review", sid=sid))

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
        days = [
            {"key": k, "person": v["person"], "date": v["date"],
             "n_trips": len(v["trips"]), "n_flags": pending.get(k, 0)}
            for k, v in data["days"].items()
        ]
        days.sort(key=lambda d: (d["person"], d["date"]))
        return jsonify({"raw_name": data["raw_name"], "counts": data["counts"],
                        "days": days, "mode_speeds": _mode_speeds(sid),
                        "participant_info": _participant_info(sid, data),
                        "purpose_options": _purpose_options(sid)})

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
        data = session.read_proposals(sid)
        day = data["days"].get(key, {})

        all_events = session.read_events(sid)
        res = decisions.resolutions(all_events)
        accepted = {fid for fid, r in res.items() if r in decisions.APPLY_RESOLUTIONS}
        ops = [op for op in decisions.manual_ops(all_events) if op.get("day_key") == key]
        live_trips, surfaced = decisions.fixpoint(trips, accepted, ops=ops)
        live_trips = routing.enrich_live_routes(live_trips, trips)

        seq_of = {t.raw_row: i for i, t in enumerate(live_trips, start=1)}

        def status(flag_id):
            r = res.get(flag_id)
            if r in decisions.APPLY_RESOLUTIONS:
                return "accepted"
            return "waived" if r in decisions.WAIVE_RESOLUTIONS else "pending"

        flags = [baseline.serialize_flag(f, seq_of, status(f.flag_id)) for f in surfaced]
        commute = _morning_commute_flag(key, data, day, live_trips)
        if commute is not None:
            r = res.get(commute["id"])
            if r in decisions.WAIVE_RESOLUTIONS:
                commute["status"] = "waived"
            elif r in decisions.APPLY_RESOLUTIONS:
                commute["status"] = "accepted"
            flags.append(commute)
        for purpose_flag in _purpose_place_flags(key, day, live_trips):
            purpose_flag["seqs"] = [seq_of.get(purpose_flag["trip_id"])]
            r = res.get(purpose_flag["id"])
            if r in decisions.WAIVE_RESOLUTIONS:
                purpose_flag["status"] = "waived"
            elif r in decisions.APPLY_RESOLUTIONS:
                purpose_flag["status"] = "accepted"
            flags.append(purpose_flag)

        return jsonify({
            "trips": [baseline.serialize_trip(t, i) for i, t in enumerate(live_trips, start=1)],
            "flags": flags,
            "places": day.get("places", []),
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
        raw_row, day_key = body.get("raw_row"), body.get("day_key")
        if raw_row is None or not day_key:
            abort(400, "require raw_row and day_key")
        session.append_event(sid, {"type": "manual_purpose", "raw_row": int(raw_row),
                                   "purpose": str(body.get("purpose") or ""),
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
            abort(400, "require raw_row, day_key, start_time and end_time")
        start_ts = _combine_dt(day_key, start_time)
        end_ts = _combine_dt(day_key, end_time)
        if end_ts <= start_ts:
            abort(400, "end time must be after start time")
        session.append_event(sid, {
            "type": "manual_time", "raw_row": int(raw_row),
            "start_ts": start_ts, "end_ts": end_ts, "day_key": day_key})
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
        session.append_event(sid, {
            "type": "manual_add",
            "start_ts": start_ts,
            "end_ts": end_ts,
            "mode": body.get("mode") or "Unknown", "purpose": body.get("purpose", ""),
            "start_lat": float(body["start_lat"]), "start_lon": float(body["start_lon"]),
            "end_lat": float(body["end_lat"]), "end_lon": float(body["end_lon"]),
            "route": body.get("route") or [],
            "day_key": day_key})
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
