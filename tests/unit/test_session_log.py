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
