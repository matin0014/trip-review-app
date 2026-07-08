"""Before/after-cleaning network distributions dashboard (QC deliverable).

A dev-only static generator: it loads the raw survey workbook, derives the cleaned
dataset the same way the export does, computes a set of network indicators for BOTH
(before cleaning = raw, after cleaning = cleaned), and writes one self-contained
``dashboard.html`` you can open or print. Not part of the shipped app.

Run it with ``python -m analysis.dashboard.build [raw_xlsx]`` (see build.py).
"""
