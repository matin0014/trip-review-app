"""Per-session storage.

Responsibility: manage ``sessions/<id>/`` working dirs; save the uploaded raw
workbook; read/write the immutable ``proposals.json`` baseline (atomic temp +
rename). No business logic — the server runs the engine and hands results here.

Later steps add ``decisions.jsonl`` (append-only event log) and the on-demand
xlsx exports next to ``proposals.json`` in the same session dir.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone

from werkzeug.utils import secure_filename


def _default_sessions_dir() -> str:
    """Where session data lives absent an explicit override: next to the frozen
    exe (PyInstaller sets ``sys.frozen`` + ``sys.executable``) so a reviewer's
    data lands beside the app regardless of the launch directory; the plain
    cwd-relative ``"sessions"`` in dev, unchanged."""
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "sessions")
    return "sessions"


SESSIONS_DIR = os.environ.get("TRIP_SESSIONS_DIR", _default_sessions_dir())
PROPOSALS = "proposals.json"
DECISIONS = "decisions.jsonl"
LAST_VIEWED = "last_viewed.json"


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


def save_bytes(sid: str, filename: str, data: bytes) -> str:
    """Persist already-read upload bytes into the session dir; return its path.
    Used by the upload flow, which reads the bytes once to hash them (for the
    resume/duplicate check) before deciding to commit a new session."""
    d = session_dir(sid)
    os.makedirs(d, exist_ok=True)
    name = secure_filename(filename or "") or "upload.xlsx"
    path = os.path.join(d, name)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def content_sha(data: bytes) -> str:
    """SHA-256 of the raw upload bytes: the key for resume-on-reupload."""
    return hashlib.sha256(data).hexdigest()


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


def append_event(sid: str, event: dict) -> dict:
    """Append one event to the session's `decisions.jsonl` (one JSON per line).

    Stamps a 1-based `seq` (line number) and UTC `ts`, then returns the stored
    event. Append-only — events are never edited or deleted (undo is itself a
    `revert` event), so the log doubles as the audit trail.
    """
    d = session_dir(sid)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, DECISIONS)
    stored = {"seq": _line_count(path) + 1,
              "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              **event}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(stored, separators=(",", ":")) + "\n")
    return stored


def read_events(sid: str) -> list:
    """All events for the session in order ([] if none logged yet)."""
    path = os.path.join(session_dir(sid), DECISIONS)
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def set_last_viewed(sid: str, day_key: str) -> None:
    """Record the person-day the reviewer most recently opened, so a reviewer
    returning later (a different day, a different launch of the app) can be
    reminded where they left off. Written atomically; overwritten on every
    live-day view, so it always reflects the last thing actually looked at,
    not the last thing edited."""
    d = session_dir(sid)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, LAST_VIEWED + ".tmp")
    payload = {"day_key": day_key, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    os.replace(tmp, os.path.join(d, LAST_VIEWED))


def get_last_viewed(sid: str) -> "dict | None":
    """``{"day_key": ..., "ts": ...}`` for the last-opened person-day, or None
    if the reviewer has never opened one yet (a brand-new session)."""
    path = os.path.join(session_dir(sid), LAST_VIEWED)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _line_count(path: str) -> int:
    if not os.path.isfile(path):
        return 0
    with open(path, encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def _session_ids() -> list:
    """Ids of every session that has a written baseline (an upload-only dir whose
    baseline is not yet built is skipped)."""
    if not os.path.isdir(SESSIONS_DIR):
        return []
    return [n for n in os.listdir(SESSIONS_DIR)
            if os.path.isfile(os.path.join(SESSIONS_DIR, n, PROPOSALS))]


def find_session_by_hash(sha: str) -> "str | None":
    """The id of an existing session built from the same workbook bytes, or None.
    Only sessions created after the resume feature carry a ``sha``; older ones never
    match (so they never spuriously block a fresh upload)."""
    for sid in _session_ids():
        try:
            if read_proposals(sid).get("sha") == sha:
                return sid
        except (OSError, ValueError):
            continue
    return None


def delete_session(sid: str) -> bool:
    """Permanently remove a session's working dir (raw file + baseline + decision
    log). Returns True if a dir was removed. Irreversible — the caller confirms."""
    d = session_dir(sid)
    if os.path.isdir(d):
        shutil.rmtree(d)
        return True
    return False


def list_sessions() -> list:
    """Every resumable session, newest-edited first: ``{sid, raw_name, counts,
    n_events, modified}``. `modified` is the decision log's mtime (or the
    baseline's, if nothing has been logged yet), so the list orders by real
    review activity."""
    out = []
    for sid in _session_ids():
        try:
            p = read_proposals(sid)
        except (OSError, ValueError):
            continue
        dec = os.path.join(session_dir(sid), DECISIONS)
        anchor = dec if os.path.isfile(dec) else os.path.join(session_dir(sid), PROPOSALS)
        mtime = os.path.getmtime(anchor)
        out.append({
            "sid": sid,
            "raw_name": p.get("raw_name", "(unknown)"),
            "counts": p.get("counts", {}),
            "n_events": _line_count(dec),
            "modified": datetime.fromtimestamp(mtime, timezone.utc).isoformat(timespec="seconds"),
            "last_viewed": get_last_viewed(sid),
            "_mtime": mtime,
        })
    out.sort(key=lambda s: s["_mtime"], reverse=True)
    for s in out:
        del s["_mtime"]
    return out
