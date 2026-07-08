# Network distributions dashboard (before vs after cleaning)

Dev-only QC deliverable. Generates one self-contained `dashboard.html` comparing the
survey network BEFORE cleaning (raw workbook) and AFTER cleaning (every proposal
auto-accepted via the engine, then out-of-region person-days dropped, same as the
export). Not part of the shipped app.

## Run

```
python -m analysis.dashboard.build [raw_xlsx]      # from the project root
```

Defaults to `data/app_data_wave2_raw.xlsx`; writes `analysis/dashboard/output/dashboard.html`.

## Layout

| file | responsibility |
|---|---|
| `pipeline.py` | load raw, derive cleaned, assign trips/people to a region -> `Dataset` |
| `indicators.py` | the nine indicators, each computed raw vs cleaned (+ per region) |
| `summary.py` | header KPI tiles + regional overview cards |
| `render.py` | inject the numbers + vendored Chart.js into `template.html` |
| `template.html` | standalone page shell (KPI tiles, region cards, one card per indicator) |
| `build.py` | entry point wiring pipeline -> indicators -> render -> file |
| `vendor/` | vendored Chart.js (inlined into the output; see vendor/README.md) |
| `output/` | generated `dashboard.html` (gitignored) |

## Indicators (mirrors the reference dashboard)

1. Trips per person per day (target ~4)
2. Mode share
3. Trip start time by hour
4. Trip purpose & mode coverage
5. Trip duration distribution
6. Activity duration by purpose
7. Weekday coverage distribution
8. User completion rate by days
9. Trip purpose distribution by hour

Each is reported twice, before and after cleaning, with a per-region split where the
reference dashboard has one.

## Status

Skeleton only: module structure, signatures, and the indicator registry are in place;
the computations, rendering, and page JS are stubbed (`NotImplementedError` / TODO).
