"""Mode normalization and manual-trip detection.

Responsibility: normalize `mode_of_travel` strings and derive `is_manual` from
`purpose_of_travel`. Pure; no I/O.

The raw `mode_of_travel` carries trailing zero-width characters (e.g.
``'Auto Driver​'``, ``'Walk​​'``) that make otherwise-identical
modes compare unequal. We strip those plus surrounding/duplicated whitespace,
preserving the human-readable label and its case (the classifier keys on speed,
not on this string — it is carried for display and the cleaned-file output).

`is_manual` reflects whether the participant logged the trip's purpose: the app
auto-detects every trip; a purpose is filled in only when the user manually
labels it. So a present purpose => manual; a missing one => automatic.
"""

from __future__ import annotations

import math
import re

# Zero-width / invisible code points seen (or plausibly present) in the raw mode
# strings: ZWSP, ZWNJ, ZWJ, word-joiner, BOM/zero-width no-break space.
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
_WS_RUN = re.compile(r"\s+")

UNKNOWN_MODE = "Unknown"


def normalize_mode(raw: object) -> str:
    """Clean one `mode_of_travel` value to a canonical label.

    Strips zero-width characters and collapses surrounding/internal whitespace.
    A missing value (None / NaN / blank) becomes ``"Unknown"`` (a real mode in
    the data, so it merges naturally with explicitly-unknown rows).
    """
    if not _is_present(raw):
        return UNKNOWN_MODE
    cleaned = str(raw).translate(_ZERO_WIDTH)
    cleaned = _WS_RUN.sub(" ", cleaned).strip()
    return cleaned or UNKNOWN_MODE


def is_manual(purpose: object) -> bool:
    """True when the participant logged a purpose (=> manually labelled trip)."""
    if not _is_present(purpose):
        return False
    return bool(str(purpose).translate(_ZERO_WIDTH).strip())


def _is_present(value: object) -> bool:
    """False for None, NaN, and blank strings; True otherwise."""
    if value is None:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True
