"""Turn the computed numbers into a single self-contained dashboard.html.

Takes the summary + indicator dicts, serialises them to JSON, and injects that plus
the vendored Chart.js into `template.html`, producing ONE standalone file (no server,
no external requests) that opens or prints anywhere. All chart building happens in
the page's own JS (see the template); this module only fills the slots.
"""

from __future__ import annotations

import os

TEMPLATE = os.path.join(os.path.dirname(__file__), "template.html")
# Vendored Chart.js, inlined into the output so the file is self-contained/offline.
VENDOR_CHART_JS = os.path.join(os.path.dirname(__file__), "vendor", "chart.umd.js")


def _read_chart_js() -> str:
    """Return the vendored Chart.js source to inline, or a clear placeholder comment
    when it has not been added yet (so the skeleton still renders)."""
    raise NotImplementedError


def render_html(summary: dict, indicators: dict, template_path: str = TEMPLATE) -> str:
    """Fill `template_path` with the JSON payload (summary + indicators) and the
    inlined Chart.js, returning the complete HTML document as a string."""
    raise NotImplementedError


def write_dashboard(html: str, out_path: str) -> None:
    """Write the rendered HTML atomically (temp file + replace), creating parent dirs."""
    raise NotImplementedError
