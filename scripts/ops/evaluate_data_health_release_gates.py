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

from aicrm_next.insights.data_health.application import data_health_summary  # noqa: E402
from aicrm_next.platform.release_governance.evaluator import (  # noqa: E402
    evaluate_data_health_release_gates,
    release_gate_set_payload,
)
from aicrm_next.platform.shared.sensitive_data import redact_sensitive_text  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate production-safe data health as release gates")
    parser.add_argument(
        "--phase",
        required=True,
        choices=("pre_merge_prod", "pre_mutation", "post_cutover"),
    )
    parser.add_argument("--candidate-sha", default="unknown")
    parser.add_argument("--production-sha", default="unknown")
    args = parser.parse_args(argv)
    results = evaluate_data_health_release_gates(
        data_health_summary(),
        phase=args.phase,
        candidate_sha=args.candidate_sha,
        production_sha=args.production_sha,
    )
    payload = release_gate_set_payload(results)
    print(redact_sensitive_text(json.dumps(payload, ensure_ascii=False, sort_keys=True)))
    return 2 if payload["decision"] == "block" else 0


if __name__ == "__main__":
    raise SystemExit(main())
