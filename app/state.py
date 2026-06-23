"""In-memory live state per session.

Responsibility: hold the loaded `Trip` objects (Flask process memory) so the live
recompute does not re-read the workbook on every request. The baseline served to
the client is JSON, but `decisions.fixpoint` needs real `Trip`s (routes + speeds)
to re-classify — so we load + group once per session here and cache it. No
persistence (that is `session`); the cache rebuilds from the saved workbook on a
cold process.
"""

from __future__ import annotations

import os

from app import session
from engine.classify import group_person_days
from engine.loader import load_trips

_cache: dict = {}  # sid -> {"<person>|<date>": [Trip, ...]}


def _load(sid: str) -> dict:
    raw_name = session.read_proposals(sid)["raw_name"]
    path = os.path.join(session.session_dir(sid), raw_name)
    days: dict = {}
    for (person, date), day_trips in group_person_days(load_trips(path)).items():
        days[f"{person}|{date}"] = day_trips  # same key scheme as baseline.build_baseline
    return days


def day_trips(sid: str, key: str) -> "list | None":
    """The `Trip` objects for one person-day (None if the key is unknown)."""
    if sid not in _cache:
        _cache[sid] = _load(sid)
    return _cache[sid].get(key)


def clear(sid: str = None) -> None:
    """Drop cached trips (one session, or all). Used by tests / on reload."""
    if sid is None:
        _cache.clear()
    else:
        _cache.pop(sid, None)
