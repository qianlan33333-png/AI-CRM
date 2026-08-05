#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.platform.release_governance.manifest import (  # noqa: E402
    data_health_registry_digest,
    load_release_gate_manifest,
)
from aicrm_next.platform.release_governance.migration_compatibility import (  # noqa: E402
    load_migration_compatibility_manifest,
)
from aicrm_next.platform.shared.sensitive_data import redact_sensitive_text  # noqa: E402
from scripts.ci.select_test_scope import load_inventory  # noqa: E402


_HEAD_LITERAL_ALLOWED = {
    Path("docs/architecture/data_table_lifecycle_manifest.yml"),
    Path("docs/architecture/runtime_contract_inventory.json"),
    Path("docs/ci/current_behavior_inventory.json"),
    Path("deploy/migration_compatibility.json"),
}
_ACTIVE_REFERENCE_ROOTS = (
    ROOT / ".github" / "workflows",
    ROOT / "aicrm_next",
    ROOT / "scripts",
)
_ACTIVE_REFERENCE_FILES = (
    ROOT / "docs" / "architecture" / "id_validation_promotion_manifest.yml",
)
_DEPRECATED_DATA_HEALTH_ID = "external_effect_failed_" + "retryable_backlog"
_DEPRECATED_MIGRATION_PROOF = "tests/test_alembic_" + "revision_chain.py"


def _node_exists(node_id: str) -> bool:
    return (ROOT / node_id.split("::", 1)[0]).is_file()


def _current_head_literal_violations() -> list[str]:
    current_head = str(load_inventory()["migration_contract"]["expected_head"])
    violations: list[str] = []
    for suffix in ("*.py", "*.json", "*.yml", "*.yaml"):
        for path in ROOT.rglob(suffix):
            relative = path.relative_to(ROOT)
            if ".venv" in relative.parts or "test-results" in relative.parts:
                continue
            if relative.parts[:2] == ("migrations", "versions") or relative in _HEAD_LITERAL_ALLOWED:
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if current_head in source:
                violations.append(relative.as_posix())
    return sorted(violations)


def _deprecated_release_reference_violations() -> list[str]:
    paths = list(_ACTIVE_REFERENCE_FILES)
    for root in _ACTIVE_REFERENCE_ROOTS:
        paths.extend(path for path in root.rglob("*") if path.suffix in {".json", ".py", ".yaml", ".yml"})
    violations: list[str] = []
    for path in paths:
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if _DEPRECATED_DATA_HEALTH_ID in source or _DEPRECATED_MIGRATION_PROOF in source:
            violations.append(path.relative_to(ROOT).as_posix())
    return sorted(set(violations))


def validate() -> dict[str, object]:
    manifest = load_release_gate_manifest()
    migration_manifest = load_migration_compatibility_manifest()
    errors: list[str] = []
    missing_contracts = sorted(gate.ci_contract for gate in manifest.gates if not _node_exists(gate.ci_contract))
    if missing_contracts:
        errors.append("missing_ci_contracts:" + ",".join(missing_contracts))
    head_violations = _current_head_literal_violations()
    if head_violations:
        errors.append("current_migration_head_literal_outside_inventory:" + ",".join(head_violations))
    deprecated_references = _deprecated_release_reference_violations()
    if deprecated_references:
        errors.append("deprecated_release_reference:" + ",".join(deprecated_references))
    current_head = str(load_inventory()["migration_contract"]["expected_head"])
    if migration_manifest.current_head != current_head:
        errors.append("migration_compatibility_current_head_not_inventory_head")
    if any(
        entry.change_kind == "expand" and not entry.previous_runtime_compatible
        for entry in migration_manifest.migrations
    ):
        errors.append("expand_migration_not_previous_runtime_compatible")
    return {
        "ok": not errors,
        "schema_version": manifest.schema_version,
        "gate_count": len(manifest.gates),
        "gate_ids": [gate.gate_id for gate in manifest.gates],
        "data_health_registry_sha256": data_health_registry_digest(manifest.data_health_check_ids),
        "errors": errors,
        "pii_included": False,
        "real_external_call_executed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the single AI-CRM release-gate manifest")
    parser.parse_args()
    payload = validate()
    print(
        redact_sensitive_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    )
    return 0 if payload["ok"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
