"""Per-session storage.

Responsibility: manage `sessions/<id>/` working dirs; read/write the immutable
`proposals.json` baseline and append to `decisions.jsonl`; atomic xlsx exports
(cleaned file, Do-file). No business logic.

TODO (Step 5-6): implement session I/O.
"""

from __future__ import annotations
