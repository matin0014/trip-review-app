"""Mode normalization and manual-trip detection.

Responsibility: normalize `mode_of_travel` strings (strip zero-width chars, case)
and derive `is_manual` from `purpose_of_travel`. Pure; no I/O.

TODO (Step 1b): implement `normalize_mode(raw: str) -> str` and
`is_manual(purpose: str) -> bool`.
"""

from __future__ import annotations
