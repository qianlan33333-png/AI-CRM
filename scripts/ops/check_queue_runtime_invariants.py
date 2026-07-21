#!/usr/bin/env python3
from __future__ import annotations

import json
import sys

try:
    from scripts.script_runtime import ensure_repo_root_on_path
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from script_runtime import ensure_repo_root_on_path

ensure_repo_root_on_path()

from aicrm_next.platform_foundation.execution_runtime.invariants import (
    QueueRuntimeInvariantChecker,
)


def main() -> int:
    report = QueueRuntimeInvariantChecker().check().to_dict()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
