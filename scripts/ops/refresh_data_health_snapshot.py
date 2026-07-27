#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from scripts.script_runtime import ensure_repo_root_on_path
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.script_runtime import ensure_repo_root_on_path

ensure_repo_root_on_path()

from aicrm_next.insights.data_health.snapshot_service import capture_data_health_snapshot  # noqa: E402


def run(*, expected_release_sha: str | None = None) -> int:
    try:
        snapshot = capture_data_health_snapshot()
        if expected_release_sha is not None and snapshot.source_release_sha != expected_release_sha.strip().lower():
            raise ValueError("data_health_snapshot_release_sha_mismatch")
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": type(exc).__name__,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "generation_id": snapshot.generation_id,
                "source_release_sha": snapshot.source_release_sha,
                "overall_status": snapshot.overall_status,
                "check_count": len(snapshot.checks),
                "duration_ms": snapshot.duration_ms,
                "captured_at": snapshot.captured_at.isoformat(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Refresh the aggregate AI-CRM data health snapshot")
    parser.add_argument("--expected-release-sha")
    args = parser.parse_args()
    raise SystemExit(run(expected_release_sha=args.expected_release_sha))
