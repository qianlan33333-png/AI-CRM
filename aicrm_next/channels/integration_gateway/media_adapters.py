from __future__ import annotations

import base64
import hashlib

from aicrm_next.platform.shared.runtime_settings import managed_runtime_setting

from .audit import record_audit_event
from .idempotency import get_or_create, make_idempotency_key
from .media_contracts import AdapterMode, Json


VALID_MODES = {"fake", "disabled", "staging"}


def _normalise_mode(value: str | None, *, default: AdapterMode = "fake") -> AdapterMode:
    mode = (value or default).strip().lower()
    if mode not in VALID_MODES:
        return default
    return mode  # type: ignore[return-value]


def _digest(value: str | bytes) -> str:
    data = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _base_result(
    *,
    ok: bool,
    adapter: str,
    mode: AdapterMode,
    operation: str,
    idempotency_key: str,
    audit_id: str,
    storage_key: str | None = None,
    media_id: str | None = None,
    public_url: str | None = None,
    reference_url: str | None = None,
    error_code: str = "",
    error_message: str = "",
) -> Json:
    return {
        "ok": ok,
        "adapter": adapter,
        "mode": mode,
        "operation": operation,
        "idempotency_key": idempotency_key,
        "storage_key": storage_key,
        "media_id": media_id,
        "public_url": public_url,
        "reference_url": reference_url,
        "audit_id": audit_id,
        "side_effect_executed": False,
        "error_code": error_code,
        "error_message": error_message,
    }


class _GuardedMediaAdapter:
    adapter_name = "media_adapter"
    production_flag = ""

    def __init__(self, mode: AdapterMode | str = "fake") -> None:
        self.mode = _normalise_mode(str(mode), default="fake")

    def _guarded_result(self, *, operation: str, idempotency_key: str) -> Json | None:
        if self.mode == "disabled":
            audit = record_audit_event(
                adapter=self.adapter_name,
                operation=operation,
                mode=self.mode,
                idempotency_key=idempotency_key,
                side_effect_executed=False,
                status="blocked",
                error_code="adapter_disabled",
            )
            return _base_result(
                ok=False,
                adapter=self.adapter_name,
                mode=self.mode,
                operation=operation,
                idempotency_key=idempotency_key,
                audit_id=audit["audit_id"],
                error_code="adapter_disabled",
                error_message=f"{self.adapter_name} is disabled",
            )
        return None

    def _successful_result(self, *, operation: str, idempotency_key: str, factory) -> Json:
        cached = get_or_create(idempotency_key, factory)
        audit = record_audit_event(
            adapter=self.adapter_name,
            operation=operation,
            mode=self.mode,
            idempotency_key=idempotency_key,
            side_effect_executed=False,
            status="ok",
        )
        return _base_result(
            ok=True,
            adapter=self.adapter_name,
            mode=self.mode,
            operation=operation,
            idempotency_key=idempotency_key,
            audit_id=audit["audit_id"],
            storage_key=cached.get("storage_key"),
            media_id=cached.get("media_id"),
            public_url=cached.get("public_url"),
            reference_url=cached.get("reference_url"),
        )


class CloudStorageAdapter(_GuardedMediaAdapter):
    adapter_name = "CloudStorageAdapter"
    production_flag = "AICRM_NEXT_ENABLE_REAL_CLOUD_STORAGE"

    def put_object(self, *, content: bytes, file_name: str, content_type: str, idempotency_key: str | None = None) -> Json:
        operation = "put_object"
        key = idempotency_key or make_idempotency_key(operation=operation, payload={"content_hash": _digest(content), "file_name": file_name, "content_type": content_type})
        guarded = self._guarded_result(operation=operation, idempotency_key=key)
        if guarded:
            return guarded
        return self._successful_result(operation=operation, idempotency_key=key, factory=lambda: self._fake_object(operation, key, file_name))

    def put_base64_object(self, *, data_base64: str, file_name: str, content_type: str, idempotency_key: str | None = None) -> Json:
        operation = "put_base64_object"
        key = idempotency_key or make_idempotency_key(operation=operation, payload={"data_hash": _digest(data_base64), "file_name": file_name, "content_type": content_type})
        guarded = self._guarded_result(operation=operation, idempotency_key=key)
        if guarded:
            return guarded
        return self._successful_result(operation=operation, idempotency_key=key, factory=lambda: self._fake_object(operation, key, file_name))

    def put_remote_reference(self, *, source_url: str, file_name: str, content_type: str, idempotency_key: str | None = None) -> Json:
        operation = "put_remote_reference"
        key = idempotency_key or make_idempotency_key(operation=operation, payload={"source_url": source_url, "file_name": file_name, "content_type": content_type})
        guarded = self._guarded_result(operation=operation, idempotency_key=key)
        if guarded:
            return guarded
        return self._successful_result(operation=operation, idempotency_key=key, factory=lambda: self._fake_object(operation, key, file_name, source_url=source_url))

    def get_public_reference(self, *, storage_key: str, idempotency_key: str | None = None) -> Json:
        operation = "get_public_reference"
        key = idempotency_key or make_idempotency_key(operation=operation, payload={"storage_key": storage_key})
        guarded = self._guarded_result(operation=operation, idempotency_key=key)
        if guarded:
            return guarded
        return self._successful_result(operation=operation, idempotency_key=key, factory=lambda: self._fake_reference(storage_key))

    def delete_object(self, *, storage_key: str, idempotency_key: str | None = None) -> Json:
        operation = "delete_object"
        key = idempotency_key or make_idempotency_key(operation=operation, payload={"storage_key": storage_key})
        guarded = self._guarded_result(operation=operation, idempotency_key=key)
        if guarded:
            return guarded
        return self._successful_result(operation=operation, idempotency_key=key, factory=lambda: {"storage_key": storage_key, "public_url": None, "reference_url": f"fake://deleted/{storage_key}"})

    def _fake_object(self, operation: str, idempotency_key: str, file_name: str, *, source_url: str | None = None) -> Json:
        digest = _digest(idempotency_key)[:24]
        mode_prefix = "staging" if self.mode == "staging" else "fake"
        safe_name = file_name.replace("/", "_") or "object.bin"
        storage_key = f"{mode_prefix}/media/{operation}/{digest}/{safe_name}"
        reference_url = source_url or f"fake://cloud-storage/{storage_key}"
        return {
            "storage_key": storage_key,
            "public_url": f"https://{mode_prefix}.storage.invalid/{storage_key}",
            "reference_url": reference_url,
        }

    def _fake_reference(self, storage_key: str) -> Json:
        mode_prefix = "staging" if self.mode == "staging" else "fake"
        return {
            "storage_key": storage_key,
            "public_url": f"https://{mode_prefix}.storage.invalid/{storage_key}",
            "reference_url": f"fake://cloud-storage/{storage_key}",
        }


class WeComMediaAdapter(_GuardedMediaAdapter):
    adapter_name = "WeComMediaAdapter"
    production_flag = "AICRM_NEXT_ENABLE_REAL_WECOM_MEDIA"

    def upload_image(self, *, data_base64: str, file_name: str, idempotency_key: str | None = None) -> Json:
        operation = "upload_image"
        key = idempotency_key or make_idempotency_key(operation=operation, payload={"data_hash": _digest(data_base64), "file_name": file_name})
        guarded = self._guarded_result(operation=operation, idempotency_key=key)
        if guarded:
            return guarded
        return self._successful_result(operation=operation, idempotency_key=key, factory=lambda: self._fake_media(operation, key))

    def upload_attachment(self, *, data_base64: str, file_name: str, content_type: str, idempotency_key: str | None = None) -> Json:
        operation = "upload_attachment"
        key = idempotency_key or make_idempotency_key(operation=operation, payload={"data_hash": _digest(data_base64), "file_name": file_name, "content_type": content_type})
        guarded = self._guarded_result(operation=operation, idempotency_key=key)
        if guarded:
            return guarded
        return self._successful_result(operation=operation, idempotency_key=key, factory=lambda: self._fake_media(operation, key))

    def resolve_media_id(self, *, reference_url: str, file_name: str, idempotency_key: str | None = None) -> Json:
        operation = "resolve_media_id"
        key = idempotency_key or make_idempotency_key(operation=operation, payload={"reference_url": reference_url, "file_name": file_name})
        guarded = self._guarded_result(operation=operation, idempotency_key=key)
        if guarded:
            return guarded
        return self._successful_result(operation=operation, idempotency_key=key, factory=lambda: self._fake_media(operation, key))

    def delete_or_expire_reference(self, *, media_id: str, idempotency_key: str | None = None) -> Json:
        operation = "delete_or_expire_reference"
        key = idempotency_key or make_idempotency_key(operation=operation, payload={"media_id": media_id})
        guarded = self._guarded_result(operation=operation, idempotency_key=key)
        if guarded:
            return guarded
        return self._successful_result(operation=operation, idempotency_key=key, factory=lambda: {"media_id": media_id, "reference_url": f"fake://wecom-media/expired/{media_id}"})

    def _fake_media(self, operation: str, idempotency_key: str) -> Json:
        digest = _digest(idempotency_key)[:24]
        mode_prefix = "staging" if self.mode == "staging" else "fake"
        media_id = f"{mode_prefix}_wecom_media_{digest}"
        return {
            "media_id": media_id,
            "reference_url": f"fake://wecom-media/{operation}/{media_id}",
        }


def build_cloud_storage_adapter() -> CloudStorageAdapter:
    return CloudStorageAdapter(managed_runtime_setting("AICRM_NEXT_MEDIA_STORAGE_MODE", "fake"))


def build_wecom_media_adapter() -> WeComMediaAdapter:
    return WeComMediaAdapter(managed_runtime_setting("AICRM_NEXT_WECOM_MEDIA_MODE", "fake"))


def extract_base64_payload(data_url_or_base64: str) -> str:
    if "," in data_url_or_base64 and data_url_or_base64.lower().startswith("data:"):
        return data_url_or_base64.split(",", 1)[1]
    return data_url_or_base64


def decode_base64_payload(data_url_or_base64: str) -> bytes:
    return base64.b64decode(extract_base64_payload(data_url_or_base64), validate=False)
