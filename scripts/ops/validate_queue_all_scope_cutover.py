#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the production all-scope cutover authorization envelope.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--authorization-base-sha", required=True)
    parser.add_argument("--confirmation", required=True)
    parser.add_argument("--changed-paths", type=Path, required=True)
    return parser.parse_args()


def validate(
    *,
    manifest: dict[str, object],
    release_sha: str,
    authorization_base_sha: str,
    confirmation: str,
    changed_paths: list[str],
) -> dict[str, object]:
    if FULL_SHA.fullmatch(release_sha) is None:
        raise ValueError("release_sha must be one full lowercase SHA")
    if authorization_base_sha != str(manifest.get("authorization_base_sha") or ""):
        raise ValueError("authorization base SHA does not match the immutable manifest")
    if FULL_SHA.fullmatch(authorization_base_sha) is None:
        raise ValueError("authorization base SHA must be one full lowercase SHA")
    if confirmation != str(manifest.get("authorization_confirmation") or ""):
        raise ValueError("all-scope confirmation does not match the immutable manifest")
    if manifest.get("target_scope") != "all" or int(manifest.get("target_generation") or 0) != 1:
        raise ValueError("manifest must bind production all-scope generation 1")
    allowed = {str(item) for item in manifest.get("allowed_successor_paths") or []}
    normalized = [str(item or "").strip() for item in changed_paths if str(item or "").strip()]
    unexpected = sorted(set(normalized) - allowed)
    if unexpected:
        raise ValueError("successor release contains unauthorized paths: " + ",".join(unexpected))
    if not normalized:
        raise ValueError("successor release must contain the reviewed cutover controls")
    return {
        "ok": True,
        "authorization_base_sha": authorization_base_sha,
        "release_sha": release_sha,
        "target_generation": 1,
        "target_scope": "all",
        "changed_path_count": len(set(normalized)),
    }


def main() -> int:
    args = _parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    changed_paths = args.changed_paths.read_text(encoding="utf-8").splitlines()
    result = validate(
        manifest=manifest,
        release_sha=str(args.release_sha),
        authorization_base_sha=str(args.authorization_base_sha),
        confirmation=str(args.confirmation),
        changed_paths=changed_paths,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
