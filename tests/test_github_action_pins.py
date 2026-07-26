from __future__ import annotations

from pathlib import Path

from scripts.ci.check_github_action_pins import TRUSTED_ACTIONS, check_workflows


ROOT = Path(__file__).resolve().parents[1]


def _write_workflow(root: Path, source: str) -> None:
    workflow_dir = root / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text(source, encoding="utf-8")


def test_repository_external_actions_are_immutable_and_trusted() -> None:
    errors, workflow_count, external_use_count = check_workflows(ROOT)

    assert errors == []
    # AI-CRM owns CI, regression and test-governance workflows plus the guarded
    # production promotion, runtime-config control, and reusable production
    # deployment workflows.
    assert workflow_count == 14
    assert external_use_count > 0


def test_mutable_action_tag_is_rejected(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "jobs:\n  test:\n    steps:\n      - uses: actions/checkout@v7\n")

    errors, _, _ = check_workflows(tmp_path)

    assert errors == [".github/workflows/ci.yml:4: mutable action ref is forbidden: actions/checkout@v7"]


def test_unapproved_action_sha_is_rejected(tmp_path: Path) -> None:
    bad_sha = "0" * 40
    _write_workflow(tmp_path, f"jobs:\n  test:\n    steps:\n      - uses: actions/checkout@{bad_sha}\n")

    errors, _, _ = check_workflows(tmp_path)

    trusted_sha, trusted_version = TRUSTED_ACTIONS["actions/checkout"]
    assert errors == [f".github/workflows/ci.yml:4: unapproved SHA for actions/checkout: {bad_sha}; expected {trusted_sha} ({trusted_version})"]
