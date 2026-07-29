#!/usr/bin/env python3
"""Aggregate CI test-scope shadow artifacts into an objective cutover report.

The convention selector remains observational. This tool de-duplicates repeated
runs by pull request, evaluates repository-owned readiness thresholds, and keeps
legacy-only test differences blocked until they receive an explicit review.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
import math
from pathlib import Path
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = ROOT / "docs" / "ci" / "test_scope_policy.yml"
DEFAULT_REVIEW = ROOT / "docs" / "ci" / "test_scope_legacy_only_review.yml"


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to load JSON document: {path}") from exc


def load_cutover_criteria(path: Path) -> dict[str, int]:
    payload = _load_json(path)
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("test scope policy must be a version 1 mapping")
    raw = payload.get("cutover_criteria")
    if not isinstance(raw, dict):
        raise ValueError("test scope policy must define cutover_criteria")
    required = {
        "minimum_pull_request_samples",
        "minimum_observation_days",
        "minimum_scoped_samples",
        "minimum_high_risk_samples",
        "maximum_normal_path_fallback_samples",
        "maximum_high_risk_downgrades",
        "maximum_unreviewed_legacy_only_tests",
    }
    if set(raw) != required:
        raise ValueError("cutover_criteria keys do not match the supported contract")
    criteria: dict[str, int] = {}
    for key in sorted(required):
        value = raw.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"cutover criterion {key} must be a non-negative integer")
        criteria[key] = value
    return criteria


def load_reviewed_legacy_only_tests(path: Path) -> set[str]:
    payload = _load_json(path)
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("legacy-only review inventory must be a version 1 mapping")
    reviews = payload.get("reviews")
    if not isinstance(reviews, list):
        raise ValueError("legacy-only review inventory reviews must be a list")
    reviewed: set[str] = set()
    for review in reviews:
        if not isinstance(review, dict):
            raise ValueError("legacy-only review entries must be mappings")
        path_value = review.get("test_path")
        disposition = review.get("disposition")
        rationale = review.get("rationale")
        if not isinstance(path_value, str) or not path_value.startswith("tests/test_") or not path_value.endswith(".py"):
            raise ValueError("legacy-only review test_path must name a Python test file")
        if disposition not in {"candidate_includes", "legacy_overcoverage"}:
            raise ValueError("legacy-only review disposition is unsupported")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError("legacy-only review rationale must be non-empty")
        if path_value in reviewed:
            raise ValueError("legacy-only review contains a duplicate test_path")
        reviewed.add(path_value)
    return reviewed


def load_run_metadata(path: Path) -> dict[int, dict]:
    payload = _load_json(path)
    if not isinstance(payload, list):
        raise ValueError("run metadata must be a list")
    metadata: dict[int, dict] = {}
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("run metadata entries must be mappings")
        run_id = item.get("run_id")
        if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id <= 0:
            raise ValueError("run metadata run_id must be a positive integer")
        if run_id in metadata:
            raise ValueError("run metadata contains a duplicate run_id")
        metadata[run_id] = item
    return metadata


def _run_id_from_path(path: Path, root: Path) -> int | None:
    try:
        parts = path.relative_to(root).parts[:-1]
    except ValueError:
        return None
    for part in reversed(parts):
        if part.isdigit() and int(part) > 0:
            return int(part)
    return None


def load_shadow_reports(root: Path) -> dict[int, dict]:
    reports: dict[int, dict] = {}
    for path in sorted(root.rglob("test-scope-v2-shadow.json")):
        payload = _load_json(path)
        if not isinstance(payload, dict) or payload.get("mode") != "shadow":
            raise ValueError(f"shadow report has an unsupported contract: {path}")
        run_context = payload.get("run_context")
        context_run_id = run_context.get("run_id") if isinstance(run_context, dict) else None
        run_id = context_run_id if isinstance(context_run_id, int) else _run_id_from_path(path, root)
        if not isinstance(run_id, int) or run_id <= 0:
            raise ValueError(f"cannot resolve run id for shadow report: {path}")
        if run_id in reports:
            raise ValueError(f"duplicate shadow report for run {run_id}")
        reports[run_id] = payload
    return reports


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("run metadata created_at must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("run metadata created_at must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("run metadata created_at must include a timezone")
    return parsed


def _latest_successful_pull_request_samples(
    reports: dict[int, dict],
    metadata: dict[int, dict],
) -> list[dict]:
    by_pull_request: dict[str, dict] = {}
    for run_id, report in reports.items():
        item = metadata.get(run_id)
        if not item or item.get("event") != "pull_request" or item.get("conclusion") != "success":
            continue
        pull_request_number = item.get("pull_request_number")
        if isinstance(pull_request_number, int) and not isinstance(pull_request_number, bool) and pull_request_number > 0:
            pull_request_key = f"pr:{pull_request_number}"
        else:
            head_branch = item.get("head_branch")
            if not isinstance(head_branch, str) or not head_branch.strip():
                continue
            pull_request_number = None
            pull_request_key = f"branch:{head_branch.strip()}"
        sample = {
            "run_id": run_id,
            "pull_request_key": pull_request_key,
            "pull_request_number": pull_request_number,
            "head_branch": item.get("head_branch"),
            "created_at": item.get("created_at"),
            "head_sha": item.get("head_sha"),
            "report": report,
        }
        previous = by_pull_request.get(pull_request_key)
        if previous is None or (_timestamp(sample["created_at"]), run_id) > (
            _timestamp(previous["created_at"]),
            previous["run_id"],
        ):
            by_pull_request[pull_request_key] = sample
    return sorted(
        by_pull_request.values(),
        key=lambda sample: (_timestamp(sample["created_at"]), sample["pull_request_key"]),
    )


def _minimum_gate(actual: int, required: int) -> dict:
    return {"actual": actual, "required": required, "passed": actual >= required, "operator": ">="}


def _maximum_gate(actual: int, required: int) -> dict:
    return {"actual": actual, "required": required, "passed": actual <= required, "operator": "<="}


def _estimated_seconds(scope: dict) -> float | None:
    value = scope.get("estimated_python_work_seconds")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("estimated Python work must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError("estimated Python work must be finite and non-negative")
    return normalized


def summarize_shadow_reports(
    reports: dict[int, dict],
    metadata: dict[int, dict],
    *,
    criteria: dict[str, int],
    reviewed_legacy_only_tests: Iterable[str] = (),
) -> dict:
    samples = _latest_successful_pull_request_samples(reports, metadata)
    reviewed = set(reviewed_legacy_only_tests)
    high_risk_samples = 0
    scoped_samples = 0
    fallback_samples = 0
    normal_path_fallback_samples = 0
    high_risk_downgrades = 0
    legacy_test_total = 0
    candidate_test_total = 0
    scoped_legacy_test_total = 0
    scoped_candidate_test_total = 0
    scoped_duration_evidence_samples = 0
    scoped_legacy_estimated_seconds = 0.0
    scoped_candidate_estimated_seconds = 0.0
    scoped_full_regressions_avoided = 0
    legacy_full_regression_samples = 0
    candidate_full_regression_samples = 0
    fallback_reason_counts: Counter[str] = Counter()
    legacy_only_tests: set[str] = set()
    legacy_only_runs: defaultdict[str, set[int]] = defaultdict(set)
    runtime_path_runs: defaultdict[str, set[int]] = defaultdict(set)
    sample_rows: list[dict] = []

    for sample in samples:
        report = sample["report"]
        legacy = report.get("legacy") if isinstance(report.get("legacy"), dict) else {}
        candidate = report.get("candidate") if isinstance(report.get("candidate"), dict) else {}
        comparison = report.get("comparison") if isinstance(report.get("comparison"), dict) else {}
        high_risk_reasons = candidate.get("high_risk_reasons") if isinstance(candidate.get("high_risk_reasons"), list) else []
        fallback_reasons = candidate.get("fallback_reasons") if isinstance(candidate.get("fallback_reasons"), list) else []
        runtime_paths = (
            candidate.get("runtime_paths_without_test_match")
            if isinstance(candidate.get("runtime_paths_without_test_match"), list)
            else []
        )
        legacy_full = legacy.get("needs_full_ci") is True
        candidate_full = candidate.get("needs_full_ci") is True
        legacy_seconds = _estimated_seconds(legacy)
        candidate_seconds = _estimated_seconds(candidate)
        is_high_risk = bool(high_risk_reasons)
        is_fallback = bool(fallback_reasons)
        high_risk_samples += int(is_high_risk)
        scoped_samples += int(not candidate_full)
        fallback_samples += int(is_fallback)
        normal_path_fallback_samples += int(is_fallback and not is_high_risk)
        high_risk_downgrades += int(is_high_risk and not candidate_full)
        legacy_full_regression_samples += int(legacy_full)
        candidate_full_regression_samples += int(candidate_full)
        fallback_reason_counts.update(str(reason) for reason in fallback_reasons)
        for runtime_path in runtime_paths:
            runtime_path_runs[str(runtime_path)].add(sample["run_id"])
        legacy_tests = legacy.get("python_tests") if isinstance(legacy.get("python_tests"), list) else []
        candidate_tests = candidate.get("python_tests") if isinstance(candidate.get("python_tests"), list) else []
        legacy_test_total += len(legacy_tests)
        candidate_test_total += len(candidate_tests)
        if not candidate_full:
            scoped_legacy_test_total += len(legacy_tests)
            scoped_candidate_test_total += len(candidate_tests)
            scoped_full_regressions_avoided += int(legacy_full)
            if legacy_seconds is not None and candidate_seconds is not None:
                scoped_duration_evidence_samples += 1
                scoped_legacy_estimated_seconds += legacy_seconds
                scoped_candidate_estimated_seconds += candidate_seconds
        current_legacy_only = comparison.get("legacy_only_python_tests")
        if isinstance(current_legacy_only, list):
            for test_path in current_legacy_only:
                normalized_test_path = str(test_path)
                legacy_only_tests.add(normalized_test_path)
                legacy_only_runs[normalized_test_path].add(sample["run_id"])
        sample_rows.append(
            {
                "pull_request_number": sample["pull_request_number"],
                "pull_request_key": sample["pull_request_key"],
                "head_branch": sample["head_branch"],
                "run_id": sample["run_id"],
                "created_at": sample["created_at"],
                "head_sha": sample["head_sha"],
                "legacy_needs_full_ci": legacy_full,
                "candidate_needs_full_ci": candidate_full,
                "candidate_would_avoid_full_regression": legacy_full and not candidate_full,
                "high_risk_reasons": high_risk_reasons,
                "fallback_reasons": fallback_reasons,
                "runtime_paths_without_test_match": runtime_paths,
                "legacy_python_tests": len(legacy_tests),
                "candidate_python_tests": len(candidate_tests),
                "legacy_estimated_python_work_seconds": legacy_seconds,
                "candidate_estimated_python_work_seconds": candidate_seconds,
            }
        )

    if len(samples) >= 2:
        observation_seconds = (_timestamp(samples[-1]["created_at"]) - _timestamp(samples[0]["created_at"])).total_seconds()
        observation_days = max(0, int(observation_seconds // 86400))
    else:
        observation_days = 0
    unreviewed = sorted(legacy_only_tests - reviewed)
    file_reduction = 0.0 if scoped_legacy_test_total == 0 else 1.0 - (scoped_candidate_test_total / scoped_legacy_test_total)
    estimated_work_reduction = (
        None
        if scoped_duration_evidence_samples == 0 or scoped_legacy_estimated_seconds <= 0
        else 1.0 - (scoped_candidate_estimated_seconds / scoped_legacy_estimated_seconds)
    )
    legacy_only_review_queue = sorted(
        (
            {
                "test_path": test_path,
                "occurrences": len(legacy_only_runs[test_path]),
                "run_ids": sorted(legacy_only_runs[test_path]),
            }
            for test_path in unreviewed
        ),
        key=lambda item: (-item["occurrences"], item["test_path"]),
    )
    runtime_path_review_queue = sorted(
        (
            {
                "path": runtime_path,
                "occurrences": len(run_ids),
                "run_ids": sorted(run_ids),
            }
            for runtime_path, run_ids in runtime_path_runs.items()
        ),
        key=lambda item: (-item["occurrences"], item["path"]),
    )
    gates = {
        "pull_request_samples": _minimum_gate(len(samples), criteria["minimum_pull_request_samples"]),
        "observation_days": _minimum_gate(observation_days, criteria["minimum_observation_days"]),
        "scoped_samples": _minimum_gate(scoped_samples, criteria["minimum_scoped_samples"]),
        "high_risk_samples": _minimum_gate(high_risk_samples, criteria["minimum_high_risk_samples"]),
        "normal_path_fallback_samples": _maximum_gate(
            normal_path_fallback_samples,
            criteria["maximum_normal_path_fallback_samples"],
        ),
        "high_risk_downgrades": _maximum_gate(high_risk_downgrades, criteria["maximum_high_risk_downgrades"]),
        "unreviewed_legacy_only_tests": _maximum_gate(len(unreviewed), criteria["maximum_unreviewed_legacy_only_tests"]),
    }
    return {
        "version": 1,
        "mode": "shadow",
        "legacy_authoritative": True,
        "total_reports": len(reports),
        "metadata_matched_reports": sum(run_id in metadata for run_id in reports),
        "eligible_pull_request_samples": len(samples),
        "observation_days": observation_days,
        "scoped_samples": scoped_samples,
        "high_risk_samples": high_risk_samples,
        "fallback_samples": fallback_samples,
        "normal_path_fallback_samples": normal_path_fallback_samples,
        "high_risk_downgrades": high_risk_downgrades,
        "legacy_python_test_selections": legacy_test_total,
        "candidate_python_test_selections": candidate_test_total,
        "scoped_legacy_python_test_selections": scoped_legacy_test_total,
        "scoped_candidate_python_test_selections": scoped_candidate_test_total,
        "candidate_selection_reduction_ratio": round(file_reduction, 4),
        "candidate_file_selection_reduction_ratio": round(file_reduction, 4),
        "scoped_duration_evidence_samples": scoped_duration_evidence_samples,
        "scoped_legacy_estimated_python_work_seconds": round(scoped_legacy_estimated_seconds, 3),
        "scoped_candidate_estimated_python_work_seconds": round(scoped_candidate_estimated_seconds, 3),
        "candidate_estimated_work_reduction_ratio": (
            None if estimated_work_reduction is None else round(estimated_work_reduction, 4)
        ),
        "legacy_full_regression_samples": legacy_full_regression_samples,
        "candidate_full_regression_samples": candidate_full_regression_samples,
        "scoped_full_regressions_avoided": scoped_full_regressions_avoided,
        "fallback_reason_counts": dict(sorted(fallback_reason_counts.items())),
        "legacy_only_tests": sorted(legacy_only_tests),
        "reviewed_legacy_only_tests": sorted(legacy_only_tests & reviewed),
        "unreviewed_legacy_only_tests": unreviewed,
        "legacy_only_review_queue": legacy_only_review_queue,
        "runtime_path_review_queue": runtime_path_review_queue,
        "criteria": criteria,
        "gates": gates,
        "automated_ready_for_explicit_cutover_review": all(gate["passed"] for gate in gates.values()),
        "samples": sample_rows,
    }


def render_markdown(summary: dict) -> str:
    status = "READY FOR EXPLICIT REVIEW" if summary["automated_ready_for_explicit_cutover_review"] else "KEEP SHADOW MODE"
    work_reduction = summary.get("candidate_estimated_work_reduction_ratio")
    if work_reduction is None:
        work_reduction_line = "- Estimated Python work reduction: unavailable (no complete duration evidence)"
    else:
        work_reduction_line = f"- Estimated Python work reduction: {work_reduction:.1%}"
    lines = [
        "# Test scope v2 observation report",
        "",
        f"**Decision: {status}.** The legacy selector remains authoritative.",
        "",
        f"- Eligible pull requests: {summary['eligible_pull_request_samples']}",
        f"- Observation window: {summary['observation_days']} days",
        f"- Scoped / high-risk samples: {summary['scoped_samples']} / {summary['high_risk_samples']}",
        work_reduction_line,
        "- Estimated scoped Python work: "
        f"legacy={summary['scoped_legacy_estimated_python_work_seconds']:.1f}s; "
        f"candidate={summary['scoped_candidate_estimated_python_work_seconds']:.1f}s; "
        f"evidence={summary['scoped_duration_evidence_samples']} samples",
        "- Full regressions avoided in scoped samples: "
        f"{summary['scoped_full_regressions_avoided']} / {summary['scoped_samples']}",
        "- Explicit test-file selection change: "
        f"{summary['candidate_file_selection_reduction_ratio']:.1%} reduction (diagnostic only)",
        f"- Safety fallbacks / normal-path fallbacks / high-risk downgrades: {summary['fallback_samples']} / {summary['normal_path_fallback_samples']} / {summary['high_risk_downgrades']}",
        "",
        "## Cutover gates",
        "",
        "| Gate | Actual | Required | Result |",
        "| --- | ---: | ---: | --- |",
    ]
    for name, gate in summary["gates"].items():
        result = "PASS" if gate["passed"] else "WAIT"
        lines.append(f"| `{name}` | {gate['actual']} | {gate['operator']} {gate['required']} | {result} |")
    unreviewed = summary["unreviewed_legacy_only_tests"]
    review_queue = summary["legacy_only_review_queue"]
    lines.extend(("", "## Legacy-only review queue", ""))
    if unreviewed:
        lines.append(f"{len(unreviewed)} unique tests still need an explicit `candidate_includes` or `legacy_overcoverage` review before cutover.")
        lines.append("")
        lines.extend(("| Test file | Occurrences | Sample runs |", "| --- | ---: | --- |"))
        for item in review_queue[:50]:
            run_ids = ", ".join(f"`{run_id}`" for run_id in item["run_ids"])
            lines.append(f"| `{item['test_path']}` | {item['occurrences']} | {run_ids} |")
    else:
        lines.append("No unreviewed legacy-only tests remain in the eligible sample set.")
    runtime_queue = summary["runtime_path_review_queue"]
    lines.extend(("", "## Runtime-path fallback review queue", ""))
    if runtime_queue:
        lines.extend(("| Runtime path | Occurrences | Sample runs |", "| --- | ---: | --- |"))
        for item in runtime_queue[:50]:
            run_ids = ", ".join(f"`{run_id}`" for run_id in item["run_ids"])
            lines.append(f"| `{item['path']}` | {item['occurrences']} | {run_ids} |")
    else:
        lines.append("No runtime paths are relying on the no-test-match fallback.")
    lines.extend(
        (
            "",
            "> This report is observational. It cannot switch the authoritative selector or delete tests.",
        )
    )
    return "\n".join(lines) + "\n"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-root", type=Path, required=True)
    parser.add_argument("--run-metadata", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--legacy-only-review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--step-summary", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = summarize_shadow_reports(
            load_shadow_reports(args.reports_root),
            load_run_metadata(args.run_metadata),
            criteria=load_cutover_criteria(args.policy),
            reviewed_legacy_only_tests=load_reviewed_legacy_only_tests(args.legacy_only_review),
        )
    except ValueError:
        print(json.dumps({"error": "test scope shadow summary failed", "ok": False}, sort_keys=True))
        return 2
    serialized = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    markdown = render_markdown(summary)
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
            "Test scope shadow summary: "
            f"samples={summary['eligible_pull_request_samples']}; "
            f"days={summary['observation_days']}; "
            f"scoped={summary['scoped_samples']}; "
            f"high_risk={summary['high_risk_samples']}; "
            f"estimated_reduction={summary['candidate_estimated_work_reduction_ratio']}; "
            f"full_avoided={summary['scoped_full_regressions_avoided']}; "
            f"unreviewed={len(summary['unreviewed_legacy_only_tests'])}; "
            f"ready={str(summary['automated_ready_for_explicit_cutover_review']).lower()}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
