from __future__ import annotations

from typing import Any


# Shared fixture storage for application-layer channel readers. Production code
# never consults this map because the channel repository is PostgreSQL-backed.
FIXTURE_CHANNELS: dict[int, dict[str, Any]] = {}
