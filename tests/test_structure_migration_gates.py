from __future__ import annotations

from pathlib import Path

import yaml

from tools.check_domain_migration_contract import _cyclic_domains, validate_domain_migration_contract
from tools.check_legacy_cleanup_contract import validate_legacy_cleanup_contract


ROOT = Path(__file__).resolve().parents[1]


def test_domain_migration_contract_preserves_logical_targets_during_physical_move() -> None:
    assert validate_domain_migration_contract(ROOT) == []


def test_crm_and_extensions_are_completed_physical_domains() -> None:
    policy = yaml.safe_load((ROOT / "docs/architecture/domain_migration_policy.yml").read_text(encoding="utf-8"))
    physical = policy["physical_moves"]

    assert physical["enabled"] is True
    assert physical["completed_domains"] == ["crm", "extensions"]
    assert physical["pending_domains"] == [
        "app",
        "platform",
        "channels",
        "engagement",
        "automation",
        "insights",
    ]


def test_domain_dependency_direction_has_one_explicit_acyclic_policy() -> None:
    policy = yaml.safe_load((ROOT / "docs/architecture/domain_migration_policy.yml").read_text(encoding="utf-8"))
    allowed = policy["allowed_domain_dependencies"]

    assert list(allowed) == [
        "app",
        "platform",
        "channels",
        "crm",
        "engagement",
        "automation",
        "insights",
        "extensions",
    ]
    assert allowed["platform"] == []
    assert "extensions" not in allowed["channels"]
    assert "extensions" not in allowed["crm"]
    assert "extensions" not in allowed["engagement"]
    assert "extensions" not in allowed["automation"]


def test_domain_dependency_cycle_detection_includes_unused_policy_edges() -> None:
    domains = {"app", "platform", "channels"}

    assert _cyclic_domains(domains, {("app", "platform"), ("platform", "app")}) is True
    assert _cyclic_domains(domains, {("app", "channels"), ("channels", "platform")}) is False


def test_legacy_cleanup_contract_has_no_ungated_table_drop() -> None:
    assert validate_legacy_cleanup_contract(ROOT) == []
