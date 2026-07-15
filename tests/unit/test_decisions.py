"""Unit tests for `app.decisions.replay` — the append-only log -> accepted_set."""

from __future__ import annotations

from app.decisions import manual_ops, replay


def dec(flag_id, resolution):
    return {"type": "decision", "flag_id": flag_id, "resolution": resolution}


REVERT = {"type": "revert"}
REDO = {"type": "redo"}


def test_empty_log_accepts_nothing():
    assert replay([]) == set()


def test_accept_applies_waive_does_not():
    assert replay([dec("a", "accept"), dec("b", "waive")]) == {"a"}


def test_all_apply_resolutions_count():
    events = [dec("a", "accept"), dec("b", "merge"), dec("c", "reviewed"), dec("d", "waive")]
    assert replay(events) == {"a", "b", "c"}


def test_last_event_wins_per_flag():
    assert replay([dec("a", "accept"), dec("a", "waive")]) == set()
    assert replay([dec("a", "waive"), dec("a", "accept")]) == {"a"}


def test_revert_undoes_last_decision():
    assert replay([dec("a", "accept"), REVERT]) == set()


def test_revert_restores_prior_decision_on_same_flag():
    # accept -> waive -> revert should fall back to the earlier accept
    assert replay([dec("a", "accept"), dec("a", "waive"), REVERT]) == {"a"}


def test_redo_reapplies_reverted_decision():
    assert replay([dec("a", "accept"), REVERT, REDO]) == {"a"}


def test_new_decision_clears_redo_stack():
    # after reverting, a fresh decision means the old redo is no longer available
    events = [dec("a", "accept"), REVERT, dec("b", "accept"), REDO]
    assert replay(events) == {"b"}


def test_revert_on_empty_is_noop():
    assert replay([REVERT, REDO]) == set()


def test_redo_without_revert_is_noop():
    assert replay([dec("a", "accept"), REDO]) == {"a"}


def test_clear_undecides_a_flag():
    assert replay([dec("a", "accept"), dec("a", "clear")]) == set()
    assert replay([dec("a", "waive"), dec("a", "clear")]) == set()


def test_revert_a_clear_restores_the_decision():
    # accept -> clear -> revert(the clear) should bring the accept back
    assert replay([dec("a", "accept"), dec("a", "clear"), REVERT]) == {"a"}


def test_targeted_undo_removes_one_decision_by_serial():
    events = [
        {"seq": 1, **dec("a", "accept")},
        {"seq": 2, **dec("b", "accept")},
        {"type": "undo_event", "target_seq": 1},
    ]

    assert replay(events) == {"b"}


def test_targeted_undo_removes_one_manual_edit_by_serial():
    events = [
        {"seq": 1, "type": "manual_mode", "raw_row": 1, "mode": "Walk", "day_key": "p|2026-01-01"},
        {"seq": 2, "type": "manual_delete", "raw_row": 2, "day_key": "p|2026-01-01"},
        {"type": "undo_event", "target_seq": 2},
    ]

    assert [e["seq"] for e in manual_ops(events)] == [1]
