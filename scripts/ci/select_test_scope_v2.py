#!/usr/bin/env python3
"""Compare a convention-driven test scope with the legacy manifest selector.

This command is deliberately observational. It never changes the outputs used by
CI Fast; it produces JSON and a GitHub step summary so the candidate selector can
earn a safe cutover with real pull-request evidence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ci.select_test_scope import (  # noqa: E402
    DEFAULT_MANIFEST,
    _changed_files_from_event,
    _load_manifest,
    _matches,
    _normalize_path,
    _select,
    _unique,
)


DEFAULT_POLICY = ROOT / "docs" / "ci" / "test_scope_policy.yml"
DEFAULT_DURATION_BASELINE = ROOT / "docs" / "ci" / "pytest_duration_baseline.json"


def _load_policy(path: Path) -> dict:
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Unable to load JSON-compatible test scope policy: {path}") from exc
    if not isinstance(policy, dict) or policy.get("version") != 1:
        raise SystemExit(f"{path} must contain a version 1 mapping")
    if policy.get("mode") != "shadow":
        raise SystemExit("The convention selector must remain in shadow mode before explicit cutover approval")
    if not isinstance(policy.get("high_risk_rules"), list):
        raise SystemExit("policy.high_risk_rules must be a list")
    return policy


def _read_test_sources(root: Path) -> tuple[dict[str, str], dict[str, str]]:
    python_sources = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8").casefold()
        for path in sorted((root / "tests").rglob("test_*.py"))
    }
    frontend_sources = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8").casefold()
        for path in sorted((root / "tests" / "frontend").rglob("*.mjs"))
    }
    return python_sources, frontend_sources


def _path_context(path: str) -> str:
    parts = _normalize_path(path).split("/")
    if parts[0] == "aicrm_next" and len(parts) >= 2:
        return parts[1] if len(parts) >= 3 else Path(parts[1]).stem
    if parts[:2] == ["frontend", "admin"] and len(parts) >= 3:
        return parts[2]
    return ""


def _source_needles(path: str, policy: dict) -> tuple[str, ...]:
    normalized = _normalize_path(path).casefold()
    needles = [normalized]
    if normalized.endswith(".py"):
        needles.append(normalized[:-3].replace("/", "."))
        script_stem = Path(normalized).stem
        if normalized.startswith(("scripts/", "tools/")) and len(script_stem) >= 12:
            needles.append(script_stem)
    context = _path_context(normalized)
    if context:
        needles.extend((f"aicrm_next.{context}", f"aicrm_next/{context}"))
        needles.extend(str(alias).casefold() for alias in policy.get("context_aliases", {}).get(context, []))
    return tuple(_unique(needle for needle in needles if needle))


def _tests_for_path(path: str, sources: dict[str, str], policy: dict) -> list[str]:
    normalized = _normalize_path(path)
    if normalized in sources:
        return [normalized]
    needles = _source_needles(normalized, policy)
    context = _path_context(normalized)
    aliases = [context, *policy.get("context_aliases", {}).get(context, [])] if context else []
    selected: list[str] = []
    for test_path, source in sources.items():
        stem = Path(test_path).stem.casefold()
        source_match = any(needle in source for needle in needles)
        name_match = any(str(alias).casefold() in stem for alias in aliases if len(str(alias)) >= 4)
        if source_match or name_match:
            selected.append(test_path)
    return selected


def _override_tests_for_path(path: str, sources: dict[str, str], policy: dict) -> list[str]:
    selected: list[str] = []
    overrides = policy.get("runtime_test_overrides", [])
    if not isinstance(overrides, list):
        raise SystemExit("policy.runtime_test_overrides must be a list")
    for override in overrides:
        if not isinstance(override, dict):
            raise SystemExit("Every runtime test override must be a mapping")
        patterns = override.get("paths", [])
        tests = override.get("python_tests", [])
        if not isinstance(patterns, list) or not isinstance(tests, list):
            raise SystemExit("Runtime test override paths and python_tests must be lists")
        rationale = override.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise SystemExit("Runtime test override rationale must be non-empty")
        if not any(_matches(path, str(pattern)) for pattern in patterns):
            continue
        for test_path in tests:
            normalized_test = _normalize_path(str(test_path))
            if normalized_test not in sources:
                raise SystemExit(f"Runtime test override references an unknown test: {normalized_test}")
            selected.append(normalized_test)
    return _unique(selected)


def _high_risk_reasons(policy: dict, changed_files: Iterable[str]) -> list[str]:
    reasons: list[str] = []
    for rule in policy.get("high_risk_rules", []):
        if not isinstance(rule, dict):
            raise SystemExit("Every high-risk rule must be a mapping")
        patterns = rule.get("paths", [])
        if any(_matches(path, pattern) for path in changed_files for pattern in patterns):
            reasons.append(str(rule.get("name") or "unnamed_high_risk_rule"))
    return _unique(reasons)


def _is_docs_only(path: str, policy: dict) -> bool:
    return any(_matches(path, pattern) for pattern in policy.get("docs_only_paths", []))


def _is_known_runtime_path(path: str) -> bool:
    return path.startswith(("aicrm_next/", "scripts/", "tools/", "frontend/", "tests/")) or path in {
        "package.json",
        "package-lock.json",
        "pyproject.toml",
        "tsconfig.frontend.json",
    }


def select_convention_scope(
    policy: dict,
    changed_files: list[str],
    *,
    deleted_files: Iterable[str] = (),
    root: Path = ROOT,
) -> dict:
    changed_files = _unique(_normalize_path(path) for path in changed_files if path.strip())
    deleted_files = _unique(_normalize_path(path) for path in deleted_files if path.strip())
    python_sources, frontend_sources = _read_test_sources(root)
    high_risk_reasons = _high_risk_reasons(policy, changed_files)
    contexts = _unique(_path_context(path) for path in changed_files if _path_context(path))
    python_tests: list[str] = []
    frontend_tests: list[str] = []
    no_test_match: list[str] = []
    unclassified: list[str] = []

    for path in changed_files:
        matched_python = _tests_for_path(path, python_sources, policy)
        matched_python.extend(_override_tests_for_path(path, python_sources, policy))
        matched_python = _unique(matched_python)
        matched_frontend = _tests_for_path(path, frontend_sources, policy)
        python_tests.extend(matched_python)
        frontend_tests.extend(matched_frontend)
        if _is_docs_only(path, policy) or path in deleted_files:
            continue
        if not _is_known_runtime_path(path) and not any(
            _matches(path, pattern)
            for rule in policy.get("high_risk_rules", [])
            for pattern in rule.get("paths", [])
        ):
            unclassified.append(path)
        elif path.startswith(("aicrm_next/", "scripts/", "tools/")) and not (matched_python or matched_frontend):
            no_test_match.append(path)

    python_tests = _unique(python_tests)
    frontend_tests = _unique(frontend_tests)
    selected_python_source = "\n".join(python_sources.get(path, "") for path in python_tests)
    if any(
        str(indicator).casefold() in selected_python_source
        for indicator in policy.get("full_regression_test_indicators", [])
    ):
        high_risk_reasons = _unique([*high_risk_reasons, "selected_high_risk_or_slow_test"])
    needs_postgres = any(
        str(indicator).casefold() in selected_python_source
        for indicator in policy.get("postgres_indicators", [])
    )
    needs_frontend_build = any(
        _matches(path, pattern)
        for path in changed_files
        for pattern in policy.get("frontend_build_paths", [])
    )
    fallback_reasons: list[str] = []
    if deleted_files:
        fallback_reasons.append("deleted_files")
    if unclassified:
        fallback_reasons.append("unclassified_paths")
    if no_test_match:
        fallback_reasons.append("runtime_path_without_test_match")
    needs_full_ci = bool(high_risk_reasons or fallback_reasons)
    if needs_full_ci:
        architecture_gate = "full"
    elif needs_postgres:
        architecture_gate = "db"
    elif any(path.startswith(("aicrm_next/", "scripts/", "tools/")) for path in changed_files):
        architecture_gate = "fast"
    else:
        architecture_gate = "none"

    return {
        "mode": "shadow",
        "changed_files": changed_files,
        "contexts": contexts,
        "python_tests": python_tests,
        "frontend_tests": frontend_tests,
        "needs_postgres": needs_postgres,
        "needs_frontend_build": needs_frontend_build,
        "needs_full_ci": needs_full_ci,
        "architecture_gate": architecture_gate,
        "high_risk_reasons": high_risk_reasons,
        "fallback_reasons": fallback_reasons,
        "unclassified_paths": unclassified,
        "runtime_paths_without_test_match": no_test_match,
        "deleted_files": deleted_files,
    }


def compare_scopes(legacy: dict, candidate: dict) -> dict:
    legacy_tests = set(legacy.get("python_tests", []))
    candidate_tests = set(candidate.get("python_tests", []))
    covered = legacy_tests & candidate_tests
    coverage = 1.0 if not legacy_tests else len(covered) / len(legacy_tests)
    legacy_only = sorted(legacy_tests - candidate_tests)
    candidate_only = sorted(candidate_tests - legacy_tests)
    ready_for_cutover = not candidate.get("fallback_reasons") and (
        bool(candidate.get("needs_full_ci")) or not legacy_only
    )
    return {
        "legacy_only_python_tests": legacy_only,
        "candidate_only_python_tests": candidate_only,
        "legacy_python_test_coverage": round(coverage, 4),
        "candidate_would_avoid_full_regression": bool(legacy.get("needs_full_ci"))
        and not bool(candidate.get("needs_full_ci")),
        "ready_for_cutover": ready_for_cutover,
    }


def _load_duration_baseline(path: Path) -> dict:
    try:
        baseline = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Unable to load pytest duration baseline: {path}") from exc
    if not isinstance(baseline, dict) or baseline.get("version") != 1:
        raise SystemExit(f"{path} must contain a version 1 duration baseline")
    return baseline


def _estimated_python_work_seconds(scope: dict, baseline: dict) -> float:
    if scope.get("needs_full_ci"):
        return round(float(baseline.get("total_duration_seconds") or 0.0), 3)
    file_entries = baseline.get("files", {})
    average_file_seconds = float(baseline.get("total_duration_seconds") or 0.0) / max(len(file_entries), 1)
    estimate = 0.0
    for test_path in scope.get("python_tests", []):
        entry = file_entries.get(test_path)
        estimate += float(entry.get("duration_seconds") or 0.0) if isinstance(entry, dict) else average_file_seconds
    return round(estimate, 3)


def build_shadow_report(
    legacy: dict,
    candidate: dict,
    *,
    duration_baseline: dict | None = None,
) -> dict:
    legacy_view = {
        "matched_scopes": legacy.get("matched_scopes", []),
        "python_tests": legacy.get("python_tests", []),
        "frontend_tests": legacy.get("frontend_tests", []),
        "needs_postgres": legacy.get("needs_postgres", False),
        "needs_frontend_build": legacy.get("needs_frontend_build", False),
        "needs_full_ci": legacy.get("needs_full_ci", False),
        "architecture_gate": legacy.get("architecture_gate", "none"),
    }
    candidate_view = dict(candidate)
    if duration_baseline is not None:
        legacy_view["estimated_python_work_seconds"] = _estimated_python_work_seconds(legacy_view, duration_baseline)
        candidate_view["estimated_python_work_seconds"] = _estimated_python_work_seconds(candidate_view, duration_baseline)
    return {
        "mode": "shadow",
        "legacy_authoritative": True,
        "changed_files": candidate["changed_files"],
        "legacy": legacy_view,
        "candidate": candidate_view,
        "comparison": compare_scopes(legacy, candidate),
    }


def render_step_summary(report: dict) -> str:
    legacy = report["legacy"]
    candidate = report["candidate"]
    comparison = report["comparison"]
    legacy_only = comparison["legacy_only_python_tests"]
    lines = [
        "## Test scope v2 shadow comparison",
        "",
        "> Observation only: the legacy selector remains authoritative for every CI output.",
        "",
        f"- Changed files: {len(report['changed_files'])}",
        f"- Legacy: {len(legacy['python_tests'])} Python tests; full={str(legacy['needs_full_ci']).lower()}; gate={legacy['architecture_gate']}",
        f"- Candidate: {len(candidate['python_tests'])} Python tests; full={str(candidate['needs_full_ci']).lower()}; gate={candidate['architecture_gate']}",
        f"- Legacy test coverage: {comparison['legacy_python_test_coverage']:.1%}",
        f"- Candidate would avoid full regression: {str(comparison['candidate_would_avoid_full_regression']).lower()}",
        f"- Ready for cutover: {str(comparison['ready_for_cutover']).lower()}",
    ]
    if "estimated_python_work_seconds" in legacy:
        lines.append(
            "- Estimated Python work: "
            f"legacy={legacy['estimated_python_work_seconds']:.1f}s; "
            f"candidate={candidate['estimated_python_work_seconds']:.1f}s"
        )
    if candidate["high_risk_reasons"]:
        lines.append(f"- High-risk reasons: {', '.join(candidate['high_risk_reasons'])}")
    if candidate["fallback_reasons"]:
        lines.append(f"- Safety fallbacks: {', '.join(candidate['fallback_reasons'])}")
    if legacy_only:
        lines.extend(("", "<details><summary>Legacy-only Python tests (first 50)</summary>", ""))
        lines.extend(f"- `{path}`" for path in legacy_only[:50])
        lines.extend(("", "</details>"))
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--duration-baseline", type=Path, default=DEFAULT_DURATION_BASELINE)
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--deleted-file", action="append", default=[])
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--step-summary", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    changed_files = [_normalize_path(path) for path in args.changed_file]
    deleted_files = [_normalize_path(path) for path in args.deleted_file]
    if changed_files or deleted_files:
        changed_files = _unique([*changed_files, *deleted_files])
    else:
        changed_files, deleted_files = _changed_files_from_event()

    legacy = _select(_load_manifest(args.manifest), changed_files, deleted_files=deleted_files)
    candidate = select_convention_scope(
        _load_policy(args.policy),
        changed_files,
        deleted_files=deleted_files,
    )
    report = build_shadow_report(
        legacy,
        candidate,
        duration_baseline=_load_duration_baseline(args.duration_baseline),
    )
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(serialized, encoding="utf-8")
    if args.step_summary:
        with args.step_summary.open("a", encoding="utf-8") as handle:
            handle.write(render_step_summary(report))
    if args.json:
        print(serialized, end="")
    else:
        comparison = report["comparison"]
        print(
            "Test scope v2 shadow: "
            f"legacy_tests={len(report['legacy']['python_tests'])}; "
            f"candidate_tests={len(candidate['python_tests'])}; "
            f"candidate_full={str(candidate['needs_full_ci']).lower()}; "
            f"coverage={comparison['legacy_python_test_coverage']:.1%}; "
            f"ready={str(comparison['ready_for_cutover']).lower()}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
