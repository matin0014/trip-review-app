"""Flask endpoints (thin controllers).

Responsibility: HTTP routing only — delegates all logic to `session`,
`baseline`, and `engine`. Stage 4 part 1 implements the upload->engine->serve
path: drag-drop a raw workbook, run the classifier, persist the baseline, then
serve per-person-day proposals as JSON. Review actions (accept/waive, cascade,
manual edits, generate, metrics) arrive in later parts.
"""

from __future__ import annotations

import os

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for

from app import baseline, decisions, generate, session, state
from engine.classify import classify_all
from engine.loader import load_trips

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # generous cap for a raw wave workbook


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
        except Exception as exc:  # surface a bad workbook as a 400, not a 500
            abort(400, f"could not process workbook: {exc}")

        data = baseline.build_baseline(trips, flags, os.path.basename(path))
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
        days = [
            {"key": k, "person": v["person"], "date": v["date"],
             "n_trips": len(v["trips"]), "n_flags": len(v["flags"])}
            for k, v in data["days"].items()
        ]
        days.sort(key=lambda d: (d["person"], d["date"]))
        return jsonify({"raw_name": data["raw_name"], "counts": data["counts"], "days": days})

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
        trips = state.day_trips(sid, request.args.get("key", ""))
        if trips is None:
            abort(404)

        res = decisions.resolutions(session.read_events(sid))
        accepted = {fid for fid, r in res.items() if r in decisions.APPLY_RESOLUTIONS}
        live_trips, surfaced = decisions.fixpoint(trips, accepted)

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
        path = generate.generate_cleaned(sid)
        return send_file(path, as_attachment=True, download_name="cleaned.xlsx")

    return app


app = create_app()
