#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

APPLICATION = ROOT / "aicrm_next/crm/customer_read_model/application.py"
API = ROOT / "aicrm_next/crm/customer_read_model/api.py"
DISPATCH = ROOT / "aicrm_next/channels/integration_gateway/dispatch.py"
FRONTEND_COMPAT = ROOT / "aicrm_next/frontend_compat/legacy_routes.py"

CONTEXT_QUERY = "GetCustomerContextQuery"
LEGACY_CONTEXT_QUERY = "GetCustomerChatContextQuery"
BYPASS_QUERY_NAMES = {"GetCustomerDetailQuery", "GetCustomerTimelineQuery", "ListRecentMessagesQuery"}
SCAN_ROOTS = [ROOT / "aicrm_next"]
IGNORED_FILES = {
    APPLICATION,
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    return ""


def _function_calls(path: Path) -> list[dict[str, Any]]:
    tree = ast.parse(_read(path), filename=str(path))
    calls: list[dict[str, Any]] = []
    parents: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
            parents.append(node.name)
            self.generic_visit(node)
            parents.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node: ast.Call) -> Any:
            calls.append(
                {
                    "name": _call_name(node.func),
                    "function": parents[-1] if parents else "<module>",
                    "lineno": node.lineno,
                    "path": path,
                }
            )
            self.generic_visit(node)

    Visitor().visit(tree)
    return calls


def _validate_context_query_contract() -> list[str]:
    blockers: list[str] = []
    text = _read(APPLICATION)
    required = [
        "class GetCustomerContextQuery",
        "GetCustomerChatContextQuery = GetCustomerContextQuery",
        "identity_binding_summary",
        "recent_messages",
        "recent_timeline_events",
        "timeline",
        "source_status",
        "local_contract_probe",
        "production_unavailable",
        "page_error",
        "legacy_production_facade",
    ]
    for marker in required:
        if marker not in text:
            blockers.append(f"context_query_missing_contract_marker:{marker}")
    return blockers


def _validate_callers_use_context_query() -> list[str]:
    blockers: list[str] = []
    expectations = {
        API: [CONTEXT_QUERY, "get_sidebar_customer_context", "get_admin_customer_profile", "get_admin_customer_profile_tags"],
        DISPATCH: [CONTEXT_QUERY, "get_customer_context"],
        FRONTEND_COMPAT: ["/api/admin/customers/profile?external_userid="],
    }
    for path, markers in expectations.items():
        text = _read(path)
        for marker in markers:
            if marker not in text:
                blockers.append(f"caller_missing_context_query_marker:{path.relative_to(ROOT)}:{marker}")
    if LEGACY_CONTEXT_QUERY in _read(DISPATCH):
        blockers.append("mcp_dispatch_uses_legacy_chat_context_query_name")
    return blockers


def _find_bypass_combiners() -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for scan_root in SCAN_ROOTS:
        for path in scan_root.rglob("*.py"):
            if path in IGNORED_FILES:
                continue
            calls_by_function: dict[str, set[str]] = {}
            lines_by_function: dict[str, list[int]] = {}
            for call in _function_calls(path):
                if call["name"] not in BYPASS_QUERY_NAMES:
                    continue
                function = str(call["function"])
                calls_by_function.setdefault(function, set()).add(str(call["name"]))
                lines_by_function.setdefault(function, []).append(int(call["lineno"]))
            for function, names in calls_by_function.items():
                if BYPASS_QUERY_NAMES.issubset(names):
                    findings.append(
                        {
                            "path": str(path.relative_to(ROOT)),
                            "function": function,
                            "queries": sorted(names),
                            "lines": sorted(lines_by_function[function]),
                        }
                    )
    return findings


def run_check() -> dict[str, Any]:
    blockers = _validate_context_query_contract() + _validate_callers_use_context_query()
    bypass_combiners = _find_bypass_combiners()
    for finding in bypass_combiners:
        blockers.append(f"customer_context_bypass_combiner:{finding['path']}:{finding['function']}")
    return {
        "ok": not blockers,
        "blockers": sorted(set(blockers)),
        "warnings": [],
        "context_query": "aicrm_next.crm.customer_read_model.application.GetCustomerContextQuery",
        "bypass_combiners": bypass_combiners,
        "business_impact": "Keeps admin, sidebar, MCP, AI Assist, and automation customer context reads consistent.",
    }


def write_outputs(result: dict[str, Any], output_md: str | None, output_json: str | None) -> None:
    if output_json:
        Path(output_json).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if output_md:
        lines = [
            "# Customer Context Read Model Unification",
            "",
            f"- ok: `{str(result['ok']).lower()}`",
            f"- blockers: `{len(result['blockers'])}`",
            f"- context_query: `{result['context_query']}`",
            f"- business_impact: {result['business_impact']}",
            "",
            "## Bypass Combiners",
        ]
        if result["bypass_combiners"]:
            for finding in result["bypass_combiners"]:
                lines.append(f"- `{finding['path']}` `{finding['function']}` lines `{finding['lines']}`")
        else:
            lines.append("- none")
        lines.extend(["", "## Blockers"])
        lines.extend(f"- {blocker}" for blocker in result["blockers"])
        Path(output_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Customer Context read model unification.")
    parser.add_argument("--output-md", default="")
    parser.add_argument("--output-json", default="")
    args = parser.parse_args()
    result = run_check()
    write_outputs(result, args.output_md, args.output_json)
    print(json.dumps({"ok": result["ok"], "blockers": result["blockers"]}, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
