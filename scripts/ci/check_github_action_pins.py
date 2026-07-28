#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
USES_PATTERN = re.compile(r"^\s*(?:-\s*)?uses:\s*([^#\s]+)")
COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")

TRUSTED_ACTIONS: dict[str, tuple[str, str]] = {
    "actions/checkout": ("9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0", "v7.0.0"),
    "actions/cache": ("55cc8345863c7cc4c66a329aec7e433d2d1c52a9", "v6.1.0"),
    "actions/setup-python": ("ece7cb06caefa5fff74198d8649806c4678c61a1", "v6.3.0"),
    "actions/setup-node": ("48b55a011bda9f5d6aeb4c2d9c7362e8dae4041e", "v6.4.0"),
    "actions/upload-artifact": ("043fb46d1a93c77aae656e7c1c64a875d1fc6a0a", "v7.0.1"),
    "actions/download-artifact": ("3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c", "v8.0.1"),
    "appleboy/scp-action": ("ff85246acaad7bdce478db94a363cd2bf7c90345", "v1.0.0"),
    "appleboy/ssh-action": ("0ff4204d59e8e51228ff73bce53f80d53301dee2", "v1.2.5"),
}


def check_workflows(root: Path = ROOT) -> tuple[list[str], int, int]:
    errors: list[str] = []
    external_use_count = 0
    workflow_files = sorted((root / ".github" / "workflows").glob("*.y*ml"))
    for workflow_file in workflow_files:
        for line_number, line in enumerate(workflow_file.read_text(encoding="utf-8").splitlines(), start=1):
            match = USES_PATTERN.match(line)
            if not match:
                continue
            action_ref = match.group(1)
            if action_ref.startswith("./") or action_ref.startswith("docker://"):
                continue
            external_use_count += 1
            if "@" not in action_ref:
                errors.append(f"{workflow_file.relative_to(root)}:{line_number}: external action has no immutable ref: {action_ref}")
                continue
            action_path, ref = action_ref.rsplit("@", 1)
            action_parts = action_path.split("/")
            action_name = "/".join(action_parts[:2]) if len(action_parts) >= 2 else action_path
            if not COMMIT_SHA_PATTERN.fullmatch(ref):
                errors.append(f"{workflow_file.relative_to(root)}:{line_number}: mutable action ref is forbidden: {action_ref}")
                continue
            trusted = TRUSTED_ACTIONS.get(action_name)
            if trusted is None:
                errors.append(f"{workflow_file.relative_to(root)}:{line_number}: action is not in the trusted registry: {action_name}@{ref}")
                continue
            trusted_sha, trusted_version = trusted
            if ref != trusted_sha:
                errors.append(
                    f"{workflow_file.relative_to(root)}:{line_number}: unapproved SHA for {action_name}: {ref}; expected {trusted_sha} ({trusted_version})"
                )
    return errors, len(workflow_files), external_use_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Reject mutable or unapproved external GitHub Action references.")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    errors, workflow_count, external_use_count = check_workflows(args.root.resolve())
    if errors:
        for violation in errors:
            print(violation)
        return 1
    del workflow_count, external_use_count
    print("GitHub Action pin check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
