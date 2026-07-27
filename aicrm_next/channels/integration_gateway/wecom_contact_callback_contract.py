from __future__ import annotations

from typing import Any, Protocol


Json = dict[str, Any]


class WeComContactCallbackContract(Protocol):
    def verify_callback_contract(self, *, signature: str, timestamp: str, nonce: str, echostr: str) -> Json:
        ...

    def parse_external_contact_event(self, payload: Json) -> Json:
        ...

    def normalize_external_contact_event(self, event: Json) -> Json:
        ...

    def dry_run_record_contact_event(self, *, event: Json, operator: str, idempotency_key: str) -> Json:
        ...

    def dry_run_identity_mapping(self, *, event: Json, operator: str, idempotency_key: str) -> Json:
        ...
