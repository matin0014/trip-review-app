"""Raw xlsx -> list[Trip].

Responsibility: THE single file that touches pandas/openpyxl. Reads a raw trip
workbook, applies `timeparse` / `normalize` / `routes`, and yields fully-built
`Trip` objects grouped by person/day. Keeping all spreadsheet I/O here means the
rest of the engine is pure and unit-testable without a file.

TODO (Step 1b): implement `load_trips(path) -> list[Trip]`.
"""

from __future__ import annotations
