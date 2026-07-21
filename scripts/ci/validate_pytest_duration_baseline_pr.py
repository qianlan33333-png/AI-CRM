#!/usr/bin/env python3
"""Validate that a proposed pytest duration baseline came from trusted main CI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Sequence


EXPECTED_WORKFLOW_NAME = "Full Regression"
EXPECTED_WORKFLOW_PATH = ".github/workflows/full-regression.yml"
ALLOWED_SOURCE_EVENTS = {"schedule", "workflow_dispatch"}


def _load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to load JSON document: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"JSON document must contain a mapping: {path}")
    return payload


def _repository_name(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    value = payload.get("full_name")
    return value if isinstance(value, str) else ""


def validate_baseline_source_run(
    baseline: dict,
    source_run: dict,
    *,
    expected_repository: str,
    expected_base_sha: str,
) -> int:
    if not re.fullmatch(r"[0-9a-f]{40}", expected_base_sha):
        raise ValueError("expected base SHA must be a lowercase 40-character Git SHA")
    if baseline.get("version") != 1:
        raise ValueError("baseline must use version 1")
    source_run_id = baseline.get("source_run_id")
    if not isinstance(source_run_id, int) or isinstance(source_run_id, bool) or source_run_id <= 0:
        raise ValueError("baseline source_run_id must be a positive integer")
    if baseline.get("source_sha") != expected_base_sha:
        raise ValueError("baseline source SHA must equal the pull request base SHA")
    if source_run.get("id") != source_run_id:
        raise ValueError("source run id does not match the proposed baseline")
    if source_run.get("name") != EXPECTED_WORKFLOW_NAME or source_run.get("path") != EXPECTED_WORKFLOW_PATH:
        raise ValueError("source run must use the trusted Full Regression workflow path")
    if source_run.get("event") not in ALLOWED_SOURCE_EVENTS:
        raise ValueError("source run event must be schedule or workflow_dispatch")
    if source_run.get("status") != "completed" or source_run.get("conclusion") != "success":
        raise ValueError("source run must be completed and successful")
    if source_run.get("head_branch") != "main":
        raise ValueError("source run must test main")
    if source_run.get("head_sha") != expected_base_sha:
        raise ValueError("source run head SHA must equal the pull request base SHA")
    if _repository_name(source_run.get("repository")) != expected_repository:
        raise ValueError("source run repository is not trusted")
    if _repository_name(source_run.get("head_repository")) != expected_repository:
        raise ValueError("source run head repository is not trusted")
    return source_run_id


def validate_baseline_artifacts(payload: dict) -> None:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("source artifacts response must contain a list")
    expected = {f"full-python-shard-{index}-of-8" for index in range(1, 9)}
    retained = {
        str(artifact.get("name"))
        for artifact in artifacts
        if isinstance(artifact, dict)
        and artifact.get("expired") is False
        and str(artifact.get("name")) in expected
    }
    if retained != expected:
        raise ValueError("source run must retain all eight unexpired Python shard artifacts")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--source-artifacts", type=Path, required=True)
    parser.add_argument("--expected-repository", required=True)
    parser.add_argument("--expected-base-sha", required=True)
    parser.add_argument("--github-output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        source_run_id = validate_baseline_source_run(
            _load_json(args.baseline),
            _load_json(args.source_run),
            expected_repository=args.expected_repository,
            expected_base_sha=args.expected_base_sha,
        )
        validate_baseline_artifacts(_load_json(args.source_artifacts))
    except ValueError:
        print(json.dumps({"error": "pytest duration baseline PR validation failed", "ok": False}, sort_keys=True))
        return 2
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as handle:
            handle.write(f"source_run_id={source_run_id}\n")
    print(json.dumps({"ok": True, "source_run_id": source_run_id}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
