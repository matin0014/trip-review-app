"""Flask endpoints (thin controllers).

Responsibility: HTTP routing only — upload, fetch day/proposals, post decisions
(accept/waive/merge/undo), rewind/redo, manual edits, generate, metrics,
download. Delegates all logic to `session`, `decisions`, `state`, `metrics`,
`engine`.

TODO (Step 4-7): implement endpoints.
"""

from __future__ import annotations
