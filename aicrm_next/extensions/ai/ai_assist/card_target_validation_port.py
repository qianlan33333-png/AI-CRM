from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Protocol
from urllib.request import Request, urlopen

from aicrm_next.platform.shared.runtime_settings import runtime_setting


class CardTargetValidationPort(Protocol):
    def validate_targets(self, cards: Iterable[Mapping[str, Any]]) -> dict[str, str]: ...


class HxcBatchCardTargetValidationAdapter:
    """Read-only adapter to the product-owned target validation contract."""

    def validate_targets(self, cards: Iterable[Mapping[str, Any]]) -> dict[str, str]:
        source = [dict(card) for card in cards]
        endpoint = str(runtime_setting("AICRM_HXC_CARD_TARGET_VALIDATION_URL", "") or "").strip()
        if not endpoint:
            return {str(card.get("row_key") or ""): "card_target_validation_unavailable" for card in source}
        token = str(runtime_setting("AICRM_HXC_CARD_TARGET_VALIDATION_TOKEN", "") or "").strip()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(
            endpoint,
            data=json.dumps({"schema_version": "hxc_card_target_validation.v1", "cards": source}).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=20) as response:  # noqa: S310 - configured internal endpoint
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            return {str(card.get("row_key") or ""): "card_target_validation_unavailable" for card in source}
        results = payload.get("results") if isinstance(payload, dict) else []
        by_key = {
            str(item.get("row_key") or ""): item
            for item in results or []
            if isinstance(item, dict)
        }
        checked: dict[str, str] = {}
        for card in source:
            row_key = str(card.get("row_key") or "")
            item = by_key.get(row_key)
            if not item:
                checked[row_key] = "card_target_result_missing"
            elif not bool(item.get("ok")):
                checked[row_key] = str(item.get("reason_code") or "card_target_invalid")
            elif item.get("owner_matches") is False:
                checked[row_key] = "card_target_owner_mismatch"
            else:
                checked[row_key] = ""
        return checked


def build_card_target_validation_port() -> CardTargetValidationPort:
    return HxcBatchCardTargetValidationAdapter()


__all__ = [
    "CardTargetValidationPort",
    "HxcBatchCardTargetValidationAdapter",
    "build_card_target_validation_port",
]
