#!/usr/bin/env python3
"""Run the selected local gate without PostgreSQL, containers, or providers."""

from __future__ import annotations

import hashlib
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
from scripts.ci.select_test_scope import classify, local_changes  # noqa: E402


HARD_TIMEOUT_SECONDS = 300
IGNORED_PARTS = {".git", ".venv", ".pytest_cache", ".ruff_cache", "__pycache__", "node_modules", "test-results"}


def _tracked_worktree_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    files: list[Path] = []
    for value in completed.stdout.splitlines():
        relative = Path(value)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        path = ROOT / relative
        if path.is_file():
            files.append(path)
    return sorted(set(files))


def _fingerprint() -> str:
    digest = hashlib.sha256()
    for path in _tracked_worktree_files():
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _run(command: list[str], *, started_at: float, environment: dict[str, str]) -> None:
    remaining = max(1, int(HARD_TIMEOUT_SECONDS - (time.monotonic() - started_at)))
    if remaining <= 1:
        raise TimeoutError("local preflight exceeded its five-minute hard limit")
    completed = subprocess.run(command, cwd=ROOT, env=environment, timeout=remaining, check=False)
    if completed.returncode:
        raise subprocess.CalledProcessError(completed.returncode, command)


def _syntax_check(paths: Iterable[str]) -> None:
    for relative in paths:
        path = ROOT / relative
        if path.is_file() and path.suffix == ".py":
            compile(path.read_text(encoding="utf-8"), str(path), "exec")


def _frontend_files(targets: Iterable[str]) -> list[str]:
    result: list[str] = []
    for target in targets:
        path = ROOT / target
        if path.is_dir():
            result.extend(item.relative_to(ROOT).as_posix() for item in sorted(path.glob("*.test.mjs")))
        elif path.is_file():
            result.append(path.relative_to(ROOT).as_posix())
    return list(dict.fromkeys(result))


def main() -> int:
    started_at = time.monotonic()
    before = _fingerprint()
    try:
        changed, deleted = local_changes("origin/main")
        selection = classify(changed, deleted_files=deleted, local=True)
        print(json.dumps(selection.to_dict(), ensure_ascii=False, sort_keys=True))
        if selection.requires_postgres or any(
            target == layer or target.startswith(layer + "/")
            for target in selection.python_targets
            for layer in ("tests/postgres", "tests/high_risk", "tests/release")
        ):
            raise RuntimeError("local preflight selected a cloud-only test layer")
        _syntax_check(changed)
        environment = dict(os.environ)
        environment.pop("DATABASE_URL", None)
        environment.pop("AICRM_TEST_DATABASE_URL", None)
        environment.update(
            {
                "AICRM_NEXT_ENV": "test",
                "AICRM_NEXT_DATA_SOURCE": "fixture",
                "AICRM_WECOM_EXECUTION_MODE": "disabled",
                "SECRET_KEY": "current-local-preflight-secret",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        _run(["git", "diff", "--check"], started_at=started_at, environment=environment)
        if selection.python_targets:
            _run(
                [sys.executable, "-m", "pytest", *selection.python_targets, "-q", "--tb=short", "--timeout=120"],
                started_at=started_at,
                environment=environment,
            )
        frontend = _frontend_files(selection.frontend_targets)
        if frontend:
            _run(["node", "--test", *frontend], started_at=started_at, environment=environment)
    finally:
        if before != _fingerprint():
            raise RuntimeError("preflight changed repository source files; changes were not accepted")
    elapsed = time.monotonic() - started_at
    print(json.dumps({"ok": True, "elapsed_seconds": round(elapsed, 2), "tier": selection.tier}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, TimeoutError, subprocess.CalledProcessError) as exc:
        print(redact_sensitive_text(f"preflight failed: {exc}"), file=sys.stderr)
        raise SystemExit(1) from exc
