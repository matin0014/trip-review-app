"""Unit tests for `app.decisions.replay` — the append-only log -> accepted_set."""

from __future__ import annotations

from app.decisions import (cleaned_days, day_overrides, excluded_days, manual_ops,
                           replay, trip_comments)


def dec(flag_id, resolution):
    return {"type": "decision", "flag_id": flag_id, "resolution": resolution}


def inc(day_key, include):
    return {"type": "day_include", "day_key": day_key, "include": include}


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


# --- day_include overrides + excluded_days (out-of-region auto-removal) ---

AUTO = {"p|d2", "q|d1"}   # the days the region rule flagged as out-of-region


def test_no_override_auto_out_days_are_excluded():
    assert excluded_days([], AUTO) == AUTO


def test_recover_re_includes_an_auto_out_day():
    # the reviewer recovers q|d1 -> only p|d2 stays excluded
    assert excluded_days([inc("q|d1", True)], AUTO) == {"p|d2"}


def test_hand_remove_excludes_an_in_region_day():
    # p|d1 is in-region (not in AUTO) but the reviewer removed it by hand
    assert excluded_days([inc("p|d1", False)], AUTO) == AUTO | {"p|d1"}


def test_day_override_is_last_event_wins():
    # recover then remove again -> excluded
    assert excluded_days([inc("q|d1", True), inc("q|d1", False)], AUTO) == AUTO
    # remove then recover -> included
    assert excluded_days([inc("q|d1", False), inc("q|d1", True)], AUTO) == {"p|d2"}


def test_day_overrides_ignores_revert_redo():
    # day_include is a sticky setting, OUT of the undo/redo LIFO: a revert must not
    # silently re-add a day the reviewer pulled.
    assert day_overrides([inc("p|d1", False), REVERT]) == {"p|d1": False}


# --- day_cleaned: the reviewer's manual "done" checkbox per day -------------

def cln(day_key, cleaned):
    return {"type": "day_cleaned", "day_key": day_key, "cleaned": cleaned}


def test_no_events_means_no_day_is_cleaned():
    assert cleaned_days([]) == {}


def test_checking_a_day_marks_it_cleaned():
    assert cleaned_days([cln("p|d1", True)]) == {"p|d1": True}


def test_unchecking_is_last_event_wins():
    assert cleaned_days([cln("p|d1", True), cln("p|d1", False)]) == {"p|d1": False}


def test_cleaned_days_ignores_revert_redo():
    # sticky, like day_include: a flag-level revert must not silently un-check
    # a day the reviewer marked done.
    assert cleaned_days([cln("p|d1", True), REVERT]) == {"p|d1": True}


# --- trip_comments: the reviewer's free-text note per trip ------------------

def cmt(day_key, raw_row, text):
    return {"type": "trip_comment", "day_key": day_key, "raw_row": raw_row, "comment": text}


def test_no_events_means_no_comments():
    assert trip_comments([]) == {}


def test_comment_is_keyed_by_day_and_row():
    assert trip_comments([cmt("p|d1", 5, "check this")]) == {("p|d1", 5): "check this"}


def test_last_comment_on_a_row_wins():
    events = [cmt("p|d1", 5, "first"), cmt("p|d1", 5, "second")]
    assert trip_comments(events) == {("p|d1", 5): "second"}


def test_saving_an_empty_comment_clears_it():
    events = [cmt("p|d1", 5, "note"), cmt("p|d1", 5, "   ")]
    assert trip_comments(events) == {}


def test_same_added_row_on_different_days_does_not_collide():
    # a manual add's raw_row is a PER-DAY counter, so -2000001 exists on every day
    # that has an add -- notes must not leak between participants.
    events = [cmt("p1|d1", -2000001, "p1 note"), cmt("p2|d1", -2000001, "p2 note")]
    assert trip_comments(events) == {("p1|d1", -2000001): "p1 note",
                                     ("p2|d1", -2000001): "p2 note"}


def test_comments_ignore_revert_redo():
    # sticky, like day_include: a flag revert must not drop a note.
    assert trip_comments([cmt("p|d1", 5, "note"), REVERT]) == {("p|d1", 5): "note"}


# --- undo_event: targeted undo of one specific edit, by its log serial ------

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


def test_targeted_undo_leaves_a_later_untargeted_edit_active():
    # undoing an OLDER edit must not disturb a later one still in effect --
    # this is the whole point of undo_event over the plain LIFO /undo.
    events = [
        {"seq": 1, "type": "manual_mode", "raw_row": 1, "mode": "Walk", "day_key": "p|2026-01-01"},
        {"seq": 2, "type": "manual_mode", "raw_row": 2, "mode": "Bike", "day_key": "p|2026-01-01"},
        {"type": "undo_event", "target_seq": 1},
    ]
    assert [e["seq"] for e in manual_ops(events)] == [2]


def test_targeted_undo_of_a_missing_seq_is_a_noop():
    events = [
        {"seq": 1, **dec("a", "accept")},
        {"type": "undo_event", "target_seq": 99},
    ]
    assert replay(events) == {"a"}
