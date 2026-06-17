"""Precision metrics.

Responsibility: TP (accept) / FP (waive) counts -> precision, for the three
scored detectors only (zero_move, fragment, missing_trip), bucketing
`origin=cascade` separately from `origin=ingest`. No recall/TN/FN.

TODO (Step 7): implement precision aggregation.
"""

from __future__ import annotations
