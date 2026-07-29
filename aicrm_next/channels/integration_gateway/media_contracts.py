from __future__ import annotations

from typing import Any, Literal, Protocol


Json = dict[str, Any]
AdapterMode = Literal["fake", "disabled", "staging", "production"]

REQUIRED_MEDIA_ADAPTER_RESULT_FIELDS = (
    "ok",
    "adapter",
    "mode",
    "operation",
    "idempotency_key",
    "storage_key",
    "media_id",
    "public_url",
    "reference_url",
    "audit_id",
    "side_effect_executed",
    "error_code",
    "error_message",
)


class CloudStorageAdapterContract(Protocol):
    def put_object(self, *, content: bytes, file_name: str, content_type: str, idempotency_key: str | None = None) -> Json: ...
    def put_base64_object(self, *, data_base64: str, file_name: str, content_type: str, idempotency_key: str | None = None) -> Json: ...
    def put_remote_reference(self, *, source_url: str, file_name: str, content_type: str, idempotency_key: str | None = None) -> Json: ...
    def get_public_reference(self, *, storage_key: str, idempotency_key: str | None = None) -> Json: ...
    def delete_object(self, *, storage_key: str, idempotency_key: str | None = None) -> Json: ...


class WeComMediaAdapterContract(Protocol):
    def upload_image(self, *, data_base64: str, file_name: str, idempotency_key: str | None = None) -> Json: ...
    def upload_attachment(self, *, data_base64: str, file_name: str, content_type: str, idempotency_key: str | None = None) -> Json: ...
    def resolve_media_id(self, *, reference_url: str, file_name: str, idempotency_key: str | None = None) -> Json: ...
    def delete_or_expire_reference(self, *, media_id: str, idempotency_key: str | None = None) -> Json: ...
