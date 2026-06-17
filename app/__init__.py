"""Thin Flask shell over the pure `engine`.

This layer owns HTTP, session files, the decision log, live state, and metrics.
It depends on `engine`; `engine` never depends on `app`.
"""
