#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.shared.sensitive_data import redact_sensitive_text  # noqa: E402


DEFAULT_MANIFEST = ROOT / "docs/architecture/id_validation_promotion_manifest.yml"

CLASS_PROMOTABLE = "promotable"
CLASS_ID_ONLY = "id_only"
CLASS_MIXED_REVIEW = "mixed_review_required"
CLASS_NOT_APPLICATION = "not_application"

_EXPECTED_SOURCE = "qianlan333/AI-CRM-ID-refactor"
_EXPECTED_TARGET = "qianlan333/AI-CRM"
_EXPECTED_UPSTREAM = "qianlan333/AI-CRM"
_EXPECTED_UPSTREAM_STRATEGY = "ancestry_merge_preserving_id_only_overlay"
_EXPECTED_COMMAND = "PROMOTE <validated_ID_SHA> TO AI-CRM"
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")

_REQUIRED_ID_ONLY = frozenset(
    {
        ".github/workflows/deploy.yml",
        ".github/workflows/id-validation-queue-operations.yml",
        "aicrm_next/platform_foundation/execution_runtime/validation.py",
        "aicrm_next/platform_foundation/external_effects/canary_repository.py",
        "aicrm_next/platform_foundation/external_effects/wecom_canary_policy.py",
        "deploy/aicrm-queue-soak-snapshot.service",
        "deploy/aicrm-queue-soak-snapshot.timer",
        "migrations/versions/0135_queue_scope_transition_audit.py",
        "scripts/ops/deploy_id_validation_remote.sh",
        "scripts/ops/run_id_validation_queue_operation.sh",
    }
)
_REQUIRED_MIXED_REVIEW = frozenset(
    {
        "aicrm_next/admin_config/application.py",
        "aicrm_next/platform_foundation/execution_runtime/cutover.py",
        "aicrm_next/platform_foundation/external_effects/adapters.py",
        "aicrm_next/platform_foundation/external_effects/service.py",
        "deploy/production_runtime_units.json",
        "migrations/versions/0136_queue_runtime_validation_soak.py",
    }
)
_SENSITIVE_PATH_MARKERS = (
    "id_validation",
    "id-validation",
    "wecom_canary",
    "queue_runtime_validation_soak",
    "queue-soak",
)


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("promotion manifest must be a mapping")
    return payload


def _normalized_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/")
    normalized = PurePosixPath(value).as_posix()
    if not value or value.startswith("/") or normalized == ".." or normalized.startswith("../"):
        raise ValueError(f"unsafe repository path: {path!r}")
    return normalized


def _section_patterns(manifest: dict[str, Any], section: str) -> tuple[list[str], list[str]]:
    value = manifest.get(section)
    if not isinstance(value, dict):
        return [], []
    paths = value.get("paths", [])
    globs = value.get("globs", [])
    if not isinstance(paths, list) or not isinstance(globs, list):
        return [], []
    return [str(item) for item in paths], [str(item) for item in globs]


def _matches(path: str, *, paths: list[str], globs: list[str]) -> bool:
    normalized = _normalized_path(path)
    if normalized in paths:
        return True
    return any(fnmatch.fnmatchcase(normalized, pattern) for pattern in globs)


def classify_path(path: str, manifest: dict[str, Any]) -> str:
    normalized = _normalized_path(path)
    id_paths, id_globs = _section_patterns(manifest, "id_only_overlay")
    mixed_paths, mixed_globs = _section_patterns(manifest, "mixed_review_required")
    app_paths, app_globs = _section_patterns(manifest, "promotable_application")
    if _matches(normalized, paths=id_paths, globs=id_globs):
        return CLASS_ID_ONLY
    if _matches(normalized, paths=mixed_paths, globs=mixed_globs):
        return CLASS_MIXED_REVIEW
    if _matches(normalized, paths=app_paths, globs=app_globs):
        return CLASS_PROMOTABLE
    return CLASS_NOT_APPLICATION


def _repository_paths(root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return sorted({line.strip() for line in completed.stdout.splitlines() if line.strip()})
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.parts
    )


def _validate_patterns(manifest: dict[str, Any], section: str) -> list[str]:
    errors: list[str] = []
    value = manifest.get(section)
    if not isinstance(value, dict):
        return [f"{section} must be a mapping"]
    for key in ("paths", "globs"):
        entries = value.get(key)
        if not isinstance(entries, list):
            errors.append(f"{section}.{key} must be a list")
            continue
        normalized_entries: list[str] = []
        for entry in entries:
            if not isinstance(entry, str) or not entry.strip():
                errors.append(f"{section}.{key} contains an empty or non-string value")
                continue
            try:
                normalized_entries.append(_normalized_path(entry))
            except ValueError as exc:
                errors.append(f"{section}.{key}: {exc}")
        if len(normalized_entries) != len(set(normalized_entries)):
            errors.append(f"{section}.{key} contains duplicate entries")
    return errors


def _is_ancestor(*, root: Path, ancestor_sha: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", ancestor_sha, "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def validate_manifest(*, root: Path, manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("version") != 1:
        errors.append("version must be 1")
    if manifest.get("source_repository") != _EXPECTED_SOURCE:
        errors.append(f"source_repository must be {_EXPECTED_SOURCE}")
    if manifest.get("target_repository") != _EXPECTED_TARGET:
        errors.append(f"target_repository must be {_EXPECTED_TARGET}")
    upstream_sync = manifest.get("upstream_sync")
    if not isinstance(upstream_sync, dict):
        errors.append("upstream_sync must be a mapping")
    else:
        if upstream_sync.get("repository") != _EXPECTED_UPSTREAM:
            errors.append(f"upstream_sync.repository must be {_EXPECTED_UPSTREAM}")
        validated_sha = str(upstream_sync.get("validated_sha") or "").strip()
        if _FULL_SHA.fullmatch(validated_sha) is None:
            errors.append("upstream_sync.validated_sha must be one lowercase full Git SHA")
        elif not _is_ancestor(root=root, ancestor_sha=validated_sha):
            errors.append("upstream_sync.validated_sha must be an ancestor of HEAD")
        if upstream_sync.get("strategy") != _EXPECTED_UPSTREAM_STRATEGY:
            errors.append(
                f"upstream_sync.strategy must be {_EXPECTED_UPSTREAM_STRATEGY}"
            )
    authorization = manifest.get("authorization")
    if not isinstance(authorization, dict):
        errors.append("authorization must be a mapping")
    else:
        if authorization.get("command") != _EXPECTED_COMMAND:
            errors.append(f"authorization.command must be {_EXPECTED_COMMAND}")
        if authorization.get("creates_github_pr_only") is not True:
            errors.append("authorization.creates_github_pr_only must be true")
        if authorization.get("authorizes_server_deploy") is not False:
            errors.append("authorization.authorizes_server_deploy must be false")

    for section in (
        "promotable_application",
        "id_only_overlay",
        "mixed_review_required",
    ):
        errors.extend(_validate_patterns(manifest, section))

    repository_paths = _repository_paths(root)
    id_paths, id_globs = _section_patterns(manifest, "id_only_overlay")
    mixed_paths, mixed_globs = _section_patterns(manifest, "mixed_review_required")
    for path in repository_paths:
        id_match = _matches(path, paths=id_paths, globs=id_globs)
        mixed_match = _matches(path, paths=mixed_paths, globs=mixed_globs)
        if id_match and mixed_match:
            errors.append(f"{path} matches both id_only and mixed_review_required")

    for path in sorted(_REQUIRED_ID_ONLY):
        if classify_path(path, manifest) != CLASS_ID_ONLY:
            errors.append(f"{path} must be id_only")
    for path in sorted(_REQUIRED_MIXED_REVIEW):
        if classify_path(path, manifest) != CLASS_MIXED_REVIEW:
            errors.append(f"{path} must be mixed_review_required")

    for path in repository_paths:
        lowered = path.lower()
        if any(marker in lowered for marker in _SENSITIVE_PATH_MARKERS):
            classification = classify_path(path, manifest)
            if classification not in {CLASS_ID_ONLY, CLASS_MIXED_REVIEW}:
                errors.append(
                    f"sensitive ID-validation path is not guarded: {path} ({classification})"
                )
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the fail-closed ID-refactor promotion path manifest."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--classify", action="append", default=[])
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    errors = validate_manifest(root=ROOT, manifest=manifest)
    if errors:
        for error in errors:
            print(redact_sensitive_text(f"ERROR: {error}"))
        return 1
    for path in args.classify:
        print(f"{_normalized_path(path)}\t{classify_path(path, manifest)}")
    print("ID-validation promotion manifest: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
