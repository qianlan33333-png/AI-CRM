from __future__ import annotations

from .observability import health_payload


class GetSystemHealthQuery:
    def execute(self) -> dict:
        return health_payload()

    __call__ = execute
