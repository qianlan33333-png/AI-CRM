from __future__ import annotations

from pathlib import Path

from tools.check_domain_migration_contract import validate_domain_migration_contract
from tools.check_legacy_cleanup_contract import validate_legacy_cleanup_contract


ROOT = Path(__file__).resolve().parents[1]


def test_domain_migration_contract_preserves_logical_targets_without_premature_move() -> None:
    assert validate_domain_migration_contract(ROOT) == []


def test_legacy_cleanup_contract_has_no_ungated_table_drop() -> None:
    assert validate_legacy_cleanup_contract(ROOT) == []
