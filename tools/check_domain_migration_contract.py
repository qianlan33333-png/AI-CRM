#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.capability_registry import CAPABILITY_SPECS, capability_for_context
from tools.check_import_graph import scan_import_graph


POLICY = ROOT / "docs" / "architecture" / "domain_migration_policy.yml"


def validate_domain_migration_contract(root: Path = ROOT) -> list[str]:
    policy = yaml.safe_load((root / POLICY.relative_to(ROOT)).read_text(encoding="utf-8"))
    errors: list[str] = []
    stable = list(policy.get("stable_core_domains") or [])
    if stable != ["app", "platform", "crm", "channels", "engagement", "automation", "insights"]:
        errors.append("stable_core_domains must remain the seven approved logical domains")
    if policy.get("static_package_domain") != "extensions":
        errors.append("static_package_domain must be extensions")

    graph = scan_import_graph(root)
    owned_contexts = {context for spec in CAPABILITY_SPECS for context in spec.current_contexts}
    if owned_contexts != set(graph.contexts):
        errors.append("every current context must have exactly one capability migration owner")
    if any(capability_for_context(context) is None for context in graph.contexts):
        errors.append("one or more contexts have no migration target")
    if graph.cyclic_context_count != 0:
        errors.append("context dependency graph must remain acyclic")

    physical = dict(policy.get("physical_moves") or {})
    if physical.get("enabled") is False:
        target_packages = set(stable) | {"extensions"}
        unexpected = sorted(
            name
            for name in target_packages
            if (root / "aicrm_next" / name).is_dir()
        )
        if unexpected:
            errors.append("physical domain packages appeared before inversion gate: " + ", ".join(unexpected))
    return errors


def main() -> int:
    errors = validate_domain_migration_contract()
    if errors:
        print("Domain migration contract failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Domain migration contract OK: 7 core domains plus static extensions, physical moves gated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
