"""Flask endpoints (thin controllers).

Responsibility: HTTP routing only — delegates all logic to `session`,
`baseline`, and `engine`. Stage 4 part 1 implements the upload->engine->serve
path: drag-drop a raw workbook, run the classifier, persist the baseline, then
serve per-person-day proposals as JSON. Review actions (accept/waive, cascade,
manual edits, generate, metrics) arrive in later parts.
"""

from __future__ import annotations

import os

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

from app import baseline, session
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

    return app


app = create_app()
