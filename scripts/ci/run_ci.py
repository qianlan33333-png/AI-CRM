#!/usr/bin/env python3
"""Execute one selected CI tier on a single GitHub runner."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.platform.shared.sensitive_data import redact_sensitive_text  # noqa: E402
from scripts.ci.select_test_scope import (  # noqa: E402
    ALL_FRONTEND_TARGETS,
    ALL_PYTHON_TARGETS,
    Selection,
    dependency_audit_required,
    event_changes,
)


RESULTS = ROOT / "test-results" / "ci"
TIER_TIMEOUTS = {"fast": 420, "release": 180, "high_risk": 1500, "full": 1500}


def _selection_from_payload(payload: dict[str, object]) -> Selection:
    expected = {"tier", "python_targets", "frontend_targets", "requires_postgres", "reason"}
    if set(payload) != expected:
        raise ValueError("selection payload must contain exactly the five public fields")
    tier = str(payload["tier"])
    if tier not in TIER_TIMEOUTS:
        raise ValueError(f"unsupported CI tier: {tier}")
    return Selection(
        tier=tier,  # type: ignore[arg-type]
        python_targets=tuple(str(value) for value in payload["python_targets"]),  # type: ignore[union-attr]
        frontend_targets=tuple(str(value) for value in payload["frontend_targets"]),  # type: ignore[union-attr]
        requires_postgres=bool(payload["requires_postgres"]),
        reason=str(payload["reason"]),
    )


def _forced_selection(tier: str) -> Selection:
    if tier == "release":
        return Selection("release", ("tests/release",), (), True, "forced_release")
    if tier not in {"high_risk", "full"}:
        raise ValueError("only release, high_risk, or full may be forced")
    return Selection(
        tier=tier,  # type: ignore[arg-type]
        python_targets=ALL_PYTHON_TARGETS,
        frontend_targets=ALL_FRONTEND_TARGETS,
        requires_postgres=True,
        reason=f"forced_{tier}",
    )


def _environment(*, postgres: bool) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "AICRM_NEXT_ENV": "test",
            "AICRM_NEXT_DATA_SOURCE": "fixture",
            "AICRM_WECOM_EXECUTION_MODE": "disabled",
            "AICRM_ROUTE_POLICY_ENFORCED": "false",
            "AICRM_ADMIN_AUTH_ENFORCED": "false",
            "SECRET_KEY": "current-ci-test-secret",
            "WECHAT_SHOP_CALLBACK_TOKEN": "current-ci-callback-token",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    if postgres:
        database_url = str(environment.get("AICRM_TEST_DATABASE_URL") or environment.get("DATABASE_URL") or "").strip()
        if not database_url:
            raise RuntimeError("PostgreSQL CI layer requires AICRM_TEST_DATABASE_URL or DATABASE_URL")
        environment["DATABASE_URL"] = database_url
        environment["AICRM_TEST_DATABASE_URL"] = database_url
    else:
        environment.pop("DATABASE_URL", None)
        environment.pop("AICRM_TEST_DATABASE_URL", None)
    return environment


def _run(command: list[str], *, name: str, deadline: float, environment: dict[str, str]) -> None:
    remaining = max(1, int(deadline - time.monotonic()))
    if remaining <= 1:
        raise TimeoutError("CI tier exceeded its internal hard timeout")
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=remaining,
        check=False,
    )
    if completed.returncode:
        RESULTS.mkdir(parents=True, exist_ok=True)
        log = RESULTS / f"{name}.log"
        log.write_text(
            "command: " + " ".join(command) + "\n\n" + completed.stdout + "\n" + completed.stderr,
            encoding="utf-8",
        )
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        raise subprocess.CalledProcessError(completed.returncode, command)
    if completed.stdout:
        print(completed.stdout, end="")


def _partition_python_targets(targets: Iterable[str]) -> list[tuple[str, list[str], bool]]:
    buckets: dict[str, list[str]] = {"current": [], "postgres": [], "high-risk": [], "release": []}
    for target in dict.fromkeys(targets):
        if target.startswith("tests/postgres"):
            buckets["postgres"].append(target)
        elif target.startswith("tests/high_risk"):
            buckets["high-risk"].append(target)
        elif target.startswith("tests/release"):
            buckets["release"].append(target)
        else:
            buckets["current"].append(target)
    return [
        (name, values, name in {"postgres", "release"})
        for name, values in buckets.items()
        if values
    ]


def _frontend_files(targets: Iterable[str]) -> list[str]:
    files: list[str] = []
    for target in targets:
        path = ROOT / target
        if path.is_dir():
            files.extend(item.relative_to(ROOT).as_posix() for item in sorted(path.glob("*.test.mjs")))
        elif path.is_file():
            files.append(path.relative_to(ROOT).as_posix())
    return list(dict.fromkeys(files))


def _run_dependency_audit(*, deadline: float) -> None:
    environment = _environment(postgres=False)
    _run([sys.executable, "scripts/ci/check_dependency_security.py"], name="dependency-policy", deadline=deadline, environment=environment)
    _run(
        [sys.executable, "-m", "pip_audit", "-r", "requirements.lock", "--require-hashes", "--progress-spinner=off"],
        name="python-dependency-audit",
        deadline=deadline,
        environment=environment,
    )
    _run(["npm", "audit", "--audit-level=high"], name="node-dependency-audit", deadline=deadline, environment=environment)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection-file", type=Path)
    parser.add_argument("--tier", choices=("release", "high_risk", "full"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if bool(args.selection_file) == bool(args.tier):
        raise ValueError("provide exactly one of --selection-file or --tier")
    if args.selection_file:
        selection = _selection_from_payload(json.loads(args.selection_file.read_text(encoding="utf-8")))
    else:
        selection = _forced_selection(args.tier)
    started_at = time.monotonic()
    deadline = started_at + TIER_TIMEOUTS[selection.tier]
    print(json.dumps(selection.to_dict(), ensure_ascii=False, sort_keys=True))

    _run(
        [sys.executable, "scripts/ci/check_release_gate_manifest.py"],
        name="release-gate-manifest",
        deadline=deadline,
        environment=_environment(postgres=False),
    )

    if selection.tier in {"high_risk", "full"}:
        _run(
            ["bash", "scripts/ci/run_architecture_gates.sh", "--mode", "full"],
            name="architecture-full",
            deadline=deadline,
            environment=_environment(postgres=True),
        )
    for name, targets, needs_postgres in _partition_python_targets(selection.python_targets):
        _run(
            [sys.executable, "-m", "pytest", *targets, "-q", "--tb=short", "--timeout=120", "--timeout-method=thread"],
            name=f"pytest-{name}",
            deadline=deadline,
            environment=_environment(postgres=needs_postgres),
        )
    frontend = _frontend_files(selection.frontend_targets)
    if frontend:
        _run(["node", "--test", *frontend], name="frontend", deadline=deadline, environment=_environment(postgres=False))

    changed: list[str] = []
    if os.environ.get("GITHUB_ACTIONS") == "true":
        changed, _deleted, _main_push = event_changes()
    if changed and dependency_audit_required(changed):
        _run_dependency_audit(deadline=deadline)
    print(
        json.dumps(
            {
                "ok": True,
                "tier": selection.tier,
                "reason": selection.reason,
                "elapsed_seconds": round(time.monotonic() - started_at, 2),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, TimeoutError, ValueError, subprocess.CalledProcessError) as exc:
        print(redact_sensitive_text(f"CI runner failed: {exc}"), file=sys.stderr)
        raise SystemExit(1) from exc
