"""Unit tests for `app.session` decision-log I/O (append-only, survives reload)."""

from __future__ import annotations

from app import session


def test_append_and_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSIONS_DIR", str(tmp_path))
    sid = "sess1"

    assert session.read_events(sid) == []  # nothing logged yet

    e1 = session.append_event(sid, {"type": "decision", "flag_id": "a", "resolution": "accept"})
    e2 = session.append_event(sid, {"type": "revert"})

    assert e1["seq"] == 1 and e2["seq"] == 2  # 1-based sequence
    assert "ts" in e1                          # stamped timestamp
    assert e1["flag_id"] == "a"

    events = session.read_events(sid)          # re-read from disk (fresh)
    assert [e["type"] for e in events] == ["decision", "revert"]
    assert [e["seq"] for e in events] == [1, 2]


def test_log_is_append_only(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSIONS_DIR", str(tmp_path))
    sid = "sess2"
    for i in range(3):
        session.append_event(sid, {"type": "decision", "flag_id": f"f{i}", "resolution": "waive"})
    assert len(session.read_events(sid)) == 3


# --- resume support: hash lookup + session listing ---

def test_find_session_by_hash_matches_only_stored_sha(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSIONS_DIR", str(tmp_path))
    session.write_proposals("a", {"raw_name": "x.xlsx", "sha": "deadbeef"})
    session.write_proposals("b", {"raw_name": "y.xlsx"})        # legacy: no sha
    assert session.find_session_by_hash("deadbeef") == "a"
    assert session.find_session_by_hash("nope") is None         # unknown hash -> no match


def test_list_sessions_orders_by_recent_activity(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SESSIONS_DIR", str(tmp_path))
    session.write_proposals("old", {"raw_name": "old.xlsx", "counts": {"person_days": 5}})
    session.write_proposals("new", {"raw_name": "new.xlsx", "counts": {"person_days": 9}})
    session.append_event("new", {"type": "decision", "flag_id": "f", "resolution": "accept"})

    listed = session.list_sessions()
    assert [s["sid"] for s in listed] == ["new", "old"]         # logged session sorts first
    top = listed[0]
    assert top["raw_name"] == "new.xlsx" and top["n_events"] == 1
    assert top["counts"]["person_days"] == 9 and "modified" in top
    assert "_mtime" not in top                                   # internal sort key stripped


def test_content_sha_is_stable_and_distinct(tmp_path):
    assert session.content_sha(b"abc") == session.content_sha(b"abc")
    assert session.content_sha(b"abc") != session.content_sha(b"abd")
