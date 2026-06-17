"""Flags -> proposals + cleaned-file projection.

Responsibility: assemble classifier `Flag`s into the first-class Do-file `flags`
collection (with `pair`, `group`, `evidence`) and the `rows` collection, and
build the cleaned-file row projection at Generate time. Pure; depends on
`classify`, `ids`, `models`.

TODO (Step 2): implement proposal assembly.
"""

from __future__ import annotations
