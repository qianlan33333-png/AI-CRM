#!/usr/bin/env python3
"""Build a non-destructive inventory of duplicate, slow, and stale pytest assets."""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Iterator, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE = ROOT / "docs" / "ci" / "pytest_duration_baseline.json"


@dataclass(frozen=True)
class TestFunction:
    path: str
    qualified_name: str
    line: int
    end_line: int
    body_fingerprint: str
    body_nodes: int
    assertion_count: int


def _test_nodes(module: ast.Module) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            yield node.name, node
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test_"):
                    yield f"{node.name}.{child.name}", child


def _normalized_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, int]:
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    module = ast.Module(body=body, type_ignores=[])
    node_count = sum(1 for _ in ast.walk(module))
    normalized = ast.dump(module, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest(), node_count


def _parse_test_file(path: Path, root: Path) -> tuple[list[TestFunction], int]:
    try:
        source = path.read_text(encoding="utf-8")
        module = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        raise ValueError(f"unable to parse test file: {path}") from exc
    relative = path.relative_to(root).as_posix()
    functions: list[TestFunction] = []
    for qualified_name, node in _test_nodes(module):
        fingerprint, body_nodes = _normalized_body(node)
        functions.append(
            TestFunction(
                path=relative,
                qualified_name=qualified_name,
                line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                body_fingerprint=fingerprint,
                body_nodes=body_nodes,
                assertion_count=sum(isinstance(child, ast.Assert) for child in ast.walk(node)),
            )
        )
    return functions, len(source.splitlines())


def _load_baseline(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to load duration baseline: {path}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("duration baseline must be a version 1 mapping")
    if not isinstance(payload.get("files"), dict):
        raise ValueError("duration baseline files must be a mapping")
    return payload


def audit_test_inventory(
    root: Path,
    *,
    duration_baseline: dict,
    slow_file_seconds: float = 30.0,
    oversized_file_lines: int = 1000,
    minimum_duplicate_body_nodes: int = 12,
) -> dict:
    if slow_file_seconds < 0 or oversized_file_lines <= 0 or minimum_duplicate_body_nodes <= 0:
        raise ValueError("audit thresholds must be positive")
    test_paths = sorted((root / "tests").rglob("test_*.py"))
    if not test_paths:
        raise ValueError("repository contains no pytest test files")
    functions: list[TestFunction] = []
    file_lines: dict[str, int] = {}
    for path in test_paths:
        parsed, lines = _parse_test_file(path, root)
        functions.extend(parsed)
        file_lines[path.relative_to(root).as_posix()] = lines

    definitions: defaultdict[tuple[str, str], list[TestFunction]] = defaultdict(list)
    fingerprints: defaultdict[str, list[TestFunction]] = defaultdict(list)
    for function in functions:
        definitions[(function.path, function.qualified_name)].append(function)
        if function.body_nodes >= minimum_duplicate_body_nodes:
            fingerprints[function.body_fingerprint].append(function)

    duplicate_definitions = [
        {
            "path": path,
            "qualified_name": qualified_name,
            "lines": [function.line for function in matches],
        }
        for (path, qualified_name), matches in sorted(definitions.items())
        if len(matches) > 1
    ]
    exact_body_candidates = []
    for fingerprint, matches in sorted(fingerprints.items()):
        unique_locations = {(match.path, match.qualified_name, match.line) for match in matches}
        if len(unique_locations) < 2:
            continue
        exact_body_candidates.append(
            {
                "fingerprint": fingerprint,
                "body_nodes": matches[0].body_nodes,
                "locations": [
                    {
                        "path": match.path,
                        "qualified_name": match.qualified_name,
                        "line": match.line,
                        "end_line": match.end_line,
                    }
                    for match in sorted(matches, key=lambda item: (item.path, item.line, item.qualified_name))
                ],
            }
        )
    exact_body_candidates.sort(key=lambda group: (-len(group["locations"]), -group["body_nodes"], group["fingerprint"]))

    baseline_files = duration_baseline["files"]
    current_files = set(file_lines)
    baseline_file_paths = set(str(path) for path in baseline_files)
    slow_files = []
    for path, entry in baseline_files.items():
        if path not in current_files or not isinstance(entry, dict):
            continue
        duration = float(entry.get("duration_seconds") or 0.0)
        if duration >= slow_file_seconds:
            slow_files.append(
                {
                    "path": path,
                    "duration_seconds": round(duration, 3),
                    "items": int(entry.get("items") or 0),
                }
            )
    slow_files.sort(key=lambda entry: (-entry["duration_seconds"], entry["path"]))
    oversized_files = [
        {"path": path, "lines": lines} for path, lines in sorted(file_lines.items(), key=lambda item: (-item[1], item[0])) if lines >= oversized_file_lines
    ]
    return {
        "version": 1,
        "mode": "observation_only",
        "test_file_count": len(test_paths),
        "test_function_count": len(functions),
        "assert_statement_count": sum(function.assertion_count for function in functions),
        "total_test_lines": sum(file_lines.values()),
        "duration_baseline": {
            "source_run_id": duration_baseline.get("source_run_id"),
            "source_sha": duration_baseline.get("source_sha"),
            "total_items": duration_baseline.get("total_items"),
            "total_duration_seconds": duration_baseline.get("total_duration_seconds"),
            "missing_current_files": sorted(current_files - baseline_file_paths),
            "retired_files": sorted(baseline_file_paths - current_files),
        },
        "thresholds": {
            "slow_file_seconds": slow_file_seconds,
            "oversized_file_lines": oversized_file_lines,
            "minimum_duplicate_body_nodes": minimum_duplicate_body_nodes,
        },
        "duplicate_test_definitions": duplicate_definitions,
        "exact_duplicate_body_candidates": exact_body_candidates,
        "slow_test_files": slow_files,
        "oversized_test_files": oversized_files,
    }


def render_markdown(report: dict) -> str:
    baseline = report["duration_baseline"]
    lines = [
        "# Pytest inventory audit",
        "",
        "**Observation only:** candidates in this report must not be deleted without a focused semantic review.",
        "",
        f"- Files / AST test functions / lines: {report['test_file_count']} / {report['test_function_count']} / {report['total_test_lines']}",
        f"- Baseline items / duration: {baseline.get('total_items') or 0} / {float(baseline.get('total_duration_seconds') or 0.0):.1f}s",
        f"- Baseline missing / retired files: {len(baseline['missing_current_files'])} / {len(baseline['retired_files'])}",
        f"- Duplicate definitions / exact-body candidates: {len(report['duplicate_test_definitions'])} / {len(report['exact_duplicate_body_candidates'])}",
        f"- Slow / oversized files: {len(report['slow_test_files'])} / {len(report['oversized_test_files'])}",
        "",
        "## Slow files",
        "",
        "| Test file | Baseline seconds | Items |",
        "| --- | ---: | ---: |",
    ]
    if report["slow_test_files"]:
        for entry in report["slow_test_files"][:30]:
            lines.append(f"| `{entry['path']}` | {entry['duration_seconds']:.1f} | {entry['items']} |")
    else:
        lines.append("| None above threshold | 0.0 | 0 |")
    lines.extend(("", "## Exact duplicate-body review queue", ""))
    candidates = report["exact_duplicate_body_candidates"]
    if candidates:
        for index, group in enumerate(candidates[:20], start=1):
            locations = ", ".join(f"`{item['path']}:{item['line']}`" for item in group["locations"])
            lines.append(f"{index}. {locations}")
    else:
        lines.append("No exact duplicate test bodies met the configured evidence threshold.")
    if report["duplicate_test_definitions"]:
        lines.extend(("", "## Duplicate definitions requiring correction", ""))
        for entry in report["duplicate_test_definitions"]:
            lines.append(f"- `{entry['path']}::{entry['qualified_name']}` is defined on lines " + ", ".join(str(line) for line in entry["lines"]))
    lines.extend(("", "> This audit produces a review queue; it does not alter CI routing or delete tests."))
    return "\n".join(lines) + "\n"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--duration-baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--slow-file-seconds", type=float, default=30.0)
    parser.add_argument("--oversized-file-lines", type=int, default=1000)
    parser.add_argument("--minimum-duplicate-body-nodes", type=int, default=12)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--step-summary", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = audit_test_inventory(
            args.root,
            duration_baseline=_load_baseline(args.duration_baseline),
            slow_file_seconds=args.slow_file_seconds,
            oversized_file_lines=args.oversized_file_lines,
            minimum_duplicate_body_nodes=args.minimum_duplicate_body_nodes,
        )
    except ValueError:
        print(json.dumps({"error": "pytest inventory audit failed", "ok": False}, sort_keys=True))
        return 2
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    markdown = render_markdown(report)
    if args.json_output:
        _write(args.json_output, serialized)
    if args.markdown_output:
        _write(args.markdown_output, markdown)
    if args.step_summary:
        with args.step_summary.open("a", encoding="utf-8") as handle:
            handle.write(markdown)
    if args.json:
        print(serialized, end="")
    else:
        print(
            "Pytest inventory audit: "
            f"files={report['test_file_count']}; "
            f"functions={report['test_function_count']}; "
            f"slow={len(report['slow_test_files'])}; "
            f"duplicate_candidates={len(report['exact_duplicate_body_candidates'])}; "
            f"baseline_missing={len(report['duration_baseline']['missing_current_files'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
