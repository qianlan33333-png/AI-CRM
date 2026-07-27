from __future__ import annotations

import hashlib
from typing import Any

from aicrm_next.platform.shared.runtime_settings import managed_runtime_bool, managed_runtime_setting

from .audit import record_audit_event
from .customer_sync_contracts import AdapterMode, Json
from .idempotency import get_or_create, make_idempotency_key


VALID_MODES = {"fake", "disabled", "staging", "production"}
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_NEXT_ARCHIVE_SYNC_MODE",
        "AICRM_NEXT_CONTACTS_SYNC_MODE",
        "AICRM_NEXT_CUSTOMER_PROJECTION_SYNC_MODE",
        "AICRM_NEXT_ENABLE_REAL_ARCHIVE_SYNC",
        "AICRM_NEXT_ENABLE_REAL_CONTACTS_SYNC",
        "AICRM_NEXT_ENABLE_REAL_CUSTOMER_PROJECTION_SYNC",
        "AICRM_NEXT_ENABLE_REAL_IDENTITY_MAPPING",
        "AICRM_NEXT_IDENTITY_MAPPING_MODE",
    }
)


def _normalise_mode(value: str | None, *, default: AdapterMode = "fake") -> AdapterMode:
    mode = (value or default).strip().lower()
    if mode not in VALID_MODES:
        return default
    return mode  # type: ignore[return-value]


def _env_true(name: str) -> bool:
    return managed_runtime_bool(name)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mode_prefix(mode: AdapterMode) -> str:
    return "staging" if mode == "staging" else "fake"


def _safe_target(target: dict[str, Any]) -> dict[str, Any]:
    forbidden = {
        "secret",
        "token",
        "access_token",
        "client_secret",
        "app_secret",
        "credential",
        "password",
        "api_key",
        "private_key",
        "cert",
        "certificate",
        "archive_key",
        "archive_private_key",
        "archive_secret",
        "webhook_token",
    }

    def is_secret_key(key: str) -> bool:
        lowered = key.lower()
        return any(marker in lowered for marker in forbidden)

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items() if not is_secret_key(key)}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    return scrub(target)


def _payload_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    safe = _safe_target(payload or {})
    return {"payload_hash": _digest(repr(sorted(safe.items())))[:24], "payload_keys": sorted(safe.keys())}


def _side_effect_safety() -> dict[str, bool]:
    return {
        "real_archive_sync_executed": False,
        "real_contacts_sync_executed": False,
        "real_identity_mapping_write_executed": False,
        "real_customer_projection_write_executed": False,
        "real_wecom_call_executed": False,
    }


def _base_result(
    *,
    ok: bool,
    adapter: str,
    mode: AdapterMode,
    operation: str,
    idempotency_key: str,
    target: dict[str, Any],
    result: dict[str, Any] | None,
    audit_id: str,
    error_code: str = "",
    error_message: str = "",
) -> Json:
    return {
        "ok": ok,
        "adapter": adapter,
        "mode": mode,
        "operation": operation,
        "idempotency_key": idempotency_key,
        "target": _safe_target(target),
        "result": result or {},
        "audit_id": audit_id,
        "side_effect_executed": False,
        "error_code": error_code,
        "error_message": error_message,
    }


class _GuardedCustomerSyncAdapter:
    adapter_name = "CustomerSyncAdapter"
    production_flag = ""

    def __init__(self, mode: AdapterMode | str = "fake") -> None:
        self.mode = _normalise_mode(str(mode), default="fake")

    def _guarded_result(self, *, operation: str, idempotency_key: str, target: dict[str, Any]) -> Json | None:
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
                target=target,
                result={},
                audit_id=audit["audit_id"],
                error_code="adapter_disabled",
                error_message=f"{self.adapter_name} is disabled",
            )
        if self.mode == "production":
            error_code = "production_guard_failed" if not _env_true(self.production_flag) else "production_not_implemented"
            audit = record_audit_event(
                adapter=self.adapter_name,
                operation=operation,
                mode=self.mode,
                idempotency_key=idempotency_key,
                side_effect_executed=False,
                status="blocked",
                error_code=error_code,
            )
            return _base_result(
                ok=False,
                adapter=self.adapter_name,
                mode=self.mode,
                operation=operation,
                idempotency_key=idempotency_key,
                target=target,
                result={},
                audit_id=audit["audit_id"],
                error_code=error_code,
                error_message=f"{self.adapter_name} production mode is not implemented in D7.6",
            )
        return None

    def _operation(self, operation: str, *, target: dict[str, Any], result_factory, idempotency_key: str | None = None) -> Json:
        key = idempotency_key or make_idempotency_key(operation=operation, payload=_safe_target(target))
        guarded = self._guarded_result(operation=operation, idempotency_key=key, target=target)
        if guarded:
            return guarded
        cached = get_or_create(key, result_factory)
        audit = record_audit_event(
            adapter=self.adapter_name,
            operation=operation,
            mode=self.mode,
            idempotency_key=key,
            side_effect_executed=False,
            status="ok",
        )
        return _base_result(
            ok=True,
            adapter=self.adapter_name,
            mode=self.mode,
            operation=operation,
            idempotency_key=key,
            target=target,
            result=cached,
            audit_id=audit["audit_id"],
        )

    def _audit_only(
        self,
        *,
        operation: str,
        target: dict[str, Any],
        result: dict[str, Any] | None = None,
        error_code: str = "",
        idempotency_key: str | None = None,
    ) -> Json:
        key = idempotency_key or make_idempotency_key(operation=operation, payload={"target": _safe_target(target), "result": result or {}})
        audit = record_audit_event(
            adapter=self.adapter_name,
            operation=operation,
            mode=self.mode,
            idempotency_key=key,
            side_effect_executed=False,
            status="blocked" if error_code else "ok",
            error_code=error_code,
        )
        return _base_result(
            ok=not error_code,
            adapter=self.adapter_name,
            mode=self.mode,
            operation=operation,
            idempotency_key=key,
            target=target,
            result=result or {},
            audit_id=audit["audit_id"],
            error_code=error_code,
            error_message="" if not error_code else "audit recorded as blocked",
        )


class ArchiveSyncAdapter(_GuardedCustomerSyncAdapter):
    adapter_name = "ArchiveSyncAdapter"
    production_flag = "AICRM_NEXT_ENABLE_REAL_ARCHIVE_SYNC"

    def fetch_recent_messages(self, *, external_userid: str, limit: int = 20, sync_cursor: str = "", idempotency_key: str | None = None) -> Json:
        target = {"external_userid": external_userid, "limit": limit, "sync_cursor": sync_cursor}
        mode_prefix = _mode_prefix(self.mode)
        return self._operation(
            "fetch_recent_messages",
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {
                "messages_preview_id": f"{mode_prefix}_archive_recent_{_digest(repr(target))[:16]}",
                "external_userid": external_userid,
                "limit": limit,
                "sync_cursor": sync_cursor,
                "message_count": 0,
                "side_effect_safety": _side_effect_safety(),
            },
        )

    def fetch_incremental_archive_messages(self, *, sync_cursor: str = "", limit: int = 100, corp_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"sync_cursor": sync_cursor, "limit": limit, "corp_id": corp_id}
        mode_prefix = _mode_prefix(self.mode)
        return self._operation(
            "fetch_incremental_archive_messages",
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {
                "archive_batch_id": f"{mode_prefix}_archive_batch_{_digest(repr(target))[:16]}",
                "next_cursor": sync_cursor or f"{mode_prefix}_cursor_0",
                "message_count": 0,
                "side_effect_safety": _side_effect_safety(),
            },
        )

    def normalize_archive_message(self, *, msgid: str = "", external_userid: str = "", payload: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        target = {"msgid": msgid, "external_userid": external_userid, "payload_summary": _payload_summary(payload)}
        return self._operation(
            "normalize_archive_message",
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {"msgid": msgid or f"fake_msg_{_digest(repr(target))[:12]}", "normalized": True, "side_effect_safety": _side_effect_safety()},
        )

    def build_archive_sync_preview(self, *, external_userid: str = "", msgid: str = "", sync_cursor: str = "", payload_summary: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        target = {"external_userid": external_userid, "msgid": msgid, "sync_cursor": sync_cursor, "payload_summary": _payload_summary(payload_summary)}
        return self._operation(
            "build_archive_sync_preview",
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {"preview_id": f"{_mode_prefix(self.mode)}_archive_preview_{_digest(repr(target))[:16]}", "side_effect_safety": _side_effect_safety()},
        )

    def record_archive_sync_audit(self, *, operation: str, target: dict[str, Any], result: dict[str, Any] | None = None, error_code: str = "", idempotency_key: str | None = None) -> Json:
        return self._audit_only(operation=operation, target=target, result=result, error_code=error_code, idempotency_key=idempotency_key)


class ContactsSyncAdapter(_GuardedCustomerSyncAdapter):
    adapter_name = "ContactsSyncAdapter"
    production_flag = "AICRM_NEXT_ENABLE_REAL_CONTACTS_SYNC"

    def fetch_external_contacts(self, *, follow_user_userid: str = "", sync_cursor: str = "", limit: int = 100, corp_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"follow_user_userid": follow_user_userid, "sync_cursor": sync_cursor, "limit": limit, "corp_id": corp_id}
        return self._contacts_operation("fetch_external_contacts", target=target, idempotency_key=idempotency_key)

    def fetch_contact_detail(self, *, external_userid: str, follow_user_userid: str = "", corp_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"external_userid": external_userid, "follow_user_userid": follow_user_userid, "corp_id": corp_id}
        return self._contacts_operation("fetch_contact_detail", target=target, idempotency_key=idempotency_key)

    def fetch_follow_user_relations(self, *, external_userid: str = "", follow_user_userid: str = "", corp_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"external_userid": external_userid, "follow_user_userid": follow_user_userid, "corp_id": corp_id}
        return self._contacts_operation("fetch_follow_user_relations", target=target, idempotency_key=idempotency_key)

    def build_contacts_sync_preview(self, *, external_userid: str = "", follow_user_userid: str = "", sync_cursor: str = "", payload_summary: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        target = {"external_userid": external_userid, "follow_user_userid": follow_user_userid, "sync_cursor": sync_cursor, "payload_summary": _payload_summary(payload_summary)}
        return self._contacts_operation("build_contacts_sync_preview", target=target, idempotency_key=idempotency_key)

    def record_contacts_sync_audit(self, *, operation: str, target: dict[str, Any], result: dict[str, Any] | None = None, error_code: str = "", idempotency_key: str | None = None) -> Json:
        return self._audit_only(operation=operation, target=target, result=result, error_code=error_code, idempotency_key=idempotency_key)

    def _contacts_operation(self, operation: str, *, target: dict[str, Any], idempotency_key: str | None = None) -> Json:
        mode_prefix = _mode_prefix(self.mode)
        return self._operation(
            operation,
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {
                "contacts_sync_id": f"{mode_prefix}_contacts_{_digest(operation + repr(target))[:16]}",
                "contact_count": 0,
                "side_effect_safety": _side_effect_safety(),
            },
        )


class IdentityMappingAdapter(_GuardedCustomerSyncAdapter):
    adapter_name = "IdentityMappingAdapter"
    production_flag = "AICRM_NEXT_ENABLE_REAL_IDENTITY_MAPPING"

    def resolve_person_identity(self, *, external_userid: str = "", openid: str = "", unionid: str = "", mobile: str = "", person_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"external_userid": external_userid, "openid": openid, "unionid": unionid, "mobile": mobile, "person_id": person_id}
        return self._identity_operation("resolve_person_identity", target=target, idempotency_key=idempotency_key, applied=False)

    def upsert_identity_mapping(self, *, external_userid: str = "", openid: str = "", unionid: str = "", mobile: str = "", person_id: str = "", corp_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"external_userid": external_userid, "openid": openid, "unionid": unionid, "mobile": mobile, "person_id": person_id, "corp_id": corp_id}
        return self._identity_operation("upsert_identity_mapping", target=target, idempotency_key=idempotency_key, applied=False)

    def link_openid_unionid_external_userid(self, *, external_userid: str, openid: str = "", unionid: str = "", corp_id: str = "", idempotency_key: str | None = None) -> Json:
        target = {"external_userid": external_userid, "openid": openid, "unionid": unionid, "corp_id": corp_id}
        return self._identity_operation("link_openid_unionid_external_userid", target=target, idempotency_key=idempotency_key, applied=False)

    def build_identity_mapping_preview(self, *, external_userid: str = "", openid: str = "", unionid: str = "", person_id: str = "", payload_summary: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        target = {"external_userid": external_userid, "openid": openid, "unionid": unionid, "person_id": person_id, "payload_summary": _payload_summary(payload_summary)}
        return self._identity_operation("build_identity_mapping_preview", target=target, idempotency_key=idempotency_key, applied=False)

    def record_identity_mapping_audit(self, *, operation: str, target: dict[str, Any], result: dict[str, Any] | None = None, error_code: str = "", idempotency_key: str | None = None) -> Json:
        return self._audit_only(operation=operation, target=target, result=result, error_code=error_code, idempotency_key=idempotency_key)

    def _identity_operation(self, operation: str, *, target: dict[str, Any], idempotency_key: str | None = None, applied: bool = False) -> Json:
        mode_prefix = _mode_prefix(self.mode)
        return self._operation(
            operation,
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {
                "identity_mapping_id": f"{mode_prefix}_identity_{_digest(operation + repr(target))[:16]}",
                "resolved": bool(target.get("external_userid") or target.get("openid") or target.get("unionid") or target.get("mobile") or target.get("person_id")),
                "applied": applied,
                "side_effect_safety": _side_effect_safety(),
            },
        )


class CustomerProjectionSyncGateway(_GuardedCustomerSyncAdapter):
    adapter_name = "CustomerProjectionSyncGateway"
    production_flag = "AICRM_NEXT_ENABLE_REAL_CUSTOMER_PROJECTION_SYNC"

    def update_customer_list_projection(self, *, projection_name: str = "customer_list", sync_cursor: str = "", external_userid: str = "", idempotency_key: str | None = None) -> Json:
        target = {"projection_name": projection_name, "sync_cursor": sync_cursor, "external_userid": external_userid}
        return self._projection_operation("update_customer_list_projection", target=target, idempotency_key=idempotency_key)

    def update_customer_detail_projection(self, *, external_userid: str, projection_name: str = "customer_detail", idempotency_key: str | None = None) -> Json:
        target = {"projection_name": projection_name, "external_userid": external_userid}
        return self._projection_operation("update_customer_detail_projection", target=target, idempotency_key=idempotency_key)

    def update_customer_timeline_projection(self, *, external_userid: str, projection_name: str = "customer_timeline", sync_cursor: str = "", idempotency_key: str | None = None) -> Json:
        target = {"projection_name": projection_name, "external_userid": external_userid, "sync_cursor": sync_cursor}
        return self._projection_operation("update_customer_timeline_projection", target=target, idempotency_key=idempotency_key)

    def update_recent_messages_projection(self, *, external_userid: str, projection_name: str = "recent_messages", sync_cursor: str = "", idempotency_key: str | None = None) -> Json:
        target = {"projection_name": projection_name, "external_userid": external_userid, "sync_cursor": sync_cursor}
        return self._projection_operation("update_recent_messages_projection", target=target, idempotency_key=idempotency_key)

    def build_projection_sync_preview(self, *, projection_name: str, external_userid: str = "", sync_cursor: str = "", payload_summary: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Json:
        target = {"projection_name": projection_name, "external_userid": external_userid, "sync_cursor": sync_cursor, "payload_summary": _payload_summary(payload_summary)}
        return self._projection_operation("build_projection_sync_preview", target=target, idempotency_key=idempotency_key)

    def record_projection_sync_audit(self, *, operation: str, target: dict[str, Any], result: dict[str, Any] | None = None, error_code: str = "", idempotency_key: str | None = None) -> Json:
        return self._audit_only(operation=operation, target=target, result=result, error_code=error_code, idempotency_key=idempotency_key)

    def _projection_operation(self, operation: str, *, target: dict[str, Any], idempotency_key: str | None = None) -> Json:
        mode_prefix = _mode_prefix(self.mode)
        return self._operation(
            operation,
            target=target,
            idempotency_key=idempotency_key,
            result_factory=lambda: {
                "projection_sync_id": f"{mode_prefix}_projection_{_digest(operation + repr(target))[:16]}",
                "projection_name": target.get("projection_name") or "",
                "applied": False,
                "side_effect_safety": _side_effect_safety(),
            },
        )


def build_archive_sync_adapter() -> ArchiveSyncAdapter:
    return ArchiveSyncAdapter(managed_runtime_setting("AICRM_NEXT_ARCHIVE_SYNC_MODE", "fake"))


def build_contacts_sync_adapter() -> ContactsSyncAdapter:
    return ContactsSyncAdapter(managed_runtime_setting("AICRM_NEXT_CONTACTS_SYNC_MODE", "fake"))


def build_identity_mapping_adapter() -> IdentityMappingAdapter:
    return IdentityMappingAdapter(managed_runtime_setting("AICRM_NEXT_IDENTITY_MAPPING_MODE", "fake"))


def build_customer_projection_sync_gateway() -> CustomerProjectionSyncGateway:
    return CustomerProjectionSyncGateway(
        managed_runtime_setting("AICRM_NEXT_CUSTOMER_PROJECTION_SYNC_MODE", "fake")
    )


def customer_sync_side_effect_safety() -> dict[str, bool]:
    return _side_effect_safety()
