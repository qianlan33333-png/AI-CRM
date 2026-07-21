#!/usr/bin/env python3
"""Validate one cross-repository ID-to-AI-CRM production promotion.

The current reviewed binding promotes AI-CRM PR #1741 only after ID validation
PR #215 is active on id-dev.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[2]
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
SOURCE_REPOSITORY = "qianlan333/AI-CRM-ID-refactor"
TARGET_REPOSITORY = "qianlan333/AI-CRM"
REQUIRED_PROMOTION_PATHS = {
    ".github/workflows/deploy.yml",
    ".github/workflows/promote-production.yml",
    "docs/releases/production_promotion.json",
    "scripts/ops/validate_production_promotion.py",
}


class PromotionValidationError(ValueError):
    """Raised when promotion evidence is incomplete or inconsistent."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PromotionValidationError(message)


def _full_sha(value: Any, label: str) -> str:
    _require(isinstance(value, str) and SHA_PATTERN.fullmatch(value) is not None, f"{label} must be a full Git SHA")
    return value


def _positive_int(value: Any, label: str) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool) and value > 0, f"{label} must be positive")
    return value


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _validate_run(
    payload: dict[str, Any],
    *,
    run_id: int,
    name: str,
    event: str,
    head_sha: str,
) -> None:
    _require(payload.get("id") == run_id, f"{name} run id does not match the promotion manifest")
    _require(payload.get("name") == name, f"source run {run_id} has the wrong workflow name")
    _require(payload.get("event") == event, f"source run {run_id} has the wrong event")
    _require(payload.get("head_sha") == head_sha, f"source run {run_id} has the wrong SHA")
    _require(payload.get("head_branch") == "main", f"source run {run_id} is not on main")
    _require(payload.get("status") == "completed", f"source run {run_id} is not completed")
    _require(payload.get("conclusion") == "success", f"source run {run_id} did not succeed")


def _validate_target_ci(payload: dict[str, Any], release_sha: str) -> int:
    runs = payload.get("workflow_runs")
    _require(isinstance(runs, list), "target CI evidence must contain workflow_runs")
    for run in runs:
        if not isinstance(run, dict):
            continue
        if (
            run.get("name") == "CI Fast"
            and run.get("event") == "push"
            and run.get("head_branch") == "main"
            and run.get("head_sha") == release_sha
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
        ):
            return _positive_int(run.get("id"), "target CI run id")
    raise PromotionValidationError("requested AI-CRM release has no successful main CI Fast run")


def validate_promotion(
    manifest: dict[str, Any],
    *,
    root: Path,
    release_sha: str,
    validated_id_sha: str,
    source_main_sha: str,
    public_id_sha: str,
    source_ci_run: dict[str, Any],
    source_deploy_run: dict[str, Any],
    target_ci_runs: dict[str, Any],
) -> dict[str, Any]:
    release_sha = _full_sha(release_sha, "release_sha")
    validated_id_sha = _full_sha(validated_id_sha, "validated_id_sha")
    source_main_sha = _full_sha(source_main_sha, "source_main_sha")
    public_id_sha = _full_sha(public_id_sha, "public_id_sha")
    _require(manifest.get("version") == 1, "promotion manifest version must be 1")

    source = manifest.get("source")
    target = manifest.get("target")
    allowed_paths = manifest.get("allowed_post_candidate_paths")
    _require(isinstance(source, dict), "promotion manifest source is required")
    _require(isinstance(target, dict), "promotion manifest target is required")
    _require(isinstance(allowed_paths, list) and allowed_paths, "allowed_post_candidate_paths is required")
    _require(all(isinstance(path, str) and path for path in allowed_paths), "allowed promotion paths must be strings")
    _require(len(allowed_paths) == len(set(allowed_paths)), "allowed promotion paths must be unique")

    _require(source.get("repository") == SOURCE_REPOSITORY, "promotion source repository is not authoritative")
    manifest_source_sha = _full_sha(source.get("release_sha"), "manifest source release_sha")
    _require(manifest_source_sha == validated_id_sha, "validated ID SHA does not match the promotion manifest")
    _require(source_main_sha == validated_id_sha, "validated ID SHA is not the current ID-refactor main")
    _require(public_id_sha == validated_id_sha, "id-dev does not expose the validated ID SHA")
    source_ci_run_id = _positive_int(source.get("ci_run_id"), "source ci_run_id")
    source_deploy_run_id = _positive_int(source.get("deploy_run_id"), "source deploy_run_id")
    _positive_int(source.get("pull_request"), "source pull_request")
    _validate_run(
        source_ci_run,
        run_id=source_ci_run_id,
        name="CI Fast",
        event="push",
        head_sha=validated_id_sha,
    )
    _validate_run(
        source_deploy_run,
        run_id=source_deploy_run_id,
        name="Deploy ID Validation",
        event="workflow_run",
        head_sha=validated_id_sha,
    )

    _require(target.get("repository") == TARGET_REPOSITORY, "promotion target repository is not AI-CRM")
    candidate_sha = _full_sha(target.get("candidate_sha"), "target candidate_sha")
    _positive_int(target.get("pull_request"), "target pull_request")
    expected_head = _git(root, "rev-parse", "HEAD").stdout.strip()
    _require(expected_head == release_sha, "checked out release does not match release_sha")
    candidate_exists = _git(root, "cat-file", "-e", f"{candidate_sha}^{{commit}}", check=False)
    _require(candidate_exists.returncode == 0, "target candidate commit is missing from the checkout")
    is_ancestor = _git(root, "merge-base", "--is-ancestor", candidate_sha, release_sha, check=False)
    _require(is_ancestor.returncode == 0, "target candidate is not an ancestor of the requested release")

    changed_paths = {
        line
        for line in _git(root, "diff", "--name-only", candidate_sha, release_sha).stdout.splitlines()
        if line
    }
    allowed_path_set = set(allowed_paths)
    unexpected_paths = sorted(changed_paths - allowed_path_set)
    _require(not unexpected_paths, f"unapproved post-candidate paths: {', '.join(unexpected_paths)}")
    missing_required_paths = sorted(REQUIRED_PROMOTION_PATHS - changed_paths)
    _require(not missing_required_paths, f"promotion control paths are missing: {', '.join(missing_required_paths)}")
    target_ci_run_id = _validate_target_ci(target_ci_runs, release_sha)

    return {
        "ok": True,
        "source_repository": SOURCE_REPOSITORY,
        "validated_id_sha": validated_id_sha,
        "source_ci_run_id": source_ci_run_id,
        "source_deploy_run_id": source_deploy_run_id,
        "target_repository": TARGET_REPOSITORY,
        "target_candidate_sha": candidate_sha,
        "release_sha": release_sha,
        "target_ci_run_id": target_ci_run_id,
        "changed_paths": sorted(changed_paths),
    }


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PromotionValidationError(f"{path} must contain a JSON object")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--validated-id-sha", required=True)
    parser.add_argument("--source-main-sha", required=True)
    parser.add_argument("--public-id-sha", required=True)
    parser.add_argument("--source-ci-run-json", type=Path, required=True)
    parser.add_argument("--source-deploy-run-json", type=Path, required=True)
    parser.add_argument("--target-ci-runs-json", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = validate_promotion(
            _load_json(args.manifest),
            root=args.root.resolve(),
            release_sha=args.release_sha,
            validated_id_sha=args.validated_id_sha,
            source_main_sha=args.source_main_sha,
            public_id_sha=args.public_id_sha,
            source_ci_run=_load_json(args.source_ci_run_json),
            source_deploy_run=_load_json(args.source_deploy_run_json),
            target_ci_runs=_load_json(args.target_ci_runs_json),
        )
    except (json.JSONDecodeError, OSError, PromotionValidationError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"error": type(exc).__name__, "ok": False}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
