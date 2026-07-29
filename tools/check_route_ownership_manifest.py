from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aicrm_next.main import app
from aicrm_next.platform.shared.route_ownership import validate_route_manifest


DEFAULT_MANIFEST = ROOT / "docs" / "architecture" / "route_ownership_manifest.yml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate AI-CRM Next route ownership manifest.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--include-static", action="store_true", help="Also require manifest entries for static mounts.")
    args = parser.parse_args(argv)

    errors = validate_route_manifest(app, args.manifest, include_static=bool(args.include_static))
    if errors:
        print("Route ownership manifest check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Route ownership manifest OK: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
