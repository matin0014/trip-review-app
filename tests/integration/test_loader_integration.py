"""Integration tests against real raw data files.

Placeholder until the loader lands (Step 1b). Run only when explicitly selected:

    pytest -m integration
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skip(reason="loader not implemented yet (Step 1b)")
def test_loads_wave2_raw() -> None:
    # TODO: load data/app_data_wave2_raw.xlsx and assert trips/persons parse.
    ...
