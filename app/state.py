"""In-memory live state per session.

Responsibility: hold the current rebuilt state (Flask process memory = the
"running Do-file") and expose it to the server; rebuilt by `decisions.fixpoint`
on every event. No persistence (that is `session`).

TODO (Step 5): implement the live-state container.
"""

from __future__ import annotations
