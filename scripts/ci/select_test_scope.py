#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "docs" / "ci" / "test_scope_manifest.yml"
DEFAULT_DURATION_BASELINE = ROOT / "docs" / "ci" / "pytest_duration_baseline.json"
ARCHITECTURE_ORDER = {"none": 0, "fast": 1, "db": 2, "full": 3}


def _load_manifest(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ModuleNotFoundError as exc:
            raise SystemExit(
                f"{path} is not JSON-compatible and PyYAML is not installed. "
                "Keep the CI scope manifest JSON-compatible so selector can run before pip install."
            ) from exc
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise SystemExit(f"{path} must contain a mapping")
    return data


def _normalize_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    while normalized.startswith("/"):
        normalized = normalized[1:]
    return normalized


def _matches(path: str, pattern: str) -> bool:
    path = _normalize_path(path)
    pattern = _normalize_path(pattern)
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(f"{prefix}/")
    return fnmatch.fnmatchcase(path, pattern)


def _unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _is_direct_test_path(path: str) -> bool:
    normalized = _normalize_path(path)
    return (
        normalized.startswith("tests/test_")
        and normalized.endswith(".py")
    ) or (
        normalized.startswith("tests/frontend/")
        and normalized.endswith(".mjs")
    )


def _load_duration_baseline(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Unable to load pytest duration baseline: {path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), dict):
        raise SystemExit(f"{path} must contain a files duration mapping")
    return payload


def _estimated_python_work_seconds(
    python_tests: Iterable[str],
    baseline: dict,
    *,
    unknown_test_seconds: float,
) -> float:
    files = baseline.get("files", {})
    total = 0.0
    for test_path in python_tests:
        entry = files.get(test_path)
        duration = entry.get("duration_seconds") if isinstance(entry, dict) else None
        if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0:
            total += float(duration)
        else:
            total += unknown_test_seconds
    return round(total, 3)


def _exclusive_scope_override_matches(
    manifest: dict,
    scopes_by_name: dict[str, dict],
    path: str,
) -> list[dict] | None:
    overrides = manifest.get("exclusive_scope_overrides", [])
    if not isinstance(overrides, list):
        raise SystemExit("manifest.exclusive_scope_overrides must be a list")
    for override in overrides:
        patterns = override.get("paths", [])
        if not any(_matches(path, pattern) for pattern in patterns):
            continue
        selected_scopes: list[dict] = []
        missing_names: list[str] = []
        for name in override.get("scopes", []):
            scope = scopes_by_name.get(str(name))
            if scope is None:
                missing_names.append(str(name))
                continue
            selected_scopes.append(scope)
        if missing_names:
            raise SystemExit(f"Unknown override scope(s) for {path}: {', '.join(missing_names)}")
        return selected_scopes
    return None


def _git_diff_changes(*args: str) -> tuple[list[str], list[str]]:
    completed = subprocess.run(
        ["git", "diff", "--name-status", "--no-renames", *args],
        cwd=ROOT,
        text=True,
        check=True,
        capture_output=True,
    )
    changed_files: list[str] = []
    deleted_files: list[str] = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        status, separator, raw_path = line.partition("\t")
        if not separator or not raw_path.strip():
            raise SystemExit(f"Unable to parse git diff status line: {line!r}")
        path = _normalize_path(raw_path)
        changed_files.append(path)
        if status == "D":
            deleted_files.append(path)
    return _unique(changed_files), _unique(deleted_files)


def _changed_files_from_event() -> tuple[list[str], list[str]]:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return _git_diff_changes("HEAD^", "HEAD")

    payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")

    if event_name == "pull_request" and "pull_request" in payload:
        base_sha = payload["pull_request"]["base"]["sha"]
        head_sha = payload["pull_request"]["head"]["sha"]
        return _git_diff_changes(f"{base_sha}...{head_sha}")

    if event_name == "push":
        before = payload.get("before")
        after = payload.get("after") or "HEAD"
        if before and set(before) != {"0"}:
            return _git_diff_changes(before, after)
        return _git_diff_changes("HEAD^", after)

    return [], []


def _full_ci_requested() -> bool:
    if os.environ.get("AICRM_FORCE_FULL_CI", "").lower() in {"1", "true", "yes"}:
        return True

    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return False

    payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        inputs = payload.get("inputs") or {}
        value = inputs.get("full", "") if isinstance(inputs, dict) else ""
        return str(value).lower() in {"1", "true", "yes"}

    pull_request = payload.get("pull_request") or {}
    label_names = {
        str(label.get("name", "")).lower()
        for label in pull_request.get("labels", [])
        if isinstance(label, dict)
    }
    body = str(pull_request.get("body") or "").lower()
    title = str(pull_request.get("title") or "").lower()
    return "full-ci" in label_names or "[full-ci]" in body or "[full-ci]" in title


def _select(
    manifest: dict,
    changed_files: list[str],
    *,
    deleted_files: Iterable[str] = (),
    duration_baseline: dict | None = None,
) -> dict:
    scopes = manifest.get("scopes", [])
    if not isinstance(scopes, list):
        raise SystemExit("manifest.scopes must be a list")

    changed_files = _unique(_normalize_path(path) for path in changed_files if path.strip())
    deleted_file_set = {
        _normalize_path(path)
        for path in deleted_files
        if path.strip()
    }
    high_risk_paths = manifest.get("high_risk_paths", [])
    dependency_audit_paths = manifest.get("dependency_audit_paths", [])
    scopes_by_name = {str(scope.get("name")): scope for scope in scopes}

    matched_scopes: list[dict] = []
    matched_scope_names: set[str] = set()
    reported_scope_names: list[str] = []
    reported_scope_name_set: set[str] = set()
    direct_python_tests: list[str] = []
    direct_frontend_tests: list[str] = []
    direct_test_needs_postgres = False
    unmatched: list[str] = []
    unmapped_deleted: list[str] = []
    direct_test_patterns = manifest.get("direct_test_paths", [])
    if not isinstance(direct_test_patterns, list):
        raise SystemExit("manifest.direct_test_paths must be a list")

    for path in changed_files:
        override_matches = _exclusive_scope_override_matches(manifest, scopes_by_name, path)
        if override_matches is None:
            path_matches: list[dict] = []
            for scope in scopes:
                patterns = scope.get("paths", [])
                if any(_matches(path, pattern) for pattern in patterns):
                    path_matches.append(scope)
        else:
            path_matches = override_matches
        if _is_direct_test_path(path) and any(
            _matches(path, str(pattern)) for pattern in direct_test_patterns
        ):
            for scope in path_matches:
                name = str(scope.get("name"))
                if name not in reported_scope_name_set:
                    reported_scope_name_set.add(name)
                    reported_scope_names.append(name)
            if path.endswith(".py"):
                direct_python_tests.append(path)
                direct_test_needs_postgres = direct_test_needs_postgres or any(
                    bool(scope.get("needs_postgres")) for scope in path_matches
                )
            else:
                direct_frontend_tests.append(path)
            continue
        if not path_matches:
            if path in deleted_file_set:
                unmapped_deleted.append(path)
            else:
                unmatched.append(path)
            continue
        for scope in path_matches:
            name = str(scope.get("name"))
            if name not in reported_scope_name_set:
                reported_scope_name_set.add(name)
                reported_scope_names.append(name)
            if name not in matched_scope_names:
                matched_scope_names.add(name)
                matched_scopes.append(scope)

    high_risk = any(
        _matches(path, pattern)
        for path in changed_files
        for pattern in high_risk_paths
    )
    needs_dependency_audit = any(
        _matches(path, pattern)
        for path in changed_files
        for pattern in dependency_audit_paths
    )
    python_tests = _unique(
        [
            *direct_python_tests,
            *(
                test
                for scope in matched_scopes
                for test in scope.get("python_tests", [])
            ),
        ]
    )
    frontend_tests = _unique(
        [
            *direct_frontend_tests,
            *(
                test
                for scope in matched_scopes
                for test in scope.get("frontend_tests", [])
            ),
        ]
    )
    needs_postgres = direct_test_needs_postgres or any(bool(scope.get("needs_postgres")) for scope in matched_scopes)
    scope_forces_full = any(bool(scope.get("needs_full_ci")) for scope in matched_scopes)

    gate = "none"
    for scope in matched_scopes:
        candidate = str(scope.get("architecture_gate", "none"))
        if candidate not in ARCHITECTURE_ORDER:
            raise SystemExit(f"Unknown architecture_gate={candidate!r} in scope {scope.get('name')!r}")
        if ARCHITECTURE_ORDER[candidate] > ARCHITECTURE_ORDER[gate]:
            gate = candidate
    minimum_gate_rules = manifest.get("minimum_architecture_gate_rules", [])
    if not isinstance(minimum_gate_rules, list):
        raise SystemExit("manifest.minimum_architecture_gate_rules must be a list")
    for index, rule in enumerate(minimum_gate_rules):
        if not isinstance(rule, dict):
            raise SystemExit(f"minimum architecture gate rule {index} must be a mapping")
        patterns = rule.get("paths", [])
        if not isinstance(patterns, list):
            raise SystemExit(f"minimum architecture gate rule {index}.paths must be a list")
        candidate = str(rule.get("architecture_gate", "none"))
        if candidate not in ARCHITECTURE_ORDER:
            raise SystemExit(f"Unknown architecture_gate={candidate!r} in minimum gate rule {index}")
        if not any(_matches(path, pattern) for path in changed_files for pattern in patterns):
            continue
        if ARCHITECTURE_ORDER[candidate] > ARCHITECTURE_ORDER[gate]:
            gate = candidate
    if direct_test_needs_postgres and ARCHITECTURE_ORDER[gate] < ARCHITECTURE_ORDER["db"]:
        gate = "db"
    elif (direct_python_tests or direct_frontend_tests) and gate == "none":
        gate = "fast"
    if high_risk:
        gate = "full" if ARCHITECTURE_ORDER[gate] < ARCHITECTURE_ORDER["full"] else gate
    if unmapped_deleted:
        gate = "full"

    force_full = _full_ci_requested()
    budget = manifest.get("ci_budget", {})
    if not isinstance(budget, dict):
        raise SystemExit("manifest.ci_budget must be a mapping")
    unknown_test_seconds = float(budget.get("unknown_test_seconds", 60))
    small_max_seconds = float(budget.get("small_max_python_seconds", 180))
    large_max_seconds = float(budget.get("large_max_python_seconds", 1500))
    if duration_baseline is None:
        duration_baseline = _load_duration_baseline(DEFAULT_DURATION_BASELINE)
    estimated_seconds = _estimated_python_work_seconds(
        python_tests,
        duration_baseline,
        unknown_test_seconds=unknown_test_seconds,
    )
    budget_exceeded = not force_full and estimated_seconds > large_max_seconds
    if budget_exceeded and os.environ.get("GITHUB_EVENT_NAME") == "push":
        force_full = True
        budget_exceeded = False
    if force_full:
        ci_tier = "full"
    elif needs_dependency_audit or high_risk or scope_forces_full or estimated_seconds > small_max_seconds:
        ci_tier = "large"
    else:
        ci_tier = "small"
    return {
        "changed_files": changed_files,
        "matched_scopes": reported_scope_names,
        "unmatched_files": unmatched,
        "unmapped_deleted_files": unmapped_deleted,
        "python_tests": python_tests,
        "frontend_tests": frontend_tests,
        "needs_postgres": needs_postgres,
        "needs_dependency_audit": needs_dependency_audit,
        "needs_full_ci": high_risk or scope_forces_full or force_full or bool(unmapped_deleted),
        "force_full": force_full,
        "architecture_gate": gate,
        "ci_tier": ci_tier,
        "estimated_python_work_seconds": estimated_seconds,
        "budget_exceeded": budget_exceeded,
        "large_budget_seconds": large_max_seconds,
    }


def _write_github_output(path: str, result: dict) -> None:
    outputs = {
        "changed_files": " ".join(result["changed_files"]),
        "unmapped_deleted_files": " ".join(result["unmapped_deleted_files"]),
        "scopes": ",".join(result["matched_scopes"]),
        "python_tests": " ".join(result["python_tests"]),
        "frontend_tests": " ".join(result["frontend_tests"]),
        "needs_postgres": str(result["needs_postgres"]).lower(),
        "needs_dependency_audit": str(result["needs_dependency_audit"]).lower(),
        "needs_full_ci": str(result["needs_full_ci"]).lower(),
        "force_full": str(result["force_full"]).lower(),
        "architecture_gate": result["architecture_gate"],
        "ci_tier": result["ci_tier"],
        "estimated_python_work_seconds": str(result["estimated_python_work_seconds"]),
        "budget_exceeded": str(result["budget_exceeded"]).lower(),
    }
    with Path(path).open("a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--deleted-file", action="append", default=[])
    parser.add_argument("--changed-files-from", type=Path)
    parser.add_argument("--github-output")
    parser.add_argument("--duration-baseline", type=Path, default=DEFAULT_DURATION_BASELINE)
    parser.add_argument("--enforce-budget", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print selected scope as JSON for tests and debugging.")
    args = parser.parse_args(argv)

    changed_files = [_normalize_path(path) for path in args.changed_file]
    deleted_files = [_normalize_path(path) for path in args.deleted_file]
    if args.changed_files_from:
        changed_files.extend(
            _normalize_path(line)
            for line in args.changed_files_from.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    explicit_paths = bool(changed_files or deleted_files or args.changed_files_from)
    changed_files.extend(deleted_files)
    if not explicit_paths:
        changed_files, deleted_files = _changed_files_from_event()

    manifest = _load_manifest(args.manifest)
    duration_baseline = _load_duration_baseline(args.duration_baseline)
    result = _select(
        manifest,
        changed_files,
        deleted_files=deleted_files,
        duration_baseline=duration_baseline,
    )

    if args.github_output:
        _write_github_output(args.github_output, result)

    if result["unmatched_files"]:
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        message = manifest.get("unmapped_path_message", "Unmatched changed files")
        print(message, file=sys.stderr)
        for path in result["unmatched_files"]:
            print(f"- {path}", file=sys.stderr)
        return 2

    if result["budget_exceeded"] and args.enforce_budget:
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        print(
            "Selected CI scope exceeds the large-PR budget: "
            f"estimated_python_work_seconds={result['estimated_python_work_seconds']}; "
            f"limit={result['large_budget_seconds']}. Refine the scope or explicitly request full-ci.",
            file=sys.stderr,
        )
        return 3

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print(
        "Selected CI scopes: "
        f"{','.join(result['matched_scopes']) or 'none'}; "
        f"python_tests={len(result['python_tests'])}; "
        f"frontend_tests={len(result['frontend_tests'])}; "
        f"needs_postgres={str(result['needs_postgres']).lower()}; "
        f"needs_dependency_audit={str(result['needs_dependency_audit']).lower()}; "
        f"architecture_gate={result['architecture_gate']}; "
        f"needs_full_ci={str(result['needs_full_ci']).lower()}; "
        f"ci_tier={result['ci_tier']}; "
        f"estimated_python_work_seconds={result['estimated_python_work_seconds']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
