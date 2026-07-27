from __future__ import annotations

from typing import Any


def classify_refund_business_rejection(provider_payload: dict[str, Any]) -> dict[str, Any]:
    """Describe a completed provider decision without claiming the refund succeeded."""

    provider_code = str(provider_payload.get("code") or "").strip().upper()
    if provider_code != "NOT_ENOUGH":
        return {}
    return {
        "process_outcome": "completed",
        "business_outcome": "rejected",
        "business_reason_code": "insufficient_refund_balance",
        "system_health_impact": False,
        "replay_prohibited": True,
        "provider_success_claimed": False,
    }
