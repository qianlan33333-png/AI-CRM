from __future__ import annotations

from typing import Any


def retired_external_effect_payload(legacy_key: str, *, error: str) -> dict[str, Any]:
    """Return the stable response for an entrypoint whose old executor is gone."""

    return {
        "ok": False,
        "error": str(error or "legacy_runtime_removed").strip() or "legacy_runtime_removed",
        "legacy_key": str(legacy_key or "").strip(),
        "legacy_outbound_disabled": True,
        "external_effect_required": True,
        "migration_target": "external_effect_queue",
        "push_center_url": "/api/admin/push-center/jobs",
        "retirement_state": "physically_removed",
        "real_external_call_executed": False,
    }


__all__ = ["retired_external_effect_payload"]
