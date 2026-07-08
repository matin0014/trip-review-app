"""Entry point: raw workbook -> before/after indicators -> dashboard.html.

    python -m analysis.dashboard.build [raw_xlsx]        (from the project root)

Wires the pieces together: build the dataset (pipeline), compute the header summary
(summary) and the nine indicators (indicators), render one self-contained HTML file
(render), and write it to output/. Dev-only; not shipped in the app.
"""

from __future__ import annotations

import argparse
import os
import sys

# Allow running as a plain script too: put the project root on the path so the
# ``app`` / ``engine`` imports inside pipeline resolve.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from analysis.dashboard import indicators, pipeline, render, summary  # noqa: E402

OUT_DIR = os.path.join("analysis", "dashboard", "output")
OUT_FILE = os.path.join(OUT_DIR, "dashboard.html")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("raw_xlsx", nargs="?", default=pipeline.DEFAULT_RAW)
    ap.add_argument("-o", "--out", default=OUT_FILE, help="output HTML path")
    args = ap.parse_args()

    ds = pipeline.build_dataset(args.raw_xlsx)
    payload_summary = {
        "header": summary.header_kpis(ds),
        "regions": summary.regional_overview(ds),
    }
    payload_indicators = indicators.compute_all(ds)

    html = render.render_html(payload_summary, payload_indicators)
    render.write_dashboard(html, args.out)
    print(f"wrote {args.out}  (raw {len(ds.raw)} trips -> cleaned {len(ds.cleaned)} trips)")


if __name__ == "__main__":
    main()
