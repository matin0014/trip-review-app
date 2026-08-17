# Trip Review Tool

Interactive tool for data cleaners to review and approve trip-cleaning proposals
on smartphone GPS trip data. Local Flask app + Leaflet viewer, shipped as a
source-protected PyInstaller executable; data never leaves the reviewer's machine.

## Architecture

- **`engine/`** — pure Python cleaning engine (no Flask, no global state). One
  responsibility per module; strict one-way dependency flow:

  ```
  params / models / geo / timeparse
      -> normalize / routes
      -> loader            (the only xlsx/pandas module)
      -> velocity
      -> classify
      -> ids / proposals
  ```

- **`app/`** — thin Flask shell (endpoints, session files, decision-log replay,
  live state, metrics). Depends on `engine`; never the reverse.
- **`tests/`** — `unit/` (pure functions) and `integration/` (real data files);
  fixtures in `tests/fixtures/`.

## Development

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
pip install -r requirements.txt
pytest                        # all tests
pytest -m "not integration"   # unit only
```

## Status

Step 1a (scaffold + pure foundation: `params`, `models`, `geo`) — in progress.
See the plan for the full design and build sequence.

## Flags

**Trip speed** — median speed between GPS points, ignoring anything under 2 km/h
(standing still) or over 150 km/h (GPS glitch).

**Gap between two trips (A then B):**

```
gap distance   = distance from A's end point to B's start point
gap time       = minutes from A's end to B's start
expected time  = gap distance ÷ A's speed
buffer         = 10% of expected time + 3 min
residual       = gap time − expected time   (unexplained time)
```

**Decision:**

```
gap distance < 200m?
├─ yes, gap time < 3 min  → FRAGMENT
├─ yes, otherwise         → no flag
└─ no
   ├─ residual < buffer         → FRAGMENT  (unless speeds don't match → MODE CHANGE)
   ├─ residual < buffer + 20min → MISSING TRIP
   └─ otherwise                 → MISSING ACTIVITY
```

**Per trip, on its own:** start and end within 200m, and speed ≤ 10 km/h → **ZERO MOVE**

| Flag | Meaning |
|---|---|
| Fragment | one trip, logged as two |
| Mode change | looks mergeable, but speed doesn't match — needs a human |
| Missing trip | a bit of unexplained time — probably an unlogged trip |
| Missing activity | a lot of unexplained time — probably an unlogged activity |
| Zero move | never actually moved |
