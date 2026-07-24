#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.deployment_profile import load_deployment_profile


DEFAULT_PROFILE_DIR = ROOT / "deploy" / "deployment_profiles"


def validate_deployment_profiles(profile_dir: Path = DEFAULT_PROFILE_DIR) -> list[str]:
    errors: list[str] = []
    paths = sorted(profile_dir.glob("*.json"))
    if not paths:
        return [f"no deployment profiles found in {profile_dir}"]
    ids: set[str] = set()
    for path in paths:
        try:
            profile = load_deployment_profile(path)
        except (OSError, ValueError) as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        if profile.profile_id in ids:
            errors.append(f"duplicate deployment profile id: {profile.profile_id}")
        ids.add(profile.profile_id)
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate versioned AI-CRM deployment profiles.")
    parser.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR)
    args = parser.parse_args(argv)
    errors = validate_deployment_profiles(args.profile_dir)
    if errors:
        print("Deployment profile check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Deployment profiles OK: {args.profile_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
