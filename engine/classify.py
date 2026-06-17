"""The locked classification rule.

Responsibility: per-consecutive-pair classification (gate / residual / symmetric
mode-change guard / buffer+20min) and the per-trip zero_move rule, emitting
`Flag`s. Pure; depends on `velocity`, `geo`, `models`, `params`.

TODO (Step 2): implement `classify_pair(...)` and `classify_trip_zero_move(...)`.
"""

from __future__ import annotations
