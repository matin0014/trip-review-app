"""Decision log replay -> live state (the fixpoint).

Responsibility: turn the append-only event log (decision / manual_edit /
manual_add / revert / redo) into the `accepted_set`, honoring last-event-wins and
the revert/redo stacks, then drive the engine's fixpoint recompute per person-day.
Pure given (baseline, events).

TODO (Step 5): implement `replay(events) -> accepted_set` and `fixpoint(...)`.
"""

from __future__ import annotations
