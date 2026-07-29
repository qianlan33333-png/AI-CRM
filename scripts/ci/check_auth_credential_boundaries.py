#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import sys
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "docs/architecture/auth_credential_inventory.yml"
ROUTE_MANIFEST = ROOT / "docs/architecture/route_ownership_manifest.yml"
RUNTIME_ROOTS = (ROOT / "aicrm_next", ROOT / "scripts", ROOT / "tools")
TEXT_CONTRACT_ROOTS = (ROOT / "docs", ROOT / "deploy", ROOT / ".github")
API_FILE_PATTERN = re.compile(r"(?:^|/)(?:api|routes)\.py$")
ROUTE_DECORATOR_PATTERN = re.compile(r"@router\.(?:get|post|put|patch|delete|api_route|options)\(\s*[rubf]*[\"']([^\"']+)")
AUTHORIZATION_READ_PATTERN = re.compile(r"(?:headers|request\.headers)\.get\(\s*[\"']authorization[\"']", re.IGNORECASE)


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected mapping: {path}")
    return payload


def _python_files() -> list[Path]:
    return sorted(path for root in RUNTIME_ROOTS for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _active_contract_files() -> list[Path]:
    result: list[Path] = []
    for root in TEXT_CONTRACT_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".md", ".yml", ".yaml", ".json", ".service", ".timer"}:
                continue
            relative = path.relative_to(ROOT)
            if relative.parts[:2] in {("docs", "archive"), ("docs", "plans")}:
                continue
            if path in {INVENTORY, ROOT / "docs/architecture/runtime_contract_inventory.json"}:
                continue
            result.append(path)
    return sorted(result)


def _literal_query_reads(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return set()
    result: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != "get" or not node.args:
            continue
        owner = node.func.value
        if not isinstance(owner, ast.Attribute) or owner.attr != "query_params":
            continue
        argument = node.args[0]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            result.add(argument.value.lower())
    return result


def check_auth_credential_boundaries() -> dict[str, Any]:
    inventory = _load_yaml(INVENTORY)
    forbidden = dict(inventory.get("forbidden_runtime_contracts") or {})
    forbidden_env = {str(value) for value in forbidden.get("environment_keys") or ()}
    forbidden_schemes = {str(value) for value in forbidden.get("route_schemes") or ()}
    path_markers = tuple(str(value).lower() for value in forbidden.get("path_parameter_markers") or ())
    query_names = {str(value).lower() for value in forbidden.get("query_parameter_names") or ()}
    violations: list[dict[str, str]] = []

    for path in _python_files():
        relative = str(path.relative_to(ROOT))
        source = path.read_text(encoding="utf-8")
        for key in sorted(forbidden_env):
            if re.search(rf"[\"']{re.escape(key)}[\"']", source):
                violations.append({"rule": "forbidden_environment_key", "path": relative, "detail": key})
        for route in ROUTE_DECORATOR_PATTERN.findall(source):
            parameters = re.findall(r"\{([^}:]+)", route)
            for parameter in parameters:
                lowered = parameter.lower()
                if any(marker in lowered for marker in path_markers):
                    violations.append({"rule": "credential_path_parameter", "path": relative, "detail": route})
        for name in sorted(_literal_query_reads(path).intersection(query_names)):
            violations.append({"rule": "credential_query_parameter", "path": relative, "detail": name})
        if API_FILE_PATTERN.search(relative) and AUTHORIZATION_READ_PATTERN.search(source):
            violations.append({"rule": "route_local_authorization_read", "path": relative, "detail": "Authorization"})

    for path in _active_contract_files():
        relative = str(path.relative_to(ROOT))
        source = path.read_text(encoding="utf-8", errors="replace")
        for key in sorted(forbidden_env):
            if key in source:
                violations.append({"rule": "forbidden_credential_in_active_contract", "path": relative, "detail": key})

    auth_root = ROOT / "aicrm_next/platform/platform_foundation/auth_platform"
    for obsolete in ("oauthlib_provider.py", "oidc_signing.py", "http_signatures.py"):
        if (auth_root / obsolete).exists():
            violations.append({"rule": "obsolete_auth_module", "path": str((auth_root / obsolete).relative_to(ROOT)), "detail": obsolete})
    forbidden_architecture_terms = ("private_key_jwt", "dpop", "mtls", "opaque_access", "token_exchange")
    for path in sorted(auth_root.rglob("*.py")):
        source = path.read_text(encoding="utf-8").lower()
        for term in forbidden_architecture_terms:
            if term in source:
                violations.append({"rule": "forbidden_auth_architecture", "path": str(path.relative_to(ROOT)), "detail": term})

    route_payload = _load_yaml(ROUTE_MANIFEST)
    routes = list(route_payload.get("routes") or ())
    for entry in routes:
        scheme = str(entry.get("auth_scheme") or "")
        if scheme in forbidden_schemes:
            violations.append({"rule": "forbidden_route_scheme", "path": str(entry.get("path") or ""), "detail": scheme})
        if "token_purpose" in entry or "machine_audience" in entry or "machine_capability" in entry:
            violations.append({"rule": "obsolete_route_policy_field", "path": str(entry.get("path") or ""), "detail": scheme})

    return {
        "ok": not violations,
        "runtime_python_file_count": len(_python_files()),
        "active_contract_file_count": len(_active_contract_files()),
        "route_count": len(routes),
        "violation_count": len(violations),
        "violations": violations,
    }


def main() -> int:
    report = check_auth_credential_boundaries()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
