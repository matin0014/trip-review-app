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
