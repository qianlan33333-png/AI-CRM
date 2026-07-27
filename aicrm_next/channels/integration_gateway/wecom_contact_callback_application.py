from __future__ import annotations

from typing import Any

from .wecom_contact_callback_adapter import build_fake_stub_wecom_contact_callback_adapter
from .wecom_contact_callback_contract import WeComContactCallbackContract
from .wecom_contact_callback_live_adapter import LiveWeComContactCallbackAdapter, build_live_wecom_contact_callback_adapter


Json = dict[str, Any]
_DEFAULT_ADAPTER = build_fake_stub_wecom_contact_callback_adapter()


def reset_wecom_contact_callback_fake_stub_state() -> None:
    _DEFAULT_ADAPTER.reset_state()


class WeComContactCallbackApplicationService:
    def __init__(self, adapter: WeComContactCallbackContract | None = None) -> None:
        self._adapter = adapter or _DEFAULT_ADAPTER

    def verify_callback_contract(self, *, signature: str, timestamp: str, nonce: str, echostr: str) -> Json:
        return self._adapter.verify_callback_contract(signature=signature, timestamp=timestamp, nonce=nonce, echostr=echostr)

    def parse_external_contact_event(self, payload: Json) -> Json:
        return self._adapter.parse_external_contact_event(payload)

    def normalize_external_contact_event(self, event: Json) -> Json:
        return self._adapter.normalize_external_contact_event(event)

    def dry_run_record_contact_event(self, *, event: Json, operator: str, idempotency_key: str) -> Json:
        return self._adapter.dry_run_record_contact_event(event=event, operator=operator, idempotency_key=idempotency_key)

    def dry_run_identity_mapping(self, *, event: Json, operator: str, idempotency_key: str) -> Json:
        return self._adapter.dry_run_identity_mapping(event=event, operator=operator, idempotency_key=idempotency_key)

    def live_callback_attempt(self) -> Json:
        if hasattr(self._adapter, "live_callback_attempt"):
            return self._adapter.live_callback_attempt()  # type: ignore[attr-defined]
        return {"ok": False, "error_code": "live_callback_not_enabled", "live_callback_processed": False}


def build_wecom_contact_callback_application_service() -> WeComContactCallbackApplicationService:
    return WeComContactCallbackApplicationService()


def build_live_wecom_contact_callback_application_service(*, confirm_live_wecom_callback: bool = False) -> LiveWeComContactCallbackAdapter:
    return build_live_wecom_contact_callback_adapter(confirm_live_wecom_callback=confirm_live_wecom_callback)
