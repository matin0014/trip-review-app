"""Unit tests for `engine.normalize` — mode cleanup + is_manual."""

from __future__ import annotations

import math

import pytest

from engine.normalize import UNKNOWN_MODE, is_manual, normalize_mode


def test_strips_trailing_zero_width() -> None:
    # The exact dirty values seen in the raw file.
    assert normalize_mode("Auto Driver​") == "Auto Driver"
    assert normalize_mode("Walk​​") == "Walk"
    assert normalize_mode("Transit Bus​​") == "Transit Bus"


def test_clean_value_unchanged() -> None:
    assert normalize_mode("Bike") == "Bike"
    assert normalize_mode("Auto Passenger") == "Auto Passenger"


def test_dirty_and_clean_collapse_equal() -> None:
    assert normalize_mode("Walk​​") == normalize_mode("Walk")


def test_whitespace_collapsed_and_trimmed() -> None:
    assert normalize_mode("  Auto   Driver  ") == "Auto Driver"


@pytest.mark.parametrize("missing", [None, float("nan"), "", "   ", "​"])
def test_missing_mode_becomes_unknown(missing: object) -> None:
    assert normalize_mode(missing) == UNKNOWN_MODE


def test_is_manual_true_when_purpose_present() -> None:
    assert is_manual("Work") is True
    assert is_manual("Shopping") is True


@pytest.mark.parametrize("missing", [None, float("nan"), "", "   ", "​"])
def test_is_manual_false_when_purpose_missing(missing: object) -> None:
    assert is_manual(missing) is False


def test_nan_handled_without_error() -> None:
    assert math.isnan(float("nan"))  # sanity: the value really is NaN
    assert normalize_mode(float("nan")) == UNKNOWN_MODE
    assert is_manual(float("nan")) is False
