"""Robust timestamp parsing.

Responsibility: turn the raw file's time strings (microseconds + timezone
offsets, mixed formats) into aware `datetime` objects. Pure; depends only on the
standard library.

TODO (Step 1b): implement `parse_ts(raw: str) -> datetime`.
"""

from __future__ import annotations
