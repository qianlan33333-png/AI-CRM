#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aicrm_next.extensions.ai.ai_audience_ops.automation_binding.precheck import (  # noqa: E402
    inspect_runtime_automation_bindings,
)


def main() -> int:
    report = inspect_runtime_automation_bindings()
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
