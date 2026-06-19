"""Per-session storage.

Responsibility: manage ``sessions/<id>/`` working dirs; save the uploaded raw
workbook; read/write the immutable ``proposals.json`` baseline (atomic temp +
rename). No business logic — the server runs the engine and hands results here.

Later steps add ``decisions.jsonl`` (append-only event log) and the on-demand
xlsx exports next to ``proposals.json`` in the same session dir.
"""

from __future__ import annotations

import json
import os
import uuid

from werkzeug.utils import secure_filename

SESSIONS_DIR = os.environ.get("TRIP_SESSIONS_DIR", "sessions")
PROPOSALS = "proposals.json"


def new_id() -> str:
    """A short, unguessable session id."""
    return uuid.uuid4().hex[:12]


def session_dir(sid: str) -> str:
    return os.path.join(SESSIONS_DIR, sid)


def exists(sid: str) -> bool:
    """True once the baseline has been written for this session."""
    return os.path.isfile(os.path.join(session_dir(sid), PROPOSALS))


def save_upload(sid: str, file_storage) -> str:
    """Persist the uploaded workbook into the session dir; return its path."""
    d = session_dir(sid)
    os.makedirs(d, exist_ok=True)
    name = secure_filename(file_storage.filename or "") or "upload.xlsx"
    path = os.path.join(d, name)
    file_storage.save(path)
    return path


def write_proposals(sid: str, data: dict) -> None:
    """Write the immutable baseline atomically (temp file + os.replace)."""
    d = session_dir(sid)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, PROPOSALS + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    os.replace(tmp, os.path.join(d, PROPOSALS))


def read_proposals(sid: str) -> dict:
    with open(os.path.join(session_dir(sid), PROPOSALS), encoding="utf-8") as fh:
        return json.load(fh)
