from __future__ import annotations

import json

import pytest

from aicrm_next.platform.platform_foundation.performance_contracts import (
    REQUIRED_PROFILES,
    evaluate_read_path_report,
    load_read_path_baselines,
    percentile,
)


def _passing_report(profile) -> dict:
    return {
        "dataset_rows": profile.dataset_rows,
        "sample_count": profile.sample_count,
        "query_count": profile.max_query_count,
        "max_page_rows": profile.page_limit,
        "p50_ms": profile.baseline_p95_ms * 0.5,
        "p95_ms": profile.baseline_p95_ms,
        "plans": [
            {
                "query": "list",
                "node_types": ["Index Scan", "Limit"],
                "indexes": ["ix_example"],
                "seq_scans": [],
            }
        ],
    }


def test_critical_read_baseline_covers_all_required_next_routes() -> None:
    profiles = load_read_path_baselines()

    assert set(profiles) == REQUIRED_PROFILES
    assert {profile.regression_factor for profile in profiles.values()} == {1.1}
    assert all(profile.sample_count >= 10 for profile in profiles.values())
    assert all(not evaluate_read_path_report(profile, _passing_report(profile)) for profile in profiles.values())


def test_customer_projection_baselines_budget_one_generation_pointer_lookup() -> None:
    profiles = load_read_path_baselines()

    # Double-buffer reads require one bounded singleton-state lookup.  Keep the
    # timeline ceiling at six and lock the other increases to exactly one query
    # so future repository calls cannot silently consume more budget.
    assert profiles["customer_list"].max_query_count == 3
    assert profiles["sidebar_workbench"].max_query_count == 10
    assert profiles["sidebar_timeline"].max_query_count == 6
    assert profiles["sidebar_recent_messages"].max_query_count == 4


def test_internal_events_admin_baseline_locks_scoped_runtime_summary_budget() -> None:
    profile = load_read_path_baselines()["internal_events_admin"]

    assert profile.route == "/api/admin/internal-events"
    assert profile.owner == "platform_foundation.internal_events"
    assert profile.max_query_count == 9
    assert profile.page_limit == 50
    assert profile.baseline_p95_ms == 250.0
    assert profile.allowed_large_seq_scan_relations == frozenset({"internal_event"})


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"query_count": 99}, "query count regression"),
        ({"max_page_rows": 999}, "pagination regression"),
        ({"p95_ms": 9999.0}, "p95 regression"),
        ({"plans": []}, "query plan evidence is required"),
    ],
)
def test_critical_read_contract_fails_closed_on_regressions(mutation: dict, message: str) -> None:
    profile = load_read_path_baselines()["customer_list"]
    report = {**_passing_report(profile), **mutation}

    assert any(message in failure for failure in evaluate_read_path_report(profile, report))


def test_critical_read_contract_rejects_large_unapproved_sequential_scan() -> None:
    profile = load_read_path_baselines()["questionnaire_admin"]
    report = _passing_report(profile)
    report["plans"] = [
        {
            "query": "list",
            "node_types": ["Seq Scan"],
            "indexes": [],
            "seq_scans": [
                {"relation": "questionnaire_submissions", "plan_rows": 20000, "actual_rows": 20000}
            ],
        }
    ]

    assert any("large sequential scan" in failure for failure in evaluate_read_path_report(profile, report))


def test_critical_read_baseline_requires_explicit_complete_review(tmp_path) -> None:
    payload = json.loads(
        (
            __import__("pathlib").Path(__file__).resolve().parents[1]
            / "docs/performance/critical_read_path_baselines.json"
        ).read_text(encoding="utf-8")
    )
    payload["profiles"].pop("admin_jobs")
    path = tmp_path / "baselines.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="profiles mismatch"):
        load_read_path_baselines(path)


def test_percentile_uses_nearest_rank_for_repeatable_reports() -> None:
    assert percentile([1, 2, 3, 4, 5], 50) == 3
    assert percentile([1, 2, 3, 4, 5], 95) == 5
