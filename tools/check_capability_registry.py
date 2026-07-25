#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.admin_config.schema import CONFIG_SCHEMA
from aicrm_next.capability_registry import (
    CAPABILITY_SPECS,
    capability_for_config_section,
    capability_for_context,
    capability_for_effect_type,
    capability_for_event_type,
    capability_for_job_type,
    capability_for_route_group,
    capability_for_table_domain,
    registry_summary,
    validate_capability_registry,
)
from aicrm_next.internal_event_composition import build_internal_event_consumer_registry
from aicrm_next.platform_foundation.external_effects import models as external_effect_models
from aicrm_next.router_registry import ROUTER_SPECS
from tools.check_import_graph import scan_import_graph


TABLE_MANIFEST = ROOT / "docs" / "architecture" / "data_table_lifecycle_manifest.yml"
EFFECT_PREFIXES = (
    "ai_assist.",
    "feishu.",
    "group_ops.",
    "media.",
    "openclaw.",
    "payment.",
    "webhook.",
    "wecom.",
)


def build_capability_ownership_report(root: Path = ROOT) -> dict[str, Any]:
    import_graph = scan_import_graph(root)
    contexts = sorted(import_graph.contexts)
    route_groups = sorted(spec.route_group for spec in ROUTER_SPECS)
    config_sections = sorted(CONFIG_SCHEMA)
    table_manifest = yaml.safe_load((root / TABLE_MANIFEST.relative_to(ROOT)).read_text(encoding="utf-8"))
    tables = dict(table_manifest.get("tables") or {})
    table_domains = sorted({str(entry.get("domain") or "") for entry in tables.values()})
    event_types = sorted(build_internal_event_consumer_registry().to_dict())
    effect_types = sorted(
        value
        for name, value in vars(external_effect_models).items()
        if name.isupper() and isinstance(value, str) and value.startswith(EFFECT_PREFIXES)
    )
    job_types = sorted({job_type for spec in CAPABILITY_SPECS for job_type in spec.job_types})
    return {
        "registry": registry_summary(),
        "contexts": [
            {"name": item, "capability_id": (capability_for_context(item) or _missing()).capability_id}
            for item in contexts
        ],
        "route_groups": [
            {"name": item, "capability_id": (capability_for_route_group(item) or _missing()).capability_id}
            for item in route_groups
        ],
        "config_sections": [
            {"name": item, "capability_id": (capability_for_config_section(item) or _missing()).capability_id}
            for item in config_sections
        ],
        "table_domains": [
            {"name": item, "capability_id": (capability_for_table_domain(item) or _missing()).capability_id}
            for item in table_domains
        ],
        "event_types": [
            {"name": item, "capability_id": (capability_for_event_type(item) or _missing()).capability_id}
            for item in event_types
        ],
        "effect_types": [
            {"name": item, "capability_id": (capability_for_effect_type(item) or _missing()).capability_id}
            for item in effect_types
        ],
        "job_types": [
            {"name": item, "capability_id": (capability_for_job_type(item) or _missing()).capability_id}
            for item in job_types
        ],
    }


def validate_capability_ownership(root: Path = ROOT) -> list[str]:
    errors = list(validate_capability_registry())
    report = build_capability_ownership_report(root)
    for section in ("contexts", "route_groups", "config_sections", "table_domains", "event_types", "effect_types", "job_types"):
        for item in report[section]:
            if item["capability_id"] == "<missing>":
                errors.append(f"{section}: missing capability owner for {item['name']}")

    exact_declarations = {
        "contexts": {item for spec in CAPABILITY_SPECS for item in spec.current_contexts},
        "route_groups": {item for spec in CAPABILITY_SPECS for item in spec.route_groups},
        "config_sections": {item for spec in CAPABILITY_SPECS for item in spec.config_sections},
        "table_domains": {item for spec in CAPABILITY_SPECS for item in spec.table_domains},
        "effect_types": {item for spec in CAPABILITY_SPECS for item in spec.effect_types},
        "job_types": {item for spec in CAPABILITY_SPECS for item in spec.job_types},
    }
    for section, declared in exact_declarations.items():
        actual = {str(item["name"]) for item in report[section]}
        stale = sorted(declared - actual)
        if stale:
            errors.append(f"{section}: stale capability declarations: {', '.join(stale)}")

    core_specs = [spec for spec in CAPABILITY_SPECS if spec.tier == "core"]
    if len(core_specs) != 7:
        errors.append(f"expected 7 stable core capabilities, found {len(core_specs)}")
    for spec in CAPABILITY_SPECS:
        if spec.tier == "extension" and spec.enabled_by_default:
            errors.append(f"extension must be opt-in: {spec.capability_id}")
        if not spec.enabled_by_default:
            continue
        for dependency in spec.dependencies:
            dependency_spec = next((item for item in CAPABILITY_SPECS if item.capability_id == dependency), None)
            if dependency_spec is None or not dependency_spec.enabled_by_default:
                errors.append(f"default capability {spec.capability_id} requires disabled dependency {dependency}")
    return errors


class _MissingCapability:
    capability_id = "<missing>"


def _missing() -> _MissingCapability:
    return _MissingCapability()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the static AI-CRM capability ownership registry.")
    parser.add_argument("--json", action="store_true", help="Print the complete ownership report as JSON.")
    args = parser.parse_args(argv)
    errors = validate_capability_ownership(ROOT)
    if args.json:
        print(json.dumps(build_capability_ownership_report(ROOT), ensure_ascii=False, indent=2, sort_keys=True))
    if errors:
        print("Capability registry check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Capability registry OK: 7 core capabilities, opt-in extensions, exact ownership coverage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
