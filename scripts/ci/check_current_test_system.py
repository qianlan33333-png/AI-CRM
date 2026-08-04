#!/usr/bin/env python3
"""Validate the compact current-code test system without executing tests."""

from __future__ import annotations

import ast
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.platform.shared.sensitive_data import redact_sensitive_text  # noqa: E402
from scripts.ci.select_test_scope import PUBLIC_OUTPUT_FIELDS, load_inventory, matches  # noqa: E402


LAYER_DIRS = {
    "unit": "tests/unit/",
    "contracts": "tests/contracts/",
    "postgres": "tests/postgres/",
    "high_risk": "tests/high_risk/",
    "release": "tests/release/",
    "frontend": "tests/frontend/",
}

IGNORED_TREE_PARTS = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "test-results",
}


def _python_test_files() -> list[Path]:
    return sorted(path for path in (ROOT / "tests").rglob("test_*.py") if path.is_file())


def _all_test_python_files() -> list[Path]:
    return sorted(path for path in (ROOT / "tests").rglob("*.py") if path.is_file())


def _literal_assignment(tree: ast.Module, name: str) -> Any:
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            value = node.value
            try:
                return ast.literal_eval(value)
            except (ValueError, TypeError):
                return None
    return None


def _migration_contract(inventory: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    contract = dict(inventory.get("migration_contract") or {})
    directory = ROOT / str(contract.get("directory") or "migrations/versions")
    files = sorted(directory.glob("*.py"))
    expected_count = int(contract.get("expected_file_count") or 0)
    if len(files) != expected_count:
        errors.append(f"migration file count is {len(files)}, expected {expected_count}")
    revisions: dict[str, tuple[Path, tuple[str, ...]]] = {}
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        revision = _literal_assignment(tree, "revision")
        down_revision = _literal_assignment(tree, "down_revision")
        if not isinstance(revision, str) or not revision:
            errors.append(f"{path.relative_to(ROOT)} has no literal revision")
            continue
        if revision in revisions:
            errors.append(f"duplicate migration revision: {revision}")
        if down_revision is None:
            parents: tuple[str, ...] = ()
        elif isinstance(down_revision, str):
            parents = (down_revision,)
        elif isinstance(down_revision, (tuple, list)) and all(isinstance(value, str) for value in down_revision):
            parents = tuple(down_revision)
        else:
            errors.append(f"{path.relative_to(ROOT)} has a non-literal down_revision")
            parents = ()
        revisions[revision] = (path, parents)
    referenced = {parent for _path, parents in revisions.values() for parent in parents}
    missing = sorted(referenced - set(revisions))
    if missing:
        errors.append("migration parents are missing: " + ", ".join(missing))
    heads = sorted(set(revisions) - referenced)
    expected_head = str(contract.get("expected_head") or "")
    if heads != [expected_head]:
        errors.append(f"migration heads are {heads}, expected [{expected_head!r}]")
    return errors


def _test_budget_contract(inventory: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    budgets = dict(inventory.get("test_budgets") or {})
    python_tests = _python_test_files()
    python_files = _all_test_python_files()
    line_count = sum(len(path.read_text(encoding="utf-8").splitlines()) for path in python_files)
    max_files = int(budgets.get("max_python_test_files") or 0)
    max_lines = int(budgets.get("max_python_test_lines") or 0)
    if len(python_tests) > max_files:
        errors.append(f"Python test file budget exceeded: {len(python_tests)} > {max_files}")
    if line_count > max_lines:
        errors.append(f"Python test line budget exceeded: {line_count} > {max_lines}")
    flat_tests = sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / "tests").glob("test_*.py"))
    if flat_tests:
        errors.append("root-level test files are forbidden: " + ", ".join(flat_tests))
    allowed_roots = tuple(LAYER_DIRS.values())
    for path in python_tests:
        relative = path.relative_to(ROOT).as_posix()
        if not relative.startswith(allowed_roots):
            errors.append(f"test is outside the six current layers: {relative}")
    return errors


def _root_conftest_contract() -> list[str]:
    path = ROOT / "tests" / "conftest.py"
    if not path.exists():
        return ["tests/conftest.py is missing"]
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = node.module if isinstance(node, ast.ImportFrom) else ""
            names = [alias.name for alias in node.names]
            if module == "aicrm_next.main" or "aicrm_next.main" in names:
                errors.append("root conftest must not import the application")
        if isinstance(node, ast.Call):
            keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
            autouse = keywords.get("autouse")
            if isinstance(autouse, ast.Constant) and autouse.value is True:
                errors.append("root conftest must not define autouse fixtures")
    return errors


def _inventory_contract(inventory: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    allowed_side_effects = set(inventory.get("allowed_test_side_effects") or [])
    all_files = [
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file() and not any(part in IGNORED_TREE_PARTS for part in path.relative_to(ROOT).parts)
    ]
    covered_route_groups: list[str] = []
    behavior_ids: set[str] = set()
    for behavior in inventory.get("behaviors", []):
        if not isinstance(behavior, dict):
            errors.append("behavior entries must be objects")
            continue
        behavior_id = str(behavior.get("id") or "")
        if not behavior_id or behavior_id in behavior_ids:
            errors.append(f"invalid or duplicate behavior id: {behavior_id!r}")
        behavior_ids.add(behavior_id)
        patterns = [str(value) for value in behavior.get("source_paths", [])]
        for pattern in patterns:
            if not any(matches(path, pattern) for path in all_files):
                errors.append(f"{behavior_id}: source path pattern matches nothing: {pattern}")
        covered_route_groups.extend(str(value) for value in behavior.get("route_groups", []))
        effects = set(str(value) for value in behavior.get("expected_side_effects", []))
        unknown_effects = sorted(effects - allowed_side_effects)
        if unknown_effects:
            errors.append(f"{behavior_id}: forbidden test side effects: {', '.join(unknown_effects)}")
        tests = behavior.get("tests", [])
        if not tests:
            errors.append(f"{behavior_id}: no tests mapped")
        for target in tests:
            if not isinstance(target, dict):
                errors.append(f"{behavior_id}: test mapping must be an object")
                continue
            test_path = str(target.get("path") or "")
            layer = str(target.get("layer") or "")
            if layer not in LAYER_DIRS:
                errors.append(f"{behavior_id}: unknown test layer {layer!r}")
                continue
            if not test_path.startswith(LAYER_DIRS[layer]):
                errors.append(f"{behavior_id}: {test_path} does not belong to {layer}")
            if not (ROOT / test_path).is_file():
                errors.append(f"{behavior_id}: mapped test does not exist: {test_path}")

    duplicates = sorted(group for group in set(covered_route_groups) if covered_route_groups.count(group) > 1)
    if duplicates:
        errors.append("route groups mapped more than once: " + ", ".join(duplicates))

    os.environ.setdefault("AICRM_NEXT_ENV", "test")
    os.environ.setdefault("SECRET_KEY", "current-test-system-secret")
    os.environ.pop("DATABASE_URL", None)
    from aicrm_next.router_registry import ROUTER_SPECS

    current_groups = {spec.route_group for spec in ROUTER_SPECS}
    covered_groups = set(covered_route_groups)
    missing = sorted(current_groups - covered_groups)
    stale = sorted(covered_groups - current_groups)
    if missing:
        errors.append("current route groups missing behavior coverage: " + ", ".join(missing))
    if stale:
        errors.append("behavior inventory contains stale route groups: " + ", ".join(stale))
    return errors


def _test_body_contract() -> list[str]:
    errors: list[str] = []
    bodies: defaultdict[str, list[str]] = defaultdict(list)
    assertion_strings: defaultdict[str, list[str]] = defaultdict(list)
    for path in _python_test_files():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                body_key = ast.dump(ast.Module(body=node.body, type_ignores=[]), include_attributes=False)
                if len(body_key) >= 160:
                    bodies[body_key].append(f"{path.relative_to(ROOT)}::{node.name}")
            if isinstance(node, ast.Assert):
                for nested in ast.walk(node.test):
                    if isinstance(nested, ast.Constant) and isinstance(nested.value, str) and len(nested.value) >= 24:
                        assertion_strings[nested.value].append(path.relative_to(ROOT).as_posix())
    for locations in bodies.values():
        if len(locations) > 1:
            errors.append("duplicate test bodies: " + ", ".join(locations))
    for value, locations in assertion_strings.items():
        unique_locations = sorted(set(locations))
        if len(unique_locations) > 1:
            errors.append(f"repeated long source-string assertion across tests: {value!r} in {', '.join(unique_locations)}")
    return errors


def _workflow_contract() -> list[str]:
    errors: list[str] = []
    full_path = ROOT / ".github" / "workflows" / "full-regression.yml"
    if full_path.exists():
        source = full_path.read_text(encoding="utf-8")
        if "schedule:" in source or "cron:" in source:
            errors.append("Full Regression must not have a schedule")
    selector_source = (ROOT / "scripts" / "ci" / "select_test_scope.py").read_text(encoding="utf-8")
    tree = ast.parse(selector_source)
    public_fields = _literal_assignment(tree, "PUBLIC_OUTPUT_FIELDS")
    if tuple(public_fields or ()) != PUBLIC_OUTPUT_FIELDS:
        errors.append("selector public outputs drifted from the fixed five-field contract")
    return errors


def check() -> list[str]:
    inventory = load_inventory()
    errors: list[str] = []
    errors.extend(_test_budget_contract(inventory))
    errors.extend(_root_conftest_contract())
    errors.extend(_inventory_contract(inventory))
    errors.extend(_migration_contract(inventory))
    errors.extend(_test_body_contract())
    errors.extend(_workflow_contract())
    return errors


def main() -> int:
    errors = check()
    payload = {
        "ok": not errors,
        "error_count": len(errors),
        "python_test_files": len(_python_test_files()),
        "python_test_lines": sum(
            len(path.read_text(encoding="utf-8").splitlines())
            for path in _all_test_python_files()
        ),
    }
    print(redact_sensitive_text(json.dumps(payload, ensure_ascii=False, sort_keys=True)))
    for error in errors:
        print(redact_sensitive_text(f"ERROR: {error}"))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
