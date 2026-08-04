from __future__ import annotations

import pytest

from scripts.ci.check_current_test_system import (
    _root_conftest_contract,
    _test_body_contract,
    _test_budget_contract,
    _workflow_contract,
)
from scripts.ci.select_test_scope import load_inventory


pytestmark = pytest.mark.contract


def test_compact_suite_stays_within_file_and_line_budgets() -> None:
    assert _test_budget_contract(load_inventory()) == []


def test_root_fixture_and_test_bodies_remain_lightweight_and_unique() -> None:
    assert _root_conftest_contract() == []
    assert _test_body_contract() == []


def test_full_regression_has_no_schedule_and_selector_outputs_are_fixed() -> None:
    assert _workflow_contract() == []
