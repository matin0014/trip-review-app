# Trip Review Tool

A desktop tool for reviewing and cleaning smartphone GPS trip-survey data.
An engine flags likely problems (fragmented trips, missing trips, wrong
modes, no-movement "trips"), and a reviewer accepts, waives, or manually
fixes each one in a map-based UI. Runs entirely on the reviewer's machine
as a local Flask app in a native window.
## Architecture

- **`engine/`** — pure Python cleaning engine (no Flask, no I/O). One
  responsibility per module, strict one-way dependency flow:

  ```
  params / models / geo / timeparse
      -> normalize / routes
      -> loader            (the only xlsx/pandas module)
      -> velocity
      -> classify
      -> ids / proposals
  ```

- **`app/`** — thin Flask shell: HTTP endpoints, session files, decision-log
  replay, live state, distribution charts/PDFs. Depends on `engine`, never
  the reverse.
- **`analysis/`** — offline QC scripts (distribution charts, wave rebuilds).
  Dev-only; not bundled into the shipped app.
- **`tests/`** — `unit/` (pure functions) and `integration/` (real data
  files); fixtures in `tests/fixtures/`.

## Development

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
pip install -r requirements.txt
pytest                        # all tests
pytest -m "not integration"   # unit only
```

Build the packaged desktop app:

```bash
.\build.ps1                   # -> dist\TripReview.zip
```

## Flags

Trip speed is the median speed between GPS points, ignoring anything under
2 km/h or over 150 km/h (GPS glitch).

**Gap between two trips (A then B):**

```
gap distance   = distance from A's end point to B's start point
gap time       = minutes from A's end to B's start
expected time  = gap distance ÷ A's speed
buffer         = 10% of expected time + 3 min
residual       = gap time - expected time   (unexplained time)
```

**Decision:**

```
gap distance < 200m?
├─ yes, gap time < 3 min  -> FRAGMENT
├─ yes, otherwise         -> no flag
└─ no
   ├─ residual < buffer         -> FRAGMENT  (unless speeds don't match -> MODE CHANGE)
   ├─ residual < buffer + 20min -> MISSING TRIP
   └─ otherwise                 -> MISSING ACTIVITY
```

**Per trip, on its own:** start and end within 200m, and speed ≤ 10 km/h -> ZERO MOVE

| Flag | Meaning |
|---|---|
| Fragment | Two logged trips that are really one continuous journey. |
| Mode change | Looks mergeable, but the speed before/after doesn't match — needs a human to confirm. |
| Missing trip | A modest gap of unexplained time — probably a short unlogged trip. |
| Missing activity | A large gap of unexplained time — probably an unlogged activity. |
| Zero move | The "trip" never actually went anywhere. |

## API

All endpoints are under `/api/<sid>/...` for a specific review session,
except uploading and the two standalone distributions routes. All request/
response bodies are JSON unless noted.

**Session lifecycle**

| Method & path | Does |
|---|---|
| `GET /` | Landing page (upload + resume). |
| `POST /upload` | Upload a raw `.xlsx`, classify it, start a session. |
| `GET /api/sessions` | List resumable sessions. |
| `GET /s/<sid>` | The review UI for one session. |
| `POST /api/<sid>/delete` | Permanently delete a session. |

**Reading review state**

| Method & path | Does |
|---|---|
| `GET /api/<sid>/days` | Every person-day: counts, pending flags, region status. |
| `GET /api/<sid>/day` | One day's raw proposal data (`?key=`). |
| `GET /api/<sid>/live-day` | One day's LIVE state (flags + trips after fixpoint). |
| `GET /api/<sid>/state` | Currently-accepted flag ids. |
| `GET /api/<sid>/start_times` | Raw-vs-cleaned start-time distribution (for the in-app chart). |
| `GET /api/route_estimate` | Suggested duration/route for a manually-added trip. |

**Decisions & manual edits**

| Method & path | Does |
|---|---|
| `POST /api/<sid>/decision` | Accept / waive / clear a flag. |
| `POST /api/<sid>/manual_delete` | Delete one trip. |
| `POST /api/<sid>/remove_no_purpose` | Bulk-delete a day's no-purpose trips. |
| `POST /api/<sid>/manual_mode` | Change a trip's mode. |
| `POST /api/<sid>/manual_purpose` | Change a trip's purpose. |
| `POST /api/<sid>/manual_merge` | Merge trips by hand. |
| `POST /api/<sid>/manual_split` | Split a trip in two. |
| `POST /api/<sid>/manual_add` | Add a trip that isn't in the raw data. |
| `POST /api/<sid>/manual_time` | Edit a trip's start/end time. |
| `POST /api/<sid>/manual_reposition` | Move a trip's start/end point on the map. |
| `POST /api/<sid>/day_include` | Include/exclude a day from the export. |
| `POST /api/<sid>/day_cleaned` | Mark a day as reviewed. |
| `POST /api/<sid>/comment` | Set a trip's note. |

**Undo / redo**

| Method & path | Does |
|---|---|
| `POST /api/<sid>/undo` | Undo the last action. |
| `POST /api/<sid>/redo` | Redo. |
| `POST /api/<sid>/undo_event` | Undo one specific past action. |

**Export & distributions**

| Method & path | Does |
|---|---|
| `GET /api/<sid>/generate` | Download `cleaned.xlsx` + `dofile.xlsx` as a zip. |
| `GET /api/<sid>/distributions` | Download the 8-chart distributions PDF for this session's live state. |
| `POST /api/distributions/from_file` | Build + download a distributions PDF from any loaded `cleaned.xlsx` (no session). |
| `POST /api/distributions/from_file/build` | Same, but returns a server-side path (used by the desktop app's Save-As flow). |
