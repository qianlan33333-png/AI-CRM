#!/usr/bin/env python3
"""Fail-closed selector for the current AI-CRM test system.

The selector has five and only five public outputs. It uses the current behavior
inventory for normal changes and escalates deleted or unknown runtime paths to a
full high-risk pull-request gate.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal


ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = ROOT / "docs" / "ci" / "current_behavior_inventory.json"

Tier = Literal["fast", "high_risk", "release", "full"]

PUBLIC_OUTPUT_FIELDS = (
    "tier",
    "python_targets",
    "frontend_targets",
    "requires_postgres",
    "reason",
)

CORE_FAST_TESTS = (
    "tests/contracts/test_behavior_inventory.py",
    "tests/contracts/test_test_system_budget.py",
    "tests/contracts/test_ci_selector.py",
    "tests/contracts/test_architecture_checkers.py",
)

ALL_PYTHON_TARGETS = (
    "tests/unit",
    "tests/contracts",
    "tests/postgres",
    "tests/high_risk",
    "tests/release",
)

ALL_FRONTEND_TARGETS = ("tests/frontend",)

HIGH_RISK_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "migration_or_schema",
        (
            "migrations/**",
            "alembic.ini",
            "scripts/ops/bootstrap_database.py",
            "docs/architecture/data_table_lifecycle_manifest.yml",
            "docs/architecture/db_access_boundary.yml",
        ),
    ),
    (
        "authentication_or_identity",
        (
            "aicrm_next/platform/admin_auth/**",
            "aicrm_next/platform/platform_foundation/auth_platform/**",
            "aicrm_next/channels/auth_wecom/**",
            "aicrm_next/crm/identity_contact/**",
        ),
    ),
    (
        "payment_refund_or_entitlement",
        (
            "aicrm_next/extensions/commerce/**/*payment*",
            "aicrm_next/extensions/commerce/**/*pay*",
            "aicrm_next/extensions/commerce/**/*refund*",
            "aicrm_next/extensions/commerce/**/*order*",
            "aicrm_next/extensions/commerce/**/service_period/**",
            "aicrm_next/channels/integration_gateway/payment_*",
            "aicrm_next/channels/integration_gateway/wechat_pay_client.py",
        ),
    ),
    (
        "callback_or_external_effect",
        (
            "aicrm_next/channels/channel_entry/**",
            "aicrm_next/channels/integration_gateway/**",
            "aicrm_next/platform/platform_foundation/external_effects/**",
            "aicrm_next/platform/platform_foundation/webhook_inbox/**",
            "aicrm_next/platform/external_push/**",
            "aicrm_next/external_effect_composition.py",
        ),
    ),
    (
        "approved_high_risk_business_flow",
        (
            "aicrm_next/extensions/forms/questionnaire/**",
            "aicrm_next/automation/automation_engine/group_ops/**",
        ),
    ),
    (
        "production_or_deploy",
        (
            "deploy/**",
            "scripts/prod.sh",
            "scripts/ops/*deploy*",
            "scripts/ops/*production*",
            ".github/workflows/deploy.yml",
            ".github/workflows/promote-production.yml",
        ),
    ),
    (
        "ci_or_dependency_control_plane",
        (
            ".github/**",
            "scripts/ci/**",
            "docs/ci/**",
            "Makefile",
            "pyproject.toml",
            "requirements*.txt",
            "requirements.lock",
            "package.json",
            "package-lock.json",
            "tests/conftest.py",
        ),
    ),
)

DEPENDENCY_AUDIT_PATTERNS = (
    "requirements*.txt",
    "requirements.lock",
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    ".github/**",
    "scripts/ci/check_dependency_security.py",
    "scripts/ci/check_github_action_pins.py",
    "docs/security/dependency_risk_acceptance.yml",
)


@dataclass(frozen=True)
class Selection:
    tier: Tier
    python_targets: tuple[str, ...]
    frontend_targets: tuple[str, ...]
    requires_postgres: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "tier": self.tier,
            "python_targets": list(self.python_targets),
            "frontend_targets": list(self.frontend_targets),
            "requires_postgres": self.requires_postgres,
            "reason": self.reason,
        }


def load_inventory(path: Path = INVENTORY_PATH) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("truth_source") != "current_ai_crm_next":
        raise ValueError(f"invalid current behavior inventory: {path}")
    if not isinstance(payload.get("behaviors"), list):
        raise ValueError(f"behavior inventory has no behaviors: {path}")
    return payload


def normalize_path(value: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def matches(path: str, pattern: str) -> bool:
    path = normalize_path(path)
    pattern = normalize_path(pattern)
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        if any(marker in prefix for marker in ("*", "?", "[")):
            return fnmatch.fnmatchcase(path, pattern)
        return path == prefix or path.startswith(prefix + "/")
    return fnmatch.fnmatchcase(path, pattern)


def unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _high_risk_reasons(paths: Iterable[str]) -> list[str]:
    reasons: list[str] = []
    for name, patterns in HIGH_RISK_RULES:
        if any(matches(path, pattern) for path in paths for pattern in patterns):
            reasons.append(name)
    return list(dict.fromkeys(reasons))


def _is_known_direct_test(path: str) -> bool:
    return any(
        path.startswith(prefix)
        for prefix in (
            "tests/unit/",
            "tests/contracts/",
            "tests/postgres/",
            "tests/high_risk/",
            "tests/release/",
            "tests/frontend/",
        )
    )


def _is_test_layer_target(path: str, layer: str) -> bool:
    return path == layer or path.startswith(layer + "/")


def _is_runtime_candidate(path: str) -> bool:
    if path.startswith("docs/") and path not in {
        "docs/architecture/route_ownership_manifest.yml",
        "docs/architecture/data_table_lifecycle_manifest.yml",
    }:
        return False
    if _is_known_direct_test(path):
        return False
    if path.startswith(("aicrm_next/", "scripts/", "tools/", "deploy/", "migrations/", ".github/")):
        return True
    return path in {
        "app.py",
        "alembic.ini",
        "Makefile",
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements.lock",
        "package.json",
        "package-lock.json",
    }


def _matched_tests(inventory: dict[str, object], changed_files: Iterable[str]) -> tuple[list[str], set[str]]:
    selected: list[str] = []
    matched_paths: set[str] = set()
    for behavior in inventory["behaviors"]:  # type: ignore[index]
        if not isinstance(behavior, dict):
            continue
        patterns = [str(value) for value in behavior.get("source_paths", [])]
        matching = {path for path in changed_files if any(matches(path, pattern) for pattern in patterns)}
        if not matching:
            continue
        matched_paths.update(matching)
        for target in behavior.get("tests", []):
            if isinstance(target, dict) and str(target.get("path") or ""):
                selected.append(str(target["path"]))
    return selected, matched_paths


def classify(
    changed_files: Iterable[str],
    *,
    deleted_files: Iterable[str] = (),
    event_name: str = "pull_request",
    main_push: bool = False,
    force_full: bool = False,
    local: bool = False,
    inventory: dict[str, object] | None = None,
) -> Selection:
    changed = unique(normalize_path(path) for path in changed_files)
    deleted = unique(normalize_path(path) for path in deleted_files)
    inventory = inventory or load_inventory()

    if force_full or event_name in {"workflow_call", "workflow_dispatch"}:
        return _full_selection("manual_or_reusable_full_regression", local=local)
    if main_push or (event_name == "push" and os.environ.get("GITHUB_REF") == "refs/heads/main"):
        return Selection(
            tier="release",
            python_targets=tuple() if local else ("tests/release",),
            frontend_targets=(),
            requires_postgres=not local,
            reason="main_push_exact_sha_release_gate",
        )

    direct_tests: list[str] = []
    deleted_set = set(deleted)
    for path in changed:
        if path in deleted_set or not _is_known_direct_test(path) or not (ROOT / path).exists():
            continue
        if path.endswith("conftest.py"):
            direct_tests.append(str(Path(path).parent).replace("\\", "/"))
        elif Path(path).name.startswith("test_") and path.endswith(".py"):
            direct_tests.append(path)
        elif path.endswith(".test.mjs"):
            direct_tests.append(path)
    direct_high_risk = any(
        path in {"tests/high_risk", "tests/release"}
        or path.startswith(("tests/high_risk/", "tests/release/"))
        for path in direct_tests
    )
    risk_reasons = _high_risk_reasons(changed)
    selected_tests, matched_paths = _matched_tests(inventory, changed)
    unknown_runtime = sorted(path for path in changed if _is_runtime_candidate(path) and path not in matched_paths)

    if deleted or direct_high_risk or risk_reasons or unknown_runtime:
        reasons: list[str] = []
        if deleted:
            reasons.append("deleted_file")
        if direct_high_risk:
            reasons.append("high_risk_test_change")
        reasons.extend(risk_reasons)
        if unknown_runtime:
            reasons.append("unknown_runtime_path")
        if local:
            return _local_selection(
                selected_tests,
                direct_tests,
                reason="cloud_escalation:" + ",".join(dict.fromkeys(reasons)),
            )
        return Selection(
            tier="high_risk",
            python_targets=ALL_PYTHON_TARGETS,
            frontend_targets=ALL_FRONTEND_TARGETS,
            requires_postgres=True,
            reason=",".join(dict.fromkeys(reasons)),
        )

    selected_tests.extend(direct_tests)
    selected_tests.extend(CORE_FAST_TESTS)
    python_targets = unique(
        path
        for path in selected_tests
        if (path.endswith(".py") or path in {"tests/unit", "tests/contracts", "tests/postgres"})
        and not path.startswith(("tests/high_risk/", "tests/release/"))
    )
    frontend_targets = unique(path for path in selected_tests if path.endswith(".mjs"))
    if any(path.startswith("tests/frontend/") for path in direct_tests) and not frontend_targets:
        frontend_targets = ALL_FRONTEND_TARGETS
    requires_postgres = any(_is_test_layer_target(path, "tests/postgres") for path in python_targets)

    if local:
        python_targets = tuple(
            path for path in python_targets if not _is_test_layer_target(path, "tests/postgres")
        )
        requires_postgres = False

    reason = "current_behavior_match" if matched_paths else "documentation_or_no_runtime_change"
    return Selection(
        tier="fast",
        python_targets=python_targets,
        frontend_targets=frontend_targets,
        requires_postgres=requires_postgres,
        reason=reason,
    )


def _full_selection(reason: str, *, local: bool) -> Selection:
    if local:
        return _local_selection([], [], reason=f"cloud_escalation:{reason}")
    return Selection(
        tier="full",
        python_targets=ALL_PYTHON_TARGETS,
        frontend_targets=ALL_FRONTEND_TARGETS,
        requires_postgres=True,
        reason=reason,
    )


def _local_selection(selected_tests: Iterable[str], direct_tests: Iterable[str], *, reason: str) -> Selection:
    candidates = [*selected_tests, *direct_tests, *CORE_FAST_TESTS]
    python_targets = unique(
        path
        for path in candidates
        if (path.endswith(".py") and path.startswith(("tests/unit/", "tests/contracts/")))
        or path in {"tests/unit", "tests/contracts"}
    )
    frontend_targets = unique(path for path in candidates if path.endswith(".mjs"))
    return Selection(
        tier="high_risk",
        python_targets=python_targets,
        frontend_targets=frontend_targets,
        requires_postgres=False,
        reason=reason,
    )


def dependency_audit_required(changed_files: Iterable[str]) -> bool:
    return any(matches(normalize_path(path), pattern) for path in changed_files for pattern in DEPENDENCY_AUDIT_PATTERNS)


def _parse_name_status(output: str) -> tuple[list[str], list[str]]:
    changed: list[str] = []
    deleted: list[str] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        status, separator, path = line.partition("\t")
        if not separator:
            raise ValueError(f"cannot parse git name-status line: {line!r}")
        normalized = normalize_path(path)
        changed.append(normalized)
        if status.startswith("D"):
            deleted.append(normalized)
    return changed, deleted


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def local_changes(base_ref: str) -> tuple[list[str], list[str]]:
    outputs = [
        _git("diff", "--name-status", "--no-renames", f"{base_ref}...HEAD"),
        _git("diff", "--name-status", "--no-renames", "HEAD"),
    ]
    changed: list[str] = []
    deleted: list[str] = []
    for output in outputs:
        current_changed, current_deleted = _parse_name_status(output)
        changed.extend(current_changed)
        deleted.extend(current_deleted)
    untracked = [normalize_path(path) for path in _git("ls-files", "--others", "--exclude-standard").splitlines() if path.strip()]
    changed.extend(untracked)
    return list(unique(changed)), list(unique(deleted))


def event_changes() -> tuple[list[str], list[str], bool]:
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if not event_path:
        changed, deleted = _parse_name_status(_git("diff", "--name-status", "--no-renames", "HEAD^", "HEAD"))
        return changed, deleted, event_name == "push" and os.environ.get("GITHUB_REF") == "refs/heads/main"
    payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    if event_name == "pull_request":
        base = str(payload["pull_request"]["base"]["sha"])
        head = str(payload["pull_request"]["head"]["sha"])
        changed, deleted = _parse_name_status(_git("diff", "--name-status", "--no-renames", f"{base}...{head}"))
        return changed, deleted, False
    if event_name == "push":
        before = str(payload.get("before") or "")
        after = str(payload.get("after") or "HEAD")
        base = before if before and set(before) != {"0"} else f"{after}^"
        changed, deleted = _parse_name_status(_git("diff", "--name-status", "--no-renames", base, after))
        return changed, deleted, os.environ.get("GITHUB_REF") == "refs/heads/main"
    return [], [], False


def _write_github_output(path: Path, selection: Selection) -> None:
    payload = selection.to_dict()
    with path.open("a", encoding="utf-8") as handle:
        for field in PUBLIC_OUTPUT_FIELDS:
            value = payload[field]
            rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":")) if isinstance(value, (list, bool)) else str(value)
            handle.write(f"{field}={rendered}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--deleted-file", action="append", default=[])
    parser.add_argument("--base-ref", default="origin/main")
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--force-tier", choices=("full",), default="")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    event_name = os.environ.get("GITHUB_EVENT_NAME", "pull_request")
    if args.changed_file or args.deleted_file:
        changed = list(args.changed_file) + list(args.deleted_file)
        deleted = list(args.deleted_file)
        main_push = False
    elif args.local:
        changed, deleted = local_changes(args.base_ref)
        main_push = False
    else:
        changed, deleted, main_push = event_changes()
    selection = classify(
        changed,
        deleted_files=deleted,
        event_name=event_name,
        main_push=main_push,
        force_full=args.force_tier == "full",
        local=args.local,
    )
    payload = selection.to_dict()
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered + "\n", encoding="utf-8")
    if args.github_output:
        _write_github_output(args.github_output, selection)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
