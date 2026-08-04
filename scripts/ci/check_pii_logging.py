#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "docs" / "ci" / "pii_logging_contract.yml"
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}
OUTPUT_FUNCTIONS = {"print"}
RAW_REQUEST_ATTRIBUTES = {"body", "cookies", "form", "headers", "json", "path_params", "query_params"}
SENSITIVE_FRAGMENTS = {
    "access_token",
    "answers",
    "authorization",
    "cookie",
    "error",
    "exc",
    "external_userid",
    "message_content",
    "mobile",
    "openid",
    "out_trade_no",
    "password",
    "payload",
    "phone",
    "private_key",
    "request_body",
    "secret",
    "token",
    "transaction_id",
    "unionid",
    "userid",
}
SAFE_NAME_MARKERS = {
    "alg",
    "configured",
    "count",
    "fingerprint",
    "hash",
    "keys",
    "length",
    "masked",
    "present",
    "redacted",
    "status",
    "type",
}
APPROVED_SAFE_CALLS = {
    "redact_report",
    "redact_sensitive_data",
    "redact_sensitive_text",
    "safe_log_fields",
    "stable_hmac_identifier",
}
SAFE_STRUCTURAL_CALLS = {"bool", "len", "type"}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    function: str
    rule: str
    detail: str

    def render(self) -> str:
        location = f"{self.path}:{self.line}"
        function = f" function={self.function}" if self.function else ""
        return f"{location}: {self.rule}:{function} {self.detail}"


def _normalized_name(value: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value or ""))
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _qualified_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _sensitive_name(value: str) -> bool:
    normalized = _normalized_name(value)
    if not normalized:
        return False
    if any(marker in normalized.split("_") or normalized.endswith(f"_{marker}") for marker in SAFE_NAME_MARKERS):
        return False
    return any(fragment in normalized for fragment in SENSITIVE_FRAGMENTS)


def _approved_safe_call(node: ast.Call) -> bool:
    name = _normalized_name(_qualified_name(node.func).split(".")[-1])
    if name in SAFE_STRUCTURAL_CALLS:
        return True
    if name in APPROVED_SAFE_CALLS:
        return True
    if isinstance(node.func, ast.Attribute) and node.func.attr == "keys":
        return True
    return False


def _contains_sensitive_expression(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return False
    if isinstance(node, ast.Name):
        return _sensitive_name(node.id)
    if isinstance(node, ast.Attribute):
        qualified = _qualified_name(node)
        if qualified.startswith("request.") and node.attr in RAW_REQUEST_ATTRIBUTES:
            return True
        if _sensitive_name(node.attr):
            return True
    if isinstance(node, ast.Call) and _approved_safe_call(node):
        return False
    return any(_contains_sensitive_expression(child) for child in ast.iter_child_nodes(node))


class _Scanner(ast.NodeVisitor):
    def __init__(self, *, relative_path: str) -> None:
        self.relative_path = relative_path
        self.function_stack: list[str] = []
        self.findings: list[Finding] = []

    @property
    def function(self) -> str:
        return ".".join(self.function_stack)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        self.visit_FunctionDef(node)  # type: ignore[arg-type]

    def visit_Call(self, node: ast.Call) -> Any:
        qualified = _qualified_name(node.func)
        name = qualified.split(".")[-1]
        if name in LOG_METHODS and isinstance(node.func, ast.Attribute):
            self._check_log_call(node, method=name)
        elif name in OUTPUT_FUNCTIONS and isinstance(node.func, ast.Name):
            self._check_output_call(node)
        self.generic_visit(node)

    def _add(self, node: ast.AST, *, rule: str, detail: str) -> None:
        finding = Finding(
            path=self.relative_path,
            line=int(getattr(node, "lineno", 0) or 0),
            function=self.function,
            rule=rule,
            detail=detail,
        )
        if finding not in self.findings:
            self.findings.append(finding)

    def _check_log_call(self, node: ast.Call, *, method: str) -> None:
        if method == "exception":
            self._add(node, rule="exception_trace", detail="logger.exception may serialize an unsafe exception message")
        for keyword in node.keywords:
            if keyword.arg == "exc_info" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                self._add(node, rule="exception_trace", detail="exc_info=True may serialize an unsafe exception message")
        values = list(node.args[1:] if node.args and isinstance(node.args[0], ast.Constant) else node.args)
        values.extend(keyword.value for keyword in node.keywords if keyword.arg not in {"exc_info", "stack_info"})
        if any(_contains_sensitive_expression(value) for value in values):
            self._add(node, rule="sensitive_log_argument", detail="log argument must use an approved redaction or HMAC helper")
        if node.args and isinstance(node.args[0], ast.JoinedStr) and _contains_sensitive_expression(node.args[0]):
            self._add(node, rule="sensitive_log_argument", detail="formatted log message contains a sensitive expression")

    def _check_output_call(self, node: ast.Call) -> None:
        if any(_contains_sensitive_expression(value) for value in node.args):
            self._add(node, rule="sensitive_print_argument", detail="printed value must use an approved redaction helper")


def scan_file(path: Path, *, root: Path = ROOT) -> list[Finding]:
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative)
    except (OSError, UnicodeError, SyntaxError) as exc:
        return [Finding(relative, 0, "", "scanner_error", type(exc).__name__)]
    scanner = _Scanner(relative_path=relative)
    scanner.visit(tree)
    return scanner.findings


def _python_files(paths: Iterable[Path]) -> list[Path]:
    files: set[Path] = set()
    for path in paths:
        candidate = path if path.is_absolute() else ROOT / path
        if candidate.is_dir():
            files.update(item for item in candidate.rglob("*.py") if "__pycache__" not in item.parts)
        elif candidate.suffix == ".py":
            files.add(candidate)
    return sorted(files)


def load_manifest(path: Path) -> dict[str, Any]:
    body = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        import yaml

        payload = yaml.safe_load(body)
    return dict(payload or {})


def validate_allowlist(entries: list[dict[str, Any]], *, today: date | None = None) -> list[str]:
    current = today or date.today()
    errors: list[str] = []
    for index, entry in enumerate(entries):
        label = f"pii_logging_allowlist[{index}]"
        for key in ("path", "function", "rule", "owner", "reason", "expires_at"):
            if not str(entry.get(key) or "").strip():
                errors.append(f"{label}.{key} is required")
        try:
            expires_at = date.fromisoformat(str(entry.get("expires_at") or ""))
        except ValueError:
            errors.append(f"{label}.expires_at must be YYYY-MM-DD")
            continue
        if expires_at < current:
            errors.append(f"{label} expired on {expires_at.isoformat()}")
    return errors


def _allowlist_match(finding: Finding, entry: dict[str, Any]) -> bool:
    return (
        finding.path == str(entry.get("path") or "").strip()
        and finding.function == str(entry.get("function") or "").strip()
        and finding.rule == str(entry.get("rule") or "").strip()
    )


def apply_allowlist(findings: list[Finding], entries: list[dict[str, Any]]) -> tuple[list[Finding], list[str]]:
    remaining: list[Finding] = []
    used: set[int] = set()
    for finding in findings:
        matched = next((index for index, entry in enumerate(entries) if _allowlist_match(finding, entry)), None)
        if matched is None:
            remaining.append(finding)
        else:
            used.add(matched)
    unused = [f"pii_logging_allowlist[{index}] does not match a finding" for index in range(len(entries)) if index not in used]
    return remaining, unused


def scan_paths(paths: Iterable[Path], *, root: Path = ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for path in _python_files(paths):
        findings.extend(scan_file(path, root=root))
    return sorted(findings, key=lambda item: (item.path, item.line, item.rule, item.function))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reject unredacted PII and secret logging patterns.")
    parser.add_argument("paths", nargs="*", type=Path, default=[Path("aicrm_next"), Path("scripts")])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest = load_manifest(args.manifest)
    entries = list(manifest.get("pii_logging_allowlist") or [])
    configuration_errors = validate_allowlist(entries)
    findings = scan_paths(args.paths)
    remaining, unused = apply_allowlist(findings, entries)
    allowlist_messages = [*configuration_errors, *unused]
    for finding in remaining:
        print(finding.render())
    for allowlist_message in allowlist_messages:
        print(f"allowlist_error: {allowlist_message}")
    if remaining or allowlist_messages:
        return 1
    print("PII logging check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
