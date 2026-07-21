from __future__ import annotations

import pytest

from scripts.ci.validate_pytest_duration_baseline_pr import (
    validate_baseline_artifacts,
    validate_baseline_source_run,
)


pytestmark = pytest.mark.unit


def _baseline() -> dict:
    return {
        "version": 1,
        "source_run_id": 123,
        "source_sha": "a" * 40,
        "files": {"tests/test_example.py": {"duration_seconds": 1.0, "items": 1}},
        "total_items": 1,
        "total_duration_seconds": 1.0,
    }


def _run() -> dict:
    return {
        "id": 123,
        "name": "Full Regression",
        "path": ".github/workflows/full-regression.yml",
        "event": "schedule",
        "status": "completed",
        "conclusion": "success",
        "head_branch": "main",
        "head_sha": "a" * 40,
        "repository": {"full_name": "qianlan333/AI-CRM-ID-refactor"},
        "head_repository": {"full_name": "qianlan333/AI-CRM-ID-refactor"},
    }


def test_baseline_source_run_requires_exact_trusted_main_provenance() -> None:
    assert validate_baseline_source_run(
        _baseline(),
        _run(),
        expected_repository="qianlan333/AI-CRM-ID-refactor",
        expected_base_sha="a" * 40,
    ) == 123


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("event", "pull_request", "event"),
        ("conclusion", "failure", "successful"),
        ("head_branch", "feature", "main"),
        ("head_sha", "b" * 40, "base SHA"),
        ("path", ".github/workflows/ci-fast.yml", "workflow path"),
    ],
)
def test_baseline_source_run_rejects_untrusted_run_shape(field: str, value: object, message: str) -> None:
    run = _run()
    run[field] = value

    with pytest.raises(ValueError, match=message):
        validate_baseline_source_run(
            _baseline(),
            run,
            expected_repository="qianlan333/AI-CRM-ID-refactor",
            expected_base_sha="a" * 40,
        )


def test_baseline_artifacts_require_all_eight_unexpired_python_shards() -> None:
    artifacts = {
        "artifacts": [
            {"name": f"full-python-shard-{index}-of-8", "expired": False}
            for index in range(1, 9)
        ]
    }

    validate_baseline_artifacts(artifacts)

    artifacts["artifacts"][3]["expired"] = True
    with pytest.raises(ValueError, match="eight unexpired"):
        validate_baseline_artifacts(artifacts)
