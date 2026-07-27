from __future__ import annotations

from typing import Protocol

from aicrm_next.shared.typing import JsonDict


class McpToolPort(Protocol):
    def dispatch(self, name: str, arguments: JsonDict) -> JsonDict:
        ...
