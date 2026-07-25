#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "docs" / "architecture" / "legacy_cleanup_policy.yml"
TABLES = ROOT / "docs" / "architecture" / "data_table_lifecycle_manifest.yml"
DROP_TABLE = re.compile(r"\bDROP\s+TABLE(?:\s+IF\s+EXISTS)?\s+(?:public\.)?[\"']?([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)


def validate_legacy_cleanup_contract(root: Path = ROOT) -> list[str]:
    policy = yaml.safe_load((root / POLICY.relative_to(ROOT)).read_text(encoding="utf-8"))
    table_manifest = yaml.safe_load((root / TABLES.relative_to(ROOT)).read_text(encoding="utf-8"))
    tables = dict(table_manifest.get("tables") or {})
    evidence_dir = root / str(policy.get("evidence_directory") or "")
    floor = str(policy.get("enforce_for_migrations_after") or "")
    required = tuple(str(item) for item in policy.get("required_evidence") or [])
    minimum_days = int(policy.get("minimum_zero_read_write_days") or 0)
    errors: list[str] = []

    migration_paths = sorted((root / "migrations" / "versions").glob("*.py"))
    after_floor = False
    for path in migration_paths:
        if path.stem.startswith(floor):
            after_floor = True
            continue
        if not after_floor:
            continue
        source = path.read_text(encoding="utf-8")
        for table in sorted(set(DROP_TABLE.findall(source))):
            lifecycle = str((tables.get(table) or {}).get("lifecycle") or "")
            if lifecycle not in {"legacy", "retired"}:
                errors.append(f"{path.name}: DROP TABLE {table} is not a governed legacy/retired table")
                continue
            evidence_path = evidence_dir / f"{table}.json"
            if not evidence_path.exists():
                errors.append(f"{path.name}: DROP TABLE {table} lacks independent cleanup evidence")
                continue
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            errors.extend(_validate_evidence(table, path.stem, evidence, required, minimum_days))
    return errors


def _validate_evidence(
    table: str,
    migration: str,
    evidence: dict[str, Any],
    required: tuple[str, ...],
    minimum_days: int,
) -> list[str]:
    errors = [f"{table}: cleanup evidence missing {field}" for field in required if not evidence.get(field)]
    if evidence.get("table") != table:
        errors.append(f"{table}: cleanup evidence table mismatch")
    if evidence.get("drop_migration") != migration:
        errors.append(f"{table}: cleanup evidence migration mismatch")
    if int(evidence.get("observation_days") or 0) < minimum_days:
        errors.append(f"{table}: zero read/write observation is shorter than {minimum_days} days")
    for field in ("zero_read_write_observed_since", "export_verified_at", "rollback_rehearsed_at"):
        value = str(evidence.get(field) or "")[:10]
        try:
            date.fromisoformat(value)
        except ValueError:
            errors.append(f"{table}: {field} must contain an ISO date")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refuse legacy table drops without observation, export, and rollback evidence.")
    parser.parse_args(argv)
    errors = validate_legacy_cleanup_contract()
    if errors:
        print("Legacy cleanup contract failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Legacy cleanup contract OK: no ungated post-0143 table drops")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
