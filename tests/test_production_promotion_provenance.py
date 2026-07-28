from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from scripts.ops.validate_production_promotion import PromotionValidationError, validate_promotion


pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = "a" * 40
AI_AUDIENCE_RECOVERY_PATHS = {
    ".github/workflows/ai-audience-production-diagnostics.yml",
    "aicrm_next/extensions/ai/ai_audience_ops/refresh_intents.py",
    "aicrm_next/extensions/ai/ai_audience_ops/refresh_recovery.py",
    "tests/test_ai_audience_production_diagnostics_workflow.py",
    "tests/test_ai_audience_refresh_intents_postgres.py",
    "tests/test_ai_audience_runtime_hotfixes.py",
    "tests/test_external_effect_runtime_owner_postgres.py",
    "tests/test_internal_event_consumer_run_owner_postgres.py",
    "tests/test_internal_event_outbox_runtime_owner_postgres.py",
    "tests/test_webhook_inbox_runtime_owner_postgres.py",
}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write(root: Path, relative_path: str, content: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _repository(tmp_path: Path, *, unexpected_path: str | None = None) -> tuple[Path, str, str, list[str]]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Promotion Test")
    _git(root, "config", "user.email", "promotion@example.invalid")
    _write(root, "application.txt", "candidate\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "candidate")
    candidate_sha = _git(root, "rev-parse", "HEAD")

    changed_paths = [
        ".github/workflows/deploy.yml",
        ".github/workflows/promote-production.yml",
        "docs/releases/production_promotion.json",
        "scripts/ops/validate_production_promotion.py",
    ]
    if unexpected_path:
        changed_paths.append(unexpected_path)
    for index, relative_path in enumerate(changed_paths):
        _write(root, relative_path, f"promotion {index}\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "promotion controls")
    release_sha = _git(root, "rev-parse", "HEAD")
    return root, candidate_sha, release_sha, changed_paths


def _repository_with_existing_controls(
    tmp_path: Path,
    *,
    missing_control: str | None = None,
) -> tuple[Path, str, str, list[str]]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Promotion Test")
    _git(root, "config", "user.email", "promotion@example.invalid")
    _write(root, "application.txt", "candidate\n")
    required_controls = [
        ".github/workflows/deploy.yml",
        ".github/workflows/promote-production.yml",
        "docs/releases/production_promotion.json",
        "scripts/ops/validate_production_promotion.py",
    ]
    for index, relative_path in enumerate(required_controls):
        if relative_path != missing_control:
            _write(root, relative_path, f"control {index}\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "candidate with promotion controls")
    candidate_sha = _git(root, "rev-parse", "HEAD")

    changed_paths = ["docs/releases/production_promotion.json"]
    _write(root, changed_paths[0], "next promotion binding\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "next promotion binding")
    release_sha = _git(root, "rev-parse", "HEAD")
    return root, candidate_sha, release_sha, changed_paths


def _manifest(candidate_sha: str, allowed_paths: list[str]) -> dict:
    return {
        "version": 1,
        "source": {
            "repository": "qianlan333/AI-CRM-ID-refactor",
            "release_sha": SOURCE_SHA,
            "pull_request": 179,
            "ci_run_id": 101,
            "deploy_run_id": 102,
        },
        "target": {
            "repository": "qianlan333/AI-CRM",
            "candidate_sha": candidate_sha,
            "pull_request": 1734,
        },
        "allowed_post_candidate_paths": allowed_paths,
    }


def _source_run(*, run_id: int, name: str, event: str) -> dict:
    return {
        "id": run_id,
        "name": name,
        "event": event,
        "head_sha": SOURCE_SHA,
        "head_branch": "main",
        "status": "completed",
        "conclusion": "success",
    }


def _target_runs(release_sha: str) -> dict:
    return {
        "workflow_runs": [
            {
                "id": 103,
                "name": "CI Fast",
                "event": "push",
                "head_sha": release_sha,
                "head_branch": "main",
                "status": "completed",
                "conclusion": "success",
            }
        ]
    }


def _validate(root: Path, candidate_sha: str, release_sha: str, allowed_paths: list[str], **overrides) -> dict:
    arguments = {
        "root": root,
        "release_sha": release_sha,
        "validated_id_sha": SOURCE_SHA,
        "source_main_sha": SOURCE_SHA,
        "public_id_sha": SOURCE_SHA,
        "source_ci_run": _source_run(run_id=101, name="CI Fast", event="push"),
        "source_deploy_run": _source_run(run_id=102, name="Deploy ID Validation", event="workflow_run"),
        "target_ci_runs": _target_runs(release_sha),
    }
    arguments.update(overrides)
    return validate_promotion(_manifest(candidate_sha, allowed_paths), **arguments)


def test_promotion_binds_validated_id_evidence_to_ci_verified_target_candidate(tmp_path: Path) -> None:
    root, candidate_sha, release_sha, changed_paths = _repository(tmp_path)

    result = _validate(root, candidate_sha, release_sha, changed_paths)

    assert result["ok"] is True
    assert result["validated_id_sha"] == SOURCE_SHA
    assert result["target_candidate_sha"] == candidate_sha
    assert result["release_sha"] == release_sha
    assert result["target_ci_run_id"] == 103


def test_promotion_accepts_existing_controls_for_a_later_release_binding(tmp_path: Path) -> None:
    root, candidate_sha, release_sha, changed_paths = _repository_with_existing_controls(tmp_path)

    result = _validate(root, candidate_sha, release_sha, changed_paths)

    assert result["ok"] is True
    assert result["changed_paths"] == changed_paths


def test_promotion_rejects_release_that_drops_a_required_control(tmp_path: Path) -> None:
    root, candidate_sha, release_sha, changed_paths = _repository_with_existing_controls(
        tmp_path,
        missing_control=".github/workflows/deploy.yml",
    )

    with pytest.raises(PromotionValidationError, match="promotion control paths are missing"):
        _validate(root, candidate_sha, release_sha, changed_paths)


def test_promotion_rejects_id_sha_that_is_not_publicly_active(tmp_path: Path) -> None:
    root, candidate_sha, release_sha, changed_paths = _repository(tmp_path)

    with pytest.raises(PromotionValidationError, match="id-dev does not expose"):
        _validate(root, candidate_sha, release_sha, changed_paths, public_id_sha="b" * 40)


def test_promotion_rejects_unapproved_post_candidate_application_change(tmp_path: Path) -> None:
    root, candidate_sha, release_sha, changed_paths = _repository(
        tmp_path,
        unexpected_path="aicrm_next/crm/customer_read_model/api.py",
    )

    with pytest.raises(PromotionValidationError, match="unapproved post-candidate paths"):
        _validate(root, candidate_sha, release_sha, changed_paths[:-1])


def test_promotion_rejects_release_without_successful_main_ci(tmp_path: Path) -> None:
    root, candidate_sha, release_sha, changed_paths = _repository(tmp_path)

    with pytest.raises(PromotionValidationError, match="no successful main CI Fast run"):
        _validate(root, candidate_sha, release_sha, changed_paths, target_ci_runs={"workflow_runs": []})


def test_production_manifest_allows_exact_ai_audience_recovery_paths() -> None:
    manifest = json.loads(
        (ROOT / "docs" / "releases" / "production_promotion.json").read_text(encoding="utf-8")
    )

    allowed_paths = set(manifest["allowed_post_candidate_paths"])

    assert AI_AUDIENCE_RECOVERY_PATHS <= allowed_paths
    assert not any("*" in path for path in allowed_paths)
