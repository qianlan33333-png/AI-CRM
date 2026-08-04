from __future__ import annotations

import pytest

from scripts.ci.check_current_test_system import _inventory_contract, _migration_contract
from scripts.ci.select_test_scope import load_inventory


pytestmark = pytest.mark.contract


def test_current_behavior_inventory_covers_routes_tests_and_side_effects() -> None:
    inventory = load_inventory()
    assert inventory["truth_source"] == "current_ai_crm_next"
    assert _inventory_contract(inventory) == []


def test_current_migration_graph_matches_inventory_head() -> None:
    assert _migration_contract(load_inventory()) == []
