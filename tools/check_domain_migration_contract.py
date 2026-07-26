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

    domain_names = set(stable) | {"extensions"}
    allowed_raw = policy.get("allowed_domain_dependencies") or {}
    if set(allowed_raw) != domain_names:
        errors.append("allowed_domain_dependencies must declare every approved logical domain exactly once")
    allowed: dict[str, set[str]] = {}
    for source_domain in sorted(domain_names):
        targets = allowed_raw.get(source_domain) or []
        if not isinstance(targets, list):
            errors.append(f"allowed_domain_dependencies.{source_domain} must be a list")
            continue
        normalized_targets = {str(target or "").strip() for target in targets}
        if "" in normalized_targets or len(normalized_targets) != len(targets):
            errors.append(f"allowed_domain_dependencies.{source_domain} must contain unique non-empty domains")
            continue
        unknown_targets = normalized_targets - domain_names
        if unknown_targets:
            errors.append(
                f"allowed_domain_dependencies.{source_domain} contains unknown domains: "
                + ", ".join(sorted(unknown_targets))
            )
        if source_domain in normalized_targets:
            errors.append(f"allowed_domain_dependencies.{source_domain} must not include itself")
        allowed[source_domain] = normalized_targets

    reverse_edges: list[str] = []
    domain_edges: set[tuple[str, str]] = set()
    allowed_domain_edges = {
        (source_domain, target_domain)
        for source_domain, targets in allowed.items()
        for target_domain in targets
    }
    if _cyclic_domains(domain_names, allowed_domain_edges):
        errors.append("allowed logical-domain dependency policy must remain acyclic")
    for edge in graph.edges:
        source_capability = capability_for_context(edge.source_context)
        target_capability = capability_for_context(edge.target_context)
        if source_capability is None or target_capability is None:
            continue
        source_domain = source_capability.logical_domain
        target_domain = target_capability.logical_domain
        if source_domain == target_domain:
            continue
        domain_edges.add((source_domain, target_domain))
        if target_domain not in allowed.get(source_domain, set()):
            reverse_edges.append(
                f"{edge.source_context} ({source_domain}) -> {edge.target_context} ({target_domain})"
            )
    if reverse_edges:
        errors.append("reverse logical-domain imports must use public ports: " + "; ".join(sorted(reverse_edges)))
    if _cyclic_domains(domain_names, domain_edges):
        errors.append("logical domain dependency graph must remain acyclic")

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


def _cyclic_domains(domains: set[str], edges: set[tuple[str, str]]) -> bool:
    adjacency = {domain: set() for domain in domains}
    for source, target in edges:
        adjacency.setdefault(source, set()).add(target)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(domain: str) -> bool:
        if domain in visiting:
            return True
        if domain in visited:
            return False
        visiting.add(domain)
        for target in adjacency.get(domain, set()):
            if visit(target):
                return True
        visiting.remove(domain)
        visited.add(domain)
        return False

    return any(visit(domain) for domain in sorted(domains))


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
