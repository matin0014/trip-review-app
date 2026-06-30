"""Extract participant-level demographic fields from an uploaded workbook."""

from __future__ import annotations

import math
import numbers
import re

import pandas as pd

FIELDS = (
    ("workArrangements", "Work arrangements",
     ("workArrangements", "workArrangement", "work arrangements", "work arrangement",
      "work_arrangements", "work_arrangement")),
    ("numberofVehicle", "Number of vehicles",
     ("numberofVehicle", "numberOfVehicles", "number of vehicle", "number of vehicles",
      "number_of_vehicle", "number_of_vehicles")),
    ("age", "Age", ("age",)),
    ("gender", "Gender", ("gender",)),
    ("ownershipstatus", "Ownership status",
     ("ownershipstatus", "ownerStatus", "ownership status", "owner status",
      "ownership_status", "owner_status")),
    ("occupation", "Occupation", ("occupation", "occupationCategory", "occupation category")),
    ("employment_status", "Employment status",
     ("employment status", "employmentStatus", "employment_status", "employmentstatus")),
)


def extract_participant_info(path: str) -> dict:
    """Return {accessCode: [{label, value}, ...]} from repeated trip rows.

    Socio-demographic values can repeat across trip rows and household members.
    For each accessCode, use member 0 when a ``member`` or ``person_id`` column is
    available. If member 0 is absent, use the smallest member number. Missing
    member fields are harmless; raw trip-only uploads keep the previous behavior.
    """
    df = pd.read_excel(path)
    if "accessCode" not in df.columns:
        return {}

    columns = {_norm(c): c for c in df.columns}
    matched = [
        (key, label, columns.get(_norm(alias)))
        for key, label, aliases in FIELDS
        for alias in aliases
        if _norm(alias) in columns
    ]
    matched = _dedupe_matches(matched)
    if not matched:
        return {}

    records_by_person: dict = {}
    for row in df.to_dict("records"):
        person = _person_text(row.get("accessCode")) or "(unknown)"
        records_by_person.setdefault(person, []).append(row)

    values_by_person: dict = {}
    for person, records in records_by_person.items():
        member = _selected_member(records, columns)
        values = {key: "" for key, _label, _col in matched}
        for row in records:
            if member is not None and _member_number(row, columns) != member:
                continue
            _fill_values(values, row, matched)
        values_by_person[person] = values

    return {
        person: [
            {"key": key, "label": label, "value": values.get(key, "")}
            for key, label, _col in matched
        ]
        for person, values in values_by_person.items()
    }


def _fill_values(values: dict, row: dict, matched: list) -> None:
    for key, _label, col in matched:
        if values.get(key):
            continue
        value = _value_text(row.get(col))
        if value:
            values[key] = value


def _dedupe_matches(matches: list) -> list:
    out = []
    seen = set()
    for key, label, col in matches:
        if key in seen:
            continue
        seen.add(key)
        out.append((key, label, col))
    return out


def _norm(value: object) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def _selected_member(records: list, columns: dict) -> "int | None":
    members = [_member_number(row, columns) for row in records]
    members = [m for m in members if m is not None]
    if not members:
        return None
    return 0 if 0 in members else min(members)


def _member_number(row: dict, columns: dict) -> "int | None":
    member_col = _column(columns, ("member",))
    if member_col is not None:
        parsed = _parse_member(row.get(member_col), suffix_only=False)
        if parsed is not None:
            return parsed

    person_id_col = _column(columns, ("person_id", "personId", "person id"))
    if person_id_col is not None:
        return _parse_member(row.get(person_id_col), suffix_only=True)
    return None


def _column(columns: dict, aliases: tuple) -> "str | None":
    for alias in aliases:
        col = columns.get(_norm(alias))
        if col is not None:
            return col
    return None


def _parse_member(value: object, suffix_only: bool) -> "int | None":
    if value is None:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, numbers.Number) and not isinstance(value, bool):
        return int(float(value))
    text = str(value).strip()
    if not text:
        return None
    if not suffix_only:
        try:
            return int(float(text))
        except ValueError:
            pass
    match = re.search(r"_(\d+)\s*$", text) if suffix_only else re.search(r"(\d+)\s*$", text)
    return int(match.group(1)) if match else None


def _person_text(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def _value_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, numbers.Number) and not isinstance(value, bool):
        value = float(value)
        return str(int(value)) if value.is_integer() else str(value)
    text = str(value).strip()
    if text.lower() == "nan":
        return ""
    return text.split("|", 1)[0].strip()
