"""Unit tests for `engine.ids` — content-derived flag ids + trip_id grammar."""

from __future__ import annotations

import pytest

from engine.ids import flag_id, project_trip_id, with_ids
from engine.models import Flag


def frag(left, right) -> Flag:
    return Flag(flag_type="fragment", left=left, right=right, group=min(left + right))


def test_flag_id_is_deterministic() -> None:
    assert flag_id(frag((1,), (2,))) == flag_id(frag((1,), (2,)))


def test_flag_id_depends_on_type() -> None:
    a = Flag(flag_type="fragment", left=(1,), right=(2,))
    b = Flag(flag_type="missing_trip", left=(1,), right=(2,))
    assert flag_id(a) != flag_id(b)


def test_flag_id_depends_on_members_and_direction() -> None:
    assert flag_id(frag((1,), (2,))) != flag_id(frag((1,), (3,)))
    # left/right are not interchangeable (prev vs cur leg).
    assert flag_id(frag((1,), (2,))) != flag_id(frag((2,), (1,)))


def test_flag_id_stable_under_member_set_order() -> None:
    # Same leg members in a different order hash the same (sorted internally).
    assert flag_id(frag((1, 2), (3,))) == flag_id(frag((2, 1), (3,)))


def test_zero_move_id_keys_on_trip() -> None:
    a = Flag(flag_type="zero_move", trip=5)
    b = Flag(flag_type="zero_move", trip=6)
    assert flag_id(a) != flag_id(b)
    assert flag_id(a) == flag_id(Flag(flag_type="zero_move", trip=5))


def test_with_ids_backfills_all() -> None:
    flags = [frag((1,), (2,)), Flag(flag_type="zero_move", trip=5)]
    out = with_ids(flags)
    assert all(f.flag_id is not None for f in out)
    assert out[0].flag_id == flag_id(flags[0])


def test_trip_id_grammar() -> None:
    assert project_trip_id("keep", raw_row=143) == "143"
    assert project_trip_id("merge", group=143) == "M143"
    assert project_trip_id("add", add_counter=7) == "A07"
    assert project_trip_id("remove", raw_row=145) == "R145"
    assert project_trip_id("review", raw_row=145) == "Q145"
    assert project_trip_id("split", raw_row=150) == "S150"


def test_trip_id_unknown_kind_raises() -> None:
    with pytest.raises(ValueError):
        project_trip_id("frobnicate", raw_row=1)
