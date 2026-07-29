from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci.summarize_test_scope_shadow import (
    load_reviewed_legacy_only_tests,
    render_markdown,
    summarize_shadow_reports,
)


pytestmark = pytest.mark.unit


def _report(
    *,
    legacy_tests: list[str],
    candidate_tests: list[str],
    candidate_full: bool,
    legacy_full: bool | None = None,
    legacy_seconds: float | None = None,
    candidate_seconds: float | None = None,
    high_risk_reasons: list[str] | None = None,
    fallback_reasons: list[str] | None = None,
    runtime_paths_without_test_match: list[str] | None = None,
) -> dict:
    if legacy_full is None:
        legacy_full = candidate_full
    legacy = {
        "python_tests": legacy_tests,
        "needs_full_ci": legacy_full,
    }
    candidate = {
        "python_tests": candidate_tests,
        "needs_full_ci": candidate_full,
        "high_risk_reasons": high_risk_reasons or [],
        "fallback_reasons": fallback_reasons or [],
        "runtime_paths_without_test_match": runtime_paths_without_test_match or [],
    }
    if legacy_seconds is not None:
        legacy["estimated_python_work_seconds"] = legacy_seconds
    if candidate_seconds is not None:
        candidate["estimated_python_work_seconds"] = candidate_seconds
    return {
        "mode": "shadow",
        "legacy": legacy,
        "candidate": candidate,
        "comparison": {
            "legacy_only_python_tests": sorted(set(legacy_tests) - set(candidate_tests)),
            "candidate_would_avoid_full_regression": legacy_full and not candidate_full,
        },
    }


def _criteria(**overrides: int) -> dict[str, int]:
    values = {
        "minimum_pull_request_samples": 2,
        "minimum_observation_days": 1,
        "minimum_scoped_samples": 1,
        "minimum_high_risk_samples": 1,
        "maximum_normal_path_fallback_samples": 0,
        "maximum_high_risk_downgrades": 0,
        "maximum_unreviewed_legacy_only_tests": 0,
    }
    values.update(overrides)
    return values


def test_summary_deduplicates_pull_requests_and_requires_diverse_reviewed_evidence() -> None:
    reports = {
        101: _report(
            legacy_tests=["tests/test_context.py", "tests/test_global.py"],
            candidate_tests=["tests/test_context.py"],
            candidate_full=False,
        ),
        102: _report(
            legacy_tests=["tests/test_context.py", "tests/test_global.py"],
            candidate_tests=["tests/test_context.py"],
            candidate_full=False,
        ),
        201: _report(
            legacy_tests=["tests/test_callback.py", "tests/test_global.py"],
            candidate_tests=["tests/test_callback.py"],
            candidate_full=True,
            high_risk_reasons=["callbacks_and_external_effects"],
        ),
    }
    metadata = {
        101: {
            "run_id": 101,
            "event": "pull_request",
            "conclusion": "success",
            "pull_request_number": 10,
            "created_at": "2026-07-01T00:00:00Z",
            "head_sha": "a" * 40,
        },
        102: {
            "run_id": 102,
            "event": "pull_request",
            "conclusion": "success",
            "pull_request_number": 10,
            "created_at": "2026-07-02T00:00:00Z",
            "head_sha": "b" * 40,
        },
        201: {
            "run_id": 201,
            "event": "pull_request",
            "conclusion": "success",
            "pull_request_number": 20,
            "created_at": "2026-07-03T00:00:00Z",
            "head_sha": "c" * 40,
        },
    }

    summary = summarize_shadow_reports(
        reports,
        metadata,
        criteria=_criteria(),
        reviewed_legacy_only_tests={"tests/test_global.py"},
    )

    assert summary["eligible_pull_request_samples"] == 2
    assert [sample["run_id"] for sample in summary["samples"]] == [102, 201]
    assert [sample["pull_request_key"] for sample in summary["samples"]] == ["pr:10", "pr:20"]
    assert summary["observation_days"] == 1
    assert summary["scoped_samples"] == 1
    assert summary["high_risk_samples"] == 1
    assert summary["candidate_selection_reduction_ratio"] == 0.5
    assert summary["unreviewed_legacy_only_tests"] == []
    assert summary["automated_ready_for_explicit_cutover_review"] is True
    assert "READY FOR EXPLICIT REVIEW" in render_markdown(summary)


def test_summary_stays_in_shadow_for_fallbacks_unreviewed_tests_and_short_window() -> None:
    reports = {
        301: _report(
            legacy_tests=["tests/test_context.py", "tests/test_global.py"],
            candidate_tests=["tests/test_context.py"],
            candidate_full=True,
            fallback_reasons=["runtime_path_without_test_match"],
        )
    }
    metadata = {
        301: {
            "run_id": 301,
            "event": "pull_request",
            "conclusion": "success",
            "pull_request_number": 30,
            "created_at": "2026-07-03T00:00:00Z",
            "head_sha": "d" * 40,
        }
    }

    summary = summarize_shadow_reports(
        reports,
        metadata,
        criteria=_criteria(minimum_pull_request_samples=1, minimum_scoped_samples=0, minimum_high_risk_samples=0),
    )

    assert summary["fallback_samples"] == 1
    assert summary["unreviewed_legacy_only_tests"] == ["tests/test_global.py"]
    assert summary["gates"]["observation_days"]["passed"] is False
    assert summary["gates"]["normal_path_fallback_samples"]["passed"] is False
    assert summary["automated_ready_for_explicit_cutover_review"] is False
    assert "KEEP SHADOW MODE" in render_markdown(summary)


def test_summary_uses_duration_and_full_regression_avoidance_as_primary_cost_evidence() -> None:
    reports = {
        501: _report(
            legacy_tests=["tests/test_context.py"],
            candidate_tests=["tests/test_context.py", "tests/test_related.py"],
            legacy_full=True,
            candidate_full=False,
            legacy_seconds=100.0,
            candidate_seconds=20.0,
        ),
        502: _report(
            legacy_tests=["tests/test_other.py"],
            candidate_tests=["tests/test_other.py", "tests/test_related.py"],
            legacy_full=True,
            candidate_full=False,
            legacy_seconds=100.0,
            candidate_seconds=30.0,
        ),
    }
    metadata = {
        run_id: {
            "run_id": run_id,
            "event": "pull_request",
            "conclusion": "success",
            "pull_request_number": run_id,
            "created_at": f"2026-07-{run_id - 500:02d}T00:00:00Z",
            "head_sha": str(run_id)[0] * 40,
        }
        for run_id in reports
    }

    summary = summarize_shadow_reports(
        reports,
        metadata,
        criteria=_criteria(minimum_high_risk_samples=0),
    )

    assert summary["scoped_duration_evidence_samples"] == 2
    assert summary["scoped_legacy_estimated_python_work_seconds"] == 200.0
    assert summary["scoped_candidate_estimated_python_work_seconds"] == 50.0
    assert summary["candidate_estimated_work_reduction_ratio"] == 0.75
    assert summary["scoped_full_regressions_avoided"] == 2
    assert summary["candidate_file_selection_reduction_ratio"] == -1.0
    markdown = render_markdown(summary)
    assert "Estimated Python work reduction: 75.0%" in markdown
    assert "Full regressions avoided in scoped samples: 2 / 2" in markdown
    assert "Explicit test-file selection change: -100.0% reduction" in markdown


def test_summary_prioritizes_legacy_only_and_runtime_path_review_evidence() -> None:
    reports = {
        601: _report(
            legacy_tests=["tests/test_common.py", "tests/test_once.py"],
            candidate_tests=[],
            candidate_full=True,
            fallback_reasons=["runtime_path_without_test_match"],
            runtime_paths_without_test_match=["scripts/ops/one.py"],
        ),
        602: _report(
            legacy_tests=["tests/test_common.py"],
            candidate_tests=[],
            candidate_full=True,
            fallback_reasons=["runtime_path_without_test_match"],
            runtime_paths_without_test_match=["scripts/ops/one.py", "scripts/ops/two.py"],
        ),
    }
    metadata = {
        601: {
            "run_id": 601,
            "event": "pull_request",
            "conclusion": "success",
            "pull_request_number": 61,
            "head_branch": "codex/one",
            "created_at": "2026-07-01T00:00:00Z",
            "head_sha": "a" * 40,
        },
        602: {
            "run_id": 602,
            "event": "pull_request",
            "conclusion": "success",
            "pull_request_number": 62,
            "head_branch": "codex/two",
            "created_at": "2026-07-02T00:00:00Z",
            "head_sha": "b" * 40,
        },
    }

    summary = summarize_shadow_reports(
        reports,
        metadata,
        criteria=_criteria(minimum_scoped_samples=0, minimum_high_risk_samples=0),
    )

    assert summary["fallback_reason_counts"] == {"runtime_path_without_test_match": 2}
    assert summary["legacy_only_review_queue"][0] == {
        "occurrences": 2,
        "run_ids": [601, 602],
        "test_path": "tests/test_common.py",
    }
    assert summary["runtime_path_review_queue"] == [
        {"occurrences": 2, "path": "scripts/ops/one.py", "run_ids": [601, 602]},
        {"occurrences": 1, "path": "scripts/ops/two.py", "run_ids": [602]},
    ]
    markdown = render_markdown(summary)
    assert "Runtime-path fallback review queue" in markdown
    assert "`scripts/ops/one.py` | 2 | `601`, `602`" in markdown


def test_summary_uses_head_branch_when_github_no_longer_links_a_merged_pr() -> None:
    reports = {
        401: _report(
            legacy_tests=["tests/test_context.py"],
            candidate_tests=["tests/test_context.py"],
            candidate_full=False,
        )
    }
    metadata = {
        401: {
            "run_id": 401,
            "event": "pull_request",
            "conclusion": "success",
            "pull_request_number": None,
            "head_branch": "codex/context-read-model",
            "created_at": "2026-07-03T00:00:00Z",
            "head_sha": "e" * 40,
        }
    }

    summary = summarize_shadow_reports(
        reports,
        metadata,
        criteria=_criteria(
            minimum_pull_request_samples=1,
            minimum_observation_days=0,
            minimum_high_risk_samples=0,
        ),
    )

    assert summary["eligible_pull_request_samples"] == 1
    assert summary["samples"][0]["pull_request_key"] == "branch:codex/context-read-model"


def test_review_inventory_requires_explicit_disposition_and_rationale(tmp_path: Path) -> None:
    review = tmp_path / "review.json"
    review.write_text(
        '{"version":1,"reviews":[{"test_path":"tests/test_global.py","disposition":"legacy_overcoverage","rationale":"global contract stays in nightly"}]}',
        encoding="utf-8",
    )

    assert load_reviewed_legacy_only_tests(review) == {"tests/test_global.py"}

    review.write_text(
        '{"version":1,"reviews":[{"test_path":"tests/test_global.py","disposition":"legacy_overcoverage","rationale":""}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="rationale"):
        load_reviewed_legacy_only_tests(review)
