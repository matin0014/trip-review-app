"""Raw and cleaned trip datasets + region assignment for the dashboard.

Loads the raw survey workbook and derives the CLEANED dataset exactly as the
distributions deliverable / export does: every proposal auto-accepted via the
engine's ``apply_all``, THEN out-of-region person-days dropped. That gives each
indicator two trip lists to run over: BEFORE cleaning (raw) and AFTER (cleaned).

Also assigns each trip and person to a study region (Vancouver / Okanagan) using
the locked region boxes in ``app.regions``, for the per-region panels.

Pure over the engine (no Flask, no charts). Mirrors the raw-vs-cleaned pipeline in
``analysis/distributions.py`` so the two deliverables never disagree on "cleaned".
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# NOTE: when run as ``python -m analysis.dashboard.build`` the project root is on the
# path already; a standalone entry point (build.py) inserts it just in case.
from app import regions
from engine.classify import classify_all, group_person_days
from engine.loader import load_trips
from engine.proposals import apply_all

DEFAULT_RAW = os.path.join("data", "app_data_wave2_raw.xlsx")

# Region labels used across every per-region panel (order = display order).
REGIONS = ("Vancouver", "Okanagan")


@dataclass
class Dataset:
    """Everything the indicators need: the two trip lists plus region lookups.

    ``raw`` = untouched trips; ``cleaned`` = auto-accepted + out-of-region days
    dropped. ``person_region`` / ``trip_region`` map to a label in ``REGIONS`` (or
    ``None`` when a trip/person can't be placed). Built once by ``build_dataset``.
    """

    raw: list
    cleaned: list
    person_region: dict = field(default_factory=dict)
    out_of_region_keys: set = field(default_factory=set)


def load_raw(path: str = DEFAULT_RAW) -> list:
    """Load the raw survey workbook into engine ``Trip``s."""
    raise NotImplementedError


def build_cleaned(raw: list) -> list:
    """The cleaned trip list: ``apply_all(raw, classify_all(raw))`` then drop the
    out-of-region person-days (same rule as the export). See distributions.py."""
    raise NotImplementedError


def out_of_region_keys(trips: list) -> set:
    """Person-day keys (``"person|date"``) with any trip endpoint outside every
    locked region box, i.e. the days the export auto-removes."""
    raise NotImplementedError


def region_of_trip(t) -> "str | None":
    """The study region (a ``REGIONS`` label) whose box contains the trip, else None."""
    raise NotImplementedError


def region_of_person(trips: list) -> dict:
    """Map ``person -> region`` label by their trips (majority / home endpoint).
    Persons with no placeable trips map to None."""
    raise NotImplementedError


def build_dataset(path: str = DEFAULT_RAW) -> Dataset:
    """Load raw, derive cleaned, and assemble the region lookups into a `Dataset`."""
    raise NotImplementedError
