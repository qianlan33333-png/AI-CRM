from __future__ import annotations

from typing import Any, Protocol


Json = dict[str, Any]


class WeComContactCallbackLiveGateway(Protocol):
    def verify_callback_live(self, *, signature: str, timestamp: str, nonce: str, echostr: str) -> Json:
        ...

    def process_external_contact_event_live(self, *, event: Json, operator: str) -> Json:
        ...

    def record_identity_mapping_live(self, *, event: Json, operator: str) -> Json:
        ...


class DisabledWeComContactCallbackLiveGateway:
    def verify_callback_live(self, *, signature: str, timestamp: str, nonce: str, echostr: str) -> Json:
        return {"ok": False, "error_code": "adapter_unavailable", "result_status": "live_gateway_not_configured"}

    def process_external_contact_event_live(self, *, event: Json, operator: str) -> Json:
        return {"ok": False, "error_code": "adapter_unavailable", "result_status": "live_gateway_not_configured"}

    def record_identity_mapping_live(self, *, event: Json, operator: str) -> Json:
        return {"ok": False, "error_code": "adapter_unavailable", "result_status": "live_gateway_not_configured"}


def build_wecom_contact_callback_live_gateway() -> WeComContactCallbackLiveGateway:
    return DisabledWeComContactCallbackLiveGateway()
