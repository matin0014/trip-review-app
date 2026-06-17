"""Unit tests for `engine.params` — guards against accidental threshold edits."""

from __future__ import annotations

import dataclasses

import pytest

from engine.params import DEFAULT, Params


def test_default_values_match_locked_rule() -> None:
    assert DEFAULT.glitch_cap_kmh == 150.0
    assert DEFAULT.gate_m == 200.0
    assert DEFAULT.buffer_frac == 0.10
    assert DEFAULT.buffer_const_min == 3.0
    assert DEFAULT.missing_band_min == 20.0
    assert DEFAULT.mode_low_kmh == 12.0
    assert DEFAULT.mode_high_kmh == 25.0
    assert DEFAULT.zero_move_disp_m == 200.0
    assert DEFAULT.zero_move_speed_kmh == 10.0


def test_mode_band_has_dead_zone() -> None:
    # The 12-25 dead zone is deliberate; low must sit strictly below high.
    assert DEFAULT.mode_low_kmh < DEFAULT.mode_high_kmh


def test_params_is_immutable() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT.glitch_cap_kmh = 120.0  # type: ignore[misc]


def test_alternative_params_can_be_constructed() -> None:
    # An experiment can build its own set without touching the production one.
    custom = Params(v_move_min_kmh=5.0)
    assert custom.v_move_min_kmh == 5.0
    assert DEFAULT.v_move_min_kmh == 2.0
