from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci.select_test_scope_v2 import (
    DEFAULT_POLICY,
    _estimated_python_work_seconds,
    _load_policy,
    build_shadow_report,
    render_step_summary,
    select_convention_scope,
)


pytestmark = pytest.mark.unit


def _write(root: Path, relative_path: str, source: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _policy() -> dict:
    return _load_policy(DEFAULT_POLICY)


def test_context_convention_selects_importing_tests_without_manual_scope_entries(tmp_path: Path) -> None:
    _write(tmp_path, "tests/test_data_health_api.py", "from aicrm_next.data_health.api import router\n")
    _write(tmp_path, "tests/test_commerce_api.py", "from aicrm_next.commerce.api import router\n")

    result = select_convention_scope(_policy(), ["aicrm_next/data_health/api.py"], root=tmp_path)

    assert result["contexts"] == ["data_health"]
    assert result["python_tests"] == ["tests/test_data_health_api.py"]
    assert result["needs_postgres"] is False
    assert result["needs_full_ci"] is False
    assert result["architecture_gate"] == "fast"


@pytest.mark.parametrize(
    ("changed_path", "expected_reason"),
    [
        ("aicrm_next/admin_auth/api.py", "authentication"),
        ("aicrm_next/public_product/h5_wechat_pay.py", "payment_refund_and_entitlement"),
        ("aicrm_next/channel_entry/api.py", "callbacks_and_external_effects"),
        ("migrations/versions/9999_demo.py", "schema_and_deploy"),
        ("scripts/ops/check_id_validation_release_readiness.py", "schema_and_deploy"),
        ("docs/ci/test_scope_manifest.yml", "ci_and_dependency_runtime"),
    ],
)
def test_high_risk_exceptions_keep_full_regression(changed_path: str, expected_reason: str, tmp_path: Path) -> None:
    _write(tmp_path, "tests/test_contract.py", f'CONTRACT_PATH = "{changed_path}"\n')

    result = select_convention_scope(_policy(), [changed_path], root=tmp_path)

    assert expected_reason in result["high_risk_reasons"]
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_changed_postgres_test_selects_itself_and_db_gate(tmp_path: Path) -> None:
    changed_test = "tests/test_customer_repository.py"
    _write(tmp_path, changed_test, "def test_repository(next_pg_schema):\n    pass\n")

    result = select_convention_scope(_policy(), [changed_test], root=tmp_path)

    assert result["python_tests"] == [changed_test]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is False
    assert result["architecture_gate"] == "db"


def test_selected_high_risk_marker_keeps_full_regression(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "tests/test_data_health_contract.py",
        "import pytest\nfrom aicrm_next.data_health.api import router\npytestmark = pytest.mark.high_risk\n",
    )

    result = select_convention_scope(_policy(), ["aicrm_next/data_health/api.py"], root=tmp_path)

    assert "selected_high_risk_or_slow_test" in result["high_risk_reasons"]
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_unknown_runtime_path_falls_back_to_full_but_docs_do_not(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()

    unknown = select_convention_scope(_policy(), ["new_runtime/bootstrap.py"], root=tmp_path)
    docs = select_convention_scope(_policy(), ["docs/notes/test-cleanup.md"], root=tmp_path)

    assert unknown["unclassified_paths"] == ["new_runtime/bootstrap.py"]
    assert unknown["fallback_reasons"] == ["unclassified_paths"]
    assert unknown["needs_full_ci"] is True
    assert docs["python_tests"] == []
    assert docs["fallback_reasons"] == []
    assert docs["needs_full_ci"] is False
    assert docs["architecture_gate"] == "none"


@pytest.mark.parametrize(
    ("runtime_path", "test_path", "import_source"),
    [
        (
            "scripts/ops/arm_wecom_callback_canary.py",
            "tests/test_arm_wecom_callback_canary.py",
            "from scripts.ops import arm_wecom_callback_canary as arm\n",
        ),
        (
            "tools/check_questionnaire_h5_oauth_readiness.py",
            "tests/test_questionnaire_h5_oauth_readiness.py",
            "from tools import check_questionnaire_h5_oauth_readiness as readiness\n",
        ),
    ],
)
def test_long_runtime_script_stem_matches_its_importing_contract_test(
    runtime_path: str,
    test_path: str,
    import_source: str,
    tmp_path: Path,
) -> None:
    _write(tmp_path, test_path, import_source)

    result = select_convention_scope(_policy(), [runtime_path], root=tmp_path)

    assert result["python_tests"] == [test_path]
    assert result["runtime_paths_without_test_match"] == []
    assert "runtime_path_without_test_match" not in result["fallback_reasons"]


def test_static_runtime_checker_uses_bounded_policy_override(tmp_path: Path) -> None:
    test_path = "tests/test_identity_customer_event_driven_postgres.py"
    _write(tmp_path, test_path, "import psycopg\n")

    result = select_convention_scope(
        _policy(),
        ["scripts/ops/check_identity_customer_event_driven.py"],
        root=tmp_path,
    )

    assert result["python_tests"] == [test_path]
    assert result["runtime_paths_without_test_match"] == []
    assert result["needs_postgres"] is True


def test_runtime_test_override_requires_a_rationale(tmp_path: Path) -> None:
    test_path = "tests/test_static_checker.py"
    _write(tmp_path, test_path, "def test_checker(): pass\n")
    policy = _policy()
    policy["runtime_test_overrides"] = [
        {
            "paths": ["scripts/ops/static_checker.py"],
            "python_tests": [test_path],
            "rationale": "",
        }
    ]

    with pytest.raises(SystemExit, match="rationale"):
        select_convention_scope(policy, ["scripts/ops/static_checker.py"], root=tmp_path)


def test_deleted_file_uses_safe_full_regression_fallback(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    deleted = "aicrm_next/data_health/retired.py"

    result = select_convention_scope(_policy(), [deleted], deleted_files=[deleted], root=tmp_path)

    assert result["deleted_files"] == [deleted]
    assert "deleted_files" in result["fallback_reasons"]
    assert result["needs_full_ci"] is True


def test_shadow_report_exposes_differences_without_becoming_authoritative() -> None:
    legacy = {
        "matched_scopes": ["data_health"],
        "python_tests": ["tests/test_data_health.py", "tests/test_global_contract.py"],
        "frontend_tests": [],
        "needs_postgres": False,
        "needs_frontend_build": False,
        "needs_full_ci": True,
        "architecture_gate": "full",
    }
    candidate = {
        "mode": "shadow",
        "changed_files": ["aicrm_next/data_health/api.py"],
        "contexts": ["data_health"],
        "python_tests": ["tests/test_data_health.py"],
        "frontend_tests": [],
        "needs_postgres": False,
        "needs_frontend_build": False,
        "needs_full_ci": False,
        "architecture_gate": "fast",
        "high_risk_reasons": [],
        "fallback_reasons": [],
        "unclassified_paths": [],
        "runtime_paths_without_test_match": [],
        "deleted_files": [],
    }

    report = build_shadow_report(legacy, candidate)
    summary = render_step_summary(report)

    assert report["legacy_authoritative"] is True
    assert report["comparison"]["legacy_only_python_tests"] == ["tests/test_global_contract.py"]
    assert report["comparison"]["candidate_would_avoid_full_regression"] is True
    assert report["comparison"]["ready_for_cutover"] is False
    assert "Observation only: the legacy selector remains authoritative" in summary


def test_duration_estimate_uses_full_baseline_or_selected_files() -> None:
    baseline = {
        "version": 1,
        "total_duration_seconds": 30.0,
        "files": {
            "tests/test_fast.py": {"duration_seconds": 2.0, "items": 1},
            "tests/test_slow.py": {"duration_seconds": 28.0, "items": 1},
        },
    }

    assert _estimated_python_work_seconds(
        {"needs_full_ci": False, "python_tests": ["tests/test_fast.py"]},
        baseline,
    ) == 2.0
    assert _estimated_python_work_seconds(
        {"needs_full_ci": True, "python_tests": ["tests/test_fast.py"]},
        baseline,
    ) == 30.0
