from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aicrm_next.extensions.hxc.operation_cycles.local_connector import (  # noqa: E402
    ConnectorConfig,
    OperationCycleCodexConnector,
)


def main() -> int:
    connector = OperationCycleCodexConnector(ConnectorConfig.from_mapping(dict(os.environ)))
    try:
        connector.run_forever()
    except KeyboardInterrupt:
        connector.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
