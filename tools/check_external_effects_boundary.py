from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "docs" / "architecture" / "external_effects_registry.yml"

RULE_DIRECT_EXTERNAL_EFFECT_CALL = "direct_external_effect_call"
REQUESTS_CALLEES = {"request", "get", "post", "put", "patch", "delete", "Session"}
HTTPX_CALLEES = {"request", "get", "post", "put", "patch", "delete", "Client", "AsyncClient"}
ALLOWED_PREFIXES = (
    "aicrm_next/platform_foundation/external_effects/",
    "aicrm_next/channels/integration_gateway/",
    "tests/",
    "tools/",
    "scripts/",
)
ALLOWED_FILES = {"aicrm_next/shared/http_client.py"}
REQUIRED_EFFECT_FIELDS = {
    "effect_key",
    "provider",
    "owner",
    "boundary",
    "allowed_runtime",
    "adapter_module",
    "migration_target",
    "idempotency_required",
    "audit_required",
}
REQUIRED_ALLOWLIST_FIELDS = {"path", "rule", "owner", "effect_key", "reason", "migration_target", "matches"}
BROAD_MATCHES = {
    "requests",
    "requests.",
    "requests.request",
    "requests.get",
    "requests.post",
    "requests.put",
    "requests.patch",
    "requests.delete",
    "requests.session",
    "httpx",
    "httpx.",
    "httpx.request",
    "httpx.get",
    "httpx.post",
    "httpx.put",
    "httpx.patch",
    "httpx.delete",
    "httpx.client",
    "httpx.asyncclient",
}


@dataclass(frozen=True)
class BoundaryViolation:
    path: Path
    line: int
    rule: str
    detected_callable: str
    owner: str
    reason: str
    suggestion: str

    def format(self, root: Path) -> str:
        try:
            display_path = self.path.relative_to(root)
        except ValueError:
            display_path = self.path
        return (
            f"{display_path}:{self.line}: {self.rule}: detected_callable={self.detected_callable}: "
            f"owner={self.owner}: {self.reason} Suggestion: {self.suggestion}"
        )


def load_config(path: str | Path) -> dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("external effects registry must be a mapping")
    effects = raw.get("effects") or []
    allowlist = raw.get("temporary_allowlist") or []
    _validate_effects(effects)
    _validate_temporary_allowlist(allowlist, {str(effect["effect_key"]) for effect in effects})
    return raw


def check_external_effects_boundary(root: Path = ROOT, config_path: Path = DEFAULT_CONFIG) -> list[BoundaryViolation]:
    config = load_config(config_path)
    allowlist = config.get("temporary_allowlist") or []
    violations: list[BoundaryViolation] = []

    for path in _iter_python_files(root):
        rel = path.relative_to(root).as_posix()
        if _is_allowed_boundary(rel):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            violations.append(
                BoundaryViolation(
                    path=path,
                    line=exc.lineno or 1,
                    rule="python_parse_error",
                    detected_callable="python.parse",
                    owner=_owner_for_path(rel),
                    reason=str(exc),
                    suggestion="Fix syntax before running external effects boundary checks.",
                )
            )
            continue

        lines = source.splitlines()
        aliases = _collect_http_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            detected = _detected_external_callable(node.func, aliases)
            if not detected:
                continue
            line_number = getattr(node, "lineno", 1)
            stripped_line = lines[line_number - 1].strip() if 0 < line_number <= len(lines) else ""
            if _is_allowlisted_call(allowlist, rel, stripped_line):
                continue
            violations.append(
                BoundaryViolation(
                    path=path,
                    line=line_number,
                    rule=RULE_DIRECT_EXTERNAL_EFFECT_CALL,
                    detected_callable=detected,
                    owner=_owner_for_path(rel),
                    reason=f"{rel} makes a direct external HTTP call outside the approved external effect boundaries.",
                    suggestion=(
                        "Move the call behind aicrm_next.channels.integration_gateway or "
                        "platform_foundation.external_effects; if this is historical debt, add only a precise "
                        "temporary allowlist entry with path, rule, owner, effect_key, reason, migration_target, and exact match."
                    ),
                )
            )
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate AI-CRM Next external effects boundaries.")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    violations = check_external_effects_boundary(root=root, config_path=Path(args.config).resolve())
    if violations:
        print("External effects boundary check failed:")
        for violation in violations:
            print(f"- {violation.format(root)}")
        return 1
    print(f"External effects boundary check OK: {args.config}")
    return 0


def _iter_python_files(root: Path) -> Iterable[Path]:
    candidates = [root / "aicrm_next", root / "tests", root / "tools", root / "scripts"]
    for base in candidates:
        if not base.exists():
            continue
        yield from (path for path in sorted(base.rglob("*.py")) if "__pycache__" not in path.parts)


def _is_allowed_boundary(rel: str) -> bool:
    return rel in ALLOWED_FILES or any(rel.startswith(prefix) for prefix in ALLOWED_PREFIXES)


def _collect_http_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"requests", "httpx"}:
                    aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in {"requests", "httpx"}:
            allowed = REQUESTS_CALLEES if node.module == "requests" else HTTPX_CALLEES
            for alias in node.names:
                if alias.name in allowed:
                    aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _detected_external_callable(func: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        module = aliases.get(func.value.id)
        if module == "requests" and func.attr in REQUESTS_CALLEES:
            return f"requests.{func.attr}"
        if module == "httpx" and func.attr in HTTPX_CALLEES:
            return f"httpx.{func.attr}"
    if isinstance(func, ast.Name):
        imported = aliases.get(func.id)
        if imported and (imported.startswith("requests.") or imported.startswith("httpx.")):
            return imported
    return None


def _is_allowlisted_call(allowlist: list[dict[str, Any]], path: str, stripped_line: str) -> bool:
    for entry in allowlist:
        if entry.get("path") != path:
            continue
        if entry.get("rule") != RULE_DIRECT_EXTERNAL_EFFECT_CALL:
            continue
        if stripped_line in set(entry.get("matches") or []):
            return True
    return False


def _owner_for_path(path: str) -> str:
    parts = path.split("/")
    if len(parts) >= 3 and parts[0] == "aicrm_next":
        return parts[1]
    return "unknown"


def _validate_effects(effects: list[dict[str, Any]]) -> None:
    if not isinstance(effects, list):
        raise ValueError("effects must be a list")
    for index, entry in enumerate(effects, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"effects entry #{index} must be a mapping")
        missing = sorted(field for field in REQUIRED_EFFECT_FIELDS if _missing_required_value(entry.get(field)))
        if missing:
            raise ValueError(f"effects entry #{index} missing required fields: {', '.join(missing)}")


def _validate_temporary_allowlist(allowlist: list[dict[str, Any]], effect_keys: set[str]) -> None:
    if not isinstance(allowlist, list):
        raise ValueError("temporary_allowlist must be a list")
    for index, entry in enumerate(allowlist, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"temporary_allowlist entry #{index} must be a mapping")
        missing = sorted(field for field in REQUIRED_ALLOWLIST_FIELDS if _missing_required_value(entry.get(field)))
        if missing:
            raise ValueError(f"temporary_allowlist entry #{index} missing required fields: {', '.join(missing)}")
        path = str(entry["path"])
        if any(marker in path for marker in ("*", "?", "[")) or path.endswith("/") or Path(path).suffix != ".py":
            raise ValueError(f"temporary_allowlist entry #{index} path must be an exact Python file path")
        if str(entry["rule"]) != RULE_DIRECT_EXTERNAL_EFFECT_CALL:
            raise ValueError(f"temporary_allowlist entry #{index} rule must be {RULE_DIRECT_EXTERNAL_EFFECT_CALL}")
        if str(entry["effect_key"]) not in effect_keys:
            raise ValueError(f"temporary_allowlist entry #{index} effect_key does not exist in effects")
        matches = entry.get("matches")
        if not isinstance(matches, list) or not all(isinstance(match, str) and match.strip() for match in matches):
            raise ValueError(f"temporary_allowlist entry #{index} matches must be non-empty strings")
        for match in matches:
            if _is_broad_match(match):
                raise ValueError(f"temporary_allowlist entry #{index} match is too broad: {match}")


def _is_broad_match(match: str) -> bool:
    normalized = match.strip().lower()
    return normalized in BROAD_MATCHES


def _missing_required_value(value: Any) -> bool:
    return value is None or value == "" or value == []


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
