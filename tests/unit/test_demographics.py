"""Unit tests for participant demographic extraction."""

from __future__ import annotations

import pandas as pd

from app.demographics import extract_participant_info


def test_extract_participant_info_first_non_empty_by_access_code(tmp_path):
    path = tmp_path / "demo.xlsx"
    pd.DataFrame([
        {
            "accessCode": "A1",
            "workArrangements": "Hybrid",
            "numberofVehicle": 2.0,
            "age": 35,
            "gender": "Woman",
            "ownershipstatus": "Own",
            "occupation": "",
            "employment status": "Employed",
        },
        {
            "accessCode": "A1",
            "workArrangements": "Remote",
            "numberofVehicle": 3,
            "age": 36,
            "gender": "Woman",
            "ownershipstatus": "Own",
            "occupation": "Engineer",
            "employment status": "Employed",
        },
    ]).to_excel(path, index=False)

    info = extract_participant_info(str(path))
    rows = {r["key"]: r["value"] for r in info["A1"]}

    assert rows["workArrangements"] == "Hybrid"
    assert rows["numberofVehicle"] == "2"
    assert rows["age"] == "35"
    assert rows["gender"] == "Woman"
    assert rows["ownershipstatus"] == "Own"
    assert rows["occupation"] == "Engineer"
    assert rows["employment_status"] == "Employed"


def test_extract_participant_info_missing_demographic_columns_returns_empty(tmp_path):
    path = tmp_path / "raw.xlsx"
    pd.DataFrame([{"accessCode": "A1", "start_time": "09:00"}]).to_excel(path, index=False)

    assert extract_participant_info(str(path)) == {}


def test_extract_participant_info_uses_person_id_member_zero(tmp_path):
    path = tmp_path / "person_id.xlsx"
    pd.DataFrame([
        {"accessCode": "A1", "person_id": "A1_1", "age": 44, "gender": "Other"},
        {"accessCode": "A1", "person_id": "A1_0", "age": 31, "gender": "Woman"},
        {"accessCode": "A1", "person_id": "A1_0", "age": 32, "gender": "Woman"},
    ]).to_excel(path, index=False)

    info = extract_participant_info(str(path))
    rows = {r["key"]: r["value"] for r in info["A1"]}

    assert rows["age"] == "31"
    assert rows["gender"] == "Woman"


def test_extract_participant_info_falls_back_to_smallest_member_number(tmp_path):
    path = tmp_path / "member.xlsx"
    pd.DataFrame([
        {"accessCode": "A1", "member": 3, "ownerStatus": "Rent"},
        {"accessCode": "A1", "member": 2, "ownerStatus": "Own"},
        {"accessCode": "A1", "member": 4, "ownerStatus": "Other"},
    ]).to_excel(path, index=False)

    info = extract_participant_info(str(path))
    rows = {r["key"]: r["value"] for r in info["A1"]}

    assert rows["ownershipstatus"] == "Own"


def test_extract_participant_info_takes_first_pipe_value(tmp_path):
    path = tmp_path / "pipe.xlsx"
    pd.DataFrame([{"accessCode": "A1", "age": "45 | 46", "gender": "Woman | Man"}]).to_excel(
        path, index=False)

    info = extract_participant_info(str(path))
    rows = {r["key"]: r["value"] for r in info["A1"]}

    assert rows["age"] == "45"
    assert rows["gender"] == "Woman"
