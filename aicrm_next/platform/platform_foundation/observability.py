from __future__ import annotations

from aicrm_next.platform.shared.runtime import runtime_health_state


def health_payload() -> dict:
    return runtime_health_state()
