from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.runtime_configuration import (  # noqa: E402
    CUTOVER_ELIGIBLE_RUNTIME_SETTING_KEYS,
    MANAGED_RUNTIME_SETTING_KEYS,
    STARTUP_ENVIRONMENT_SETTING_KEYS,
)


MIGRATED_PATHS = ("aicrm_next",)
RUNTIME_ACCESSORS = frozenset(
    {
        "managed_runtime_bool",
        "managed_runtime_int",
        "managed_runtime_setting",
        "runtime_bool",
        "runtime_csv",
        "runtime_int",
        "runtime_setting",
        "startup_environment_setting",
    }
)
DECLARED_KEY_NAMES = frozenset({"RUNTIME_ENVIRONMENT_KEYS", "RUNTIME_SETTING_KEYS"})
STARTUP_ONLY_KEYS = STARTUP_ENVIRONMENT_SETTING_KEYS
DIRECT_ENVIRONMENT_ACCESS_BOUNDARIES = frozenset(
    {
        "aicrm_next/shared/database.py",
        "aicrm_next/shared/db_session.py",
        "aicrm_next/shared/release.py",
        "aicrm_next/shared/runtime.py",
        "aicrm_next/shared/runtime_settings.py",
        "aicrm_next/shared/secret_store.py",
    }
)
DYNAMIC_RUNTIME_ACCESS_BOUNDARIES = frozenset(
    {
        "aicrm_next/message_archive/repo.py",
        "aicrm_next/questionnaire/repo.py",
        "aicrm_next/shared/runtime.py",
        "aicrm_next/shared/runtime_settings.py",
    }
)
ENVIRONMENT_FALLBACK_BOUNDARIES = frozenset(
    {
        "aicrm_next/admin_config/secret_settings.py",
        "aicrm_next/integration_gateway/questionnaire_adapters.py",
        "aicrm_next/platform_foundation/execution_runtime/listener.py",
        "aicrm_next/platform_foundation/push_center/capability_repository.py",
        "aicrm_next/platform_foundation/verification_files.py",
        "aicrm_next/shared/runtime_settings.py",
    }
)


@dataclass(frozen=True)
class RuntimeConfigurationViolation:
    path: str
    line: int
    rule: str
    detail: str

    def format(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.detail}"


def check_runtime_configuration_contract(
    *,
    root: Path = ROOT,
    migrated_paths: Iterable[str] = MIGRATED_PATHS,
    definition_keys: set[str] | None = None,
) -> list[RuntimeConfigurationViolation]:
    root = root.resolve()
    known_keys = _config_definition_keys() if definition_keys is None else set(definition_keys)
    violations: list[RuntimeConfigurationViolation] = []
    if definition_keys is None:
        for key in sorted(
            CUTOVER_ELIGIBLE_RUNTIME_SETTING_KEYS - MANAGED_RUNTIME_SETTING_KEYS
        ):
            violations.append(
                RuntimeConfigurationViolation(
                    path="aicrm_next/runtime_configuration.py",
                    line=1,
                    rule="cutover_key_not_managed",
                    detail=f"{key} is eligible for cutover but has no managed resolver",
                )
            )
        for key in sorted(MANAGED_RUNTIME_SETTING_KEYS - known_keys):
            violations.append(
                RuntimeConfigurationViolation(
                    path="aicrm_next/runtime_configuration.py",
                    line=1,
                    rule="managed_runtime_setting_missing_definition",
                    detail=f"{key} is not declared by ConfigDefinition",
                )
            )
    migrated_python_paths = _python_paths(root, migrated_paths)
    for path in migrated_python_paths:
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        constants = _module_string_constants(tree)
        declared_keys = _declared_keys(tree)

        if relative not in DIRECT_ENVIRONMENT_ACCESS_BOUNDARIES:
            for line, detail in _direct_environment_accesses(tree):
                violations.append(
                    RuntimeConfigurationViolation(
                        path=relative,
                        line=line,
                        rule="direct_environment_access_forbidden",
                        detail=detail,
                    )
                )

        for key in sorted(declared_keys):
            if key not in known_keys and key not in STARTUP_ONLY_KEYS:
                violations.append(
                    RuntimeConfigurationViolation(
                        path=relative,
                        line=1,
                        rule="runtime_setting_missing_definition",
                        detail=f"{key} is not declared by ConfigDefinition",
                    )
                )

        has_declared_keys = bool(declared_keys)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            accessor = _call_name(node)
            if accessor not in RUNTIME_ACCESSORS | {"environment_fallback"}:
                continue
            key = _resolved_string(node.args[0] if node.args else None, constants)
            if accessor == "startup_environment_setting":
                if key:
                    if key not in STARTUP_ONLY_KEYS:
                        violations.append(
                            RuntimeConfigurationViolation(
                                path=relative,
                                line=int(getattr(node, "lineno", 0) or 0),
                                rule="non_startup_environment_setting",
                                detail=f"{key} must use a published ConfigDefinition",
                            )
                        )
                elif not has_declared_keys:
                    violations.append(
                        RuntimeConfigurationViolation(
                            path=relative,
                            line=int(getattr(node, "lineno", 0) or 0),
                            rule="dynamic_startup_setting_without_declaration",
                            detail="startup_environment_setting must be backed by RUNTIME_ENVIRONMENT_KEYS",
                        )
                    )
                else:
                    for declared_key in sorted(declared_keys - STARTUP_ONLY_KEYS):
                        violations.append(
                            RuntimeConfigurationViolation(
                                path=relative,
                                line=int(getattr(node, "lineno", 0) or 0),
                                rule="non_startup_environment_setting",
                                detail=f"{declared_key} must use a published ConfigDefinition",
                            )
                        )
                continue
            if not key:
                if accessor.startswith("managed_runtime_"):
                    for declared_key in sorted(declared_keys - MANAGED_RUNTIME_SETTING_KEYS):
                        violations.append(
                            RuntimeConfigurationViolation(
                                path=relative,
                                line=int(getattr(node, "lineno", 0) or 0),
                                rule="managed_runtime_setting_missing_cutover_registration",
                                detail=f"{declared_key} is not registered for expand/contract cutover",
                            )
                        )
                if (
                    not has_declared_keys
                    and relative not in ENVIRONMENT_FALLBACK_BOUNDARIES
                    and relative not in DYNAMIC_RUNTIME_ACCESS_BOUNDARIES
                ):
                    violations.append(
                        RuntimeConfigurationViolation(
                            path=relative,
                            line=int(getattr(node, "lineno", 0) or 0),
                            rule="dynamic_runtime_setting_without_declaration",
                            detail=f"{accessor} must be backed by RUNTIME_SETTING_KEYS",
                        )
                    )
                continue
            if accessor.startswith("managed_runtime_") and key not in MANAGED_RUNTIME_SETTING_KEYS:
                violations.append(
                    RuntimeConfigurationViolation(
                        path=relative,
                        line=int(getattr(node, "lineno", 0) or 0),
                        rule="managed_runtime_setting_missing_cutover_registration",
                        detail=f"{key} is not registered for expand/contract cutover",
                    )
                )
            if accessor == "environment_fallback":
                if relative not in ENVIRONMENT_FALLBACK_BOUNDARIES or (
                    key not in STARTUP_ONLY_KEYS and relative not in {
                        "aicrm_next/admin_config/secret_settings.py",
                        "aicrm_next/platform_foundation/push_center/capability_repository.py",
                    }
                ):
                    violations.append(
                        RuntimeConfigurationViolation(
                            path=relative,
                            line=int(getattr(node, "lineno", 0) or 0),
                            rule="environment_fallback_outside_boundary",
                            detail=f"{key} must use runtime_setting or be an explicit startup key",
                        )
                    )
                continue
            if key not in known_keys and key not in STARTUP_ONLY_KEYS:
                violations.append(
                    RuntimeConfigurationViolation(
                        path=relative,
                        line=int(getattr(node, "lineno", 0) or 0),
                        rule="runtime_setting_missing_definition",
                        detail=f"{key} is not declared by ConfigDefinition",
                    )
                )
    for path in _python_paths(root, ("aicrm_next",)):
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        constants = _module_string_constants(tree)
        for line, key, detail in _direct_environment_key_accesses(tree, constants):
            if key not in CUTOVER_ELIGIBLE_RUNTIME_SETTING_KEYS:
                continue
            violations.append(
                RuntimeConfigurationViolation(
                    path=relative,
                    line=line,
                    rule="cutover_key_direct_environment_access",
                    detail=f"{key} bypasses the atomic runtime cutover resolver via {detail}",
                )
            )
    return _unique_violations(violations)


def _config_definition_keys() -> set[str]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from aicrm_next.admin_config.config_definitions import CONFIG_DEFINITIONS_BY_KEY

    return set(CONFIG_DEFINITIONS_BY_KEY)


def _python_paths(root: Path, migrated_paths: Iterable[str]) -> list[Path]:
    paths: set[Path] = set()
    for value in migrated_paths:
        path = root / value
        if path.is_dir():
            paths.update(path.rglob("*.py"))
        elif path.is_file() and path.suffix == ".py":
            paths.add(path)
    return sorted(paths)


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and isinstance(value, ast.Constant) and isinstance(value.value, str):
            result[target.id] = value.value.strip()
    return result


def _declared_keys(tree: ast.Module) -> set[str]:
    result: set[str] = set()
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if not isinstance(target, ast.Name) or target.id not in DECLARED_KEY_NAMES or value is None:
            continue
        result.update(
            str(item.value).strip()
            for item in ast.walk(value)
            if isinstance(item, ast.Constant)
            and isinstance(item.value, str)
            and str(item.value).strip()
        )
    return result


def _direct_environment_accesses(tree: ast.Module) -> list[tuple[int, str]]:
    os_aliases = {"os"}
    getenv_aliases: set[str] = set()
    environ_aliases: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    os_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                if alias.name == "getenv":
                    getenv_aliases.add(alias.asname or alias.name)
                elif alias.name == "environ":
                    environ_aliases.add(alias.asname or alias.name)

    accesses: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in getenv_aliases:
                accesses.append((node.lineno, f"{node.func.id}(...) bypasses runtime settings"))
            elif (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in os_aliases
                and node.func.attr == "getenv"
            ):
                accesses.append((node.lineno, f"{node.func.value.id}.getenv(...) bypasses runtime settings"))
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in os_aliases and node.attr == "environ":
                accesses.append((node.lineno, f"{node.value.id}.environ bypasses runtime settings"))
        if isinstance(node, ast.Name) and node.id in environ_aliases and isinstance(node.ctx, ast.Load):
            accesses.append((node.lineno, f"{node.id} bypasses runtime settings"))
    return sorted(set(accesses))


def _direct_environment_key_accesses(
    tree: ast.Module,
    constants: dict[str, str],
) -> list[tuple[int, str, str]]:
    """Return literal environment keys used directly by application code."""

    os_aliases = {"os"}
    getenv_aliases: set[str] = set()
    environ_aliases: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    os_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                if alias.name == "getenv":
                    getenv_aliases.add(alias.asname or alias.name)
                elif alias.name == "environ":
                    environ_aliases.add(alias.asname or alias.name)

    accesses: set[tuple[int, str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            key = _resolved_string(node.args[0] if node.args else None, constants)
            if not key:
                continue
            if isinstance(node.func, ast.Name) and node.func.id in getenv_aliases:
                accesses.add((node.lineno, key, f"{node.func.id}(...)"))
                continue
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in os_aliases
                and node.func.attr == "getenv"
            ):
                accesses.add(
                    (node.lineno, key, f"{node.func.value.id}.getenv(...)")
                )
                continue
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and _is_environment_expression(
                    node.func.value,
                    os_aliases=os_aliases,
                    environ_aliases=environ_aliases,
                )
            ):
                accesses.add((node.lineno, key, "environ.get(...)"))
        if isinstance(node, ast.Subscript) and _is_environment_expression(
            node.value,
            os_aliases=os_aliases,
            environ_aliases=environ_aliases,
        ):
            key = _resolved_string(node.slice, constants)
            if key:
                accesses.add((node.lineno, key, "environ[...]"))
    return sorted(accesses)


def _is_environment_expression(
    node: ast.AST,
    *,
    os_aliases: set[str],
    environ_aliases: set[str],
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in environ_aliases
    return bool(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in os_aliases
        and node.attr == "environ"
    )


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _resolved_string(node: ast.AST | None, constants: dict[str, str]) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.strip()
    if isinstance(node, ast.Name):
        return constants.get(node.id, "")
    return ""


def _unique_violations(
    violations: Iterable[RuntimeConfigurationViolation],
) -> list[RuntimeConfigurationViolation]:
    unique = {
        (item.path, item.line, item.rule, item.detail): item
        for item in violations
    }
    return [unique[key] for key in sorted(unique)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the runtime configuration cutover contract.")
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args(argv)
    violations = check_runtime_configuration_contract(root=Path(args.root))
    if violations:
        print("Runtime configuration contract check failed:")
        for violation in violations:
            print(f"- {violation.format()}")
        return 1
    print("Runtime configuration contract check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
