from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from aicrm_next.platform_foundation.external_effects.execution_gates import explicit_wecom_execution_disabled
from aicrm_next.shared.postgres_connection import get_db
from aicrm_next.shared.runtime_settings import runtime_bool, runtime_setting
from aicrm_next.shared.wecom_payload_contract import normalize_group_admin_userids
from aicrm_next.shared.wecom_runtime import classify_wecom_provider_error

from .audit import record_audit_event
from .wecom_customer_group_client import WeComCustomerGroupClient, WeComCustomerGroupClientError
from .wecom_group_contract import Json

WECOM_GROUP_CHAT_ID_LIST_FIELD = "chat_id_list"


def _mode() -> str:
    if explicit_wecom_execution_disabled():
        return "disabled"
    value = str(runtime_setting("AICRM_WECOM_GROUP_ADAPTER_MODE") or "").strip().lower()
    return value if value in {"disabled", "fake", "staging", "production"} else "disabled"


def _enabled(name: str) -> bool:
    return runtime_bool(name)


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(repr(sorted((payload or {}).items())).encode("utf-8")).hexdigest()[:24]


def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    safe = dict(payload or {})
    safe.pop("token", None)
    safe.pop("access_token", None)
    return safe


def _provider_errcode(value: Any, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _provider_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def _requested_chat_ids(payload: dict[str, Any]) -> list[str]:
    return [str(item or "").strip() for item in list((payload or {}).get("chat_ids") or []) if str(item or "").strip()]


class WeComGroupMessageAdapter:
    adapter_name = "WeComGroupMessageAdapter"

    def __init__(self, *, mode: str | None = None, client_factory: Callable[[], Any] | None = None) -> None:
        self.mode = (mode or _mode()).strip().lower()
        if self.mode not in {"disabled", "fake", "staging", "production"}:
            self.mode = "disabled"
        self._client_factory = client_factory

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        return WeComCustomerGroupClient()

    def create_group_message_task(self, payload: dict[str, Any], *, idempotency_key: str = "") -> Json:
        normalized = self._build_wecom_payload(payload)
        requested_chat_ids = list(normalized.get(WECOM_GROUP_CHAT_ID_LIST_FIELD) or [])
        target = {
            "sender": normalized.get("sender", ""),
            "requested_chat_ids": requested_chat_ids,
            "requested_chat_count": len(requested_chat_ids),
            "exact_target_required": True,
            "official_chat_id_field": WECOM_GROUP_CHAT_ID_LIST_FIELD,
            "payload_hash": _hash_payload(_safe_payload(normalized)),
        }
        audit = record_audit_event(
            adapter=self.adapter_name,
            operation="create_group_message_task",
            mode=self.mode,
            idempotency_key=idempotency_key or _hash_payload(target),
            side_effect_executed=False,
            status="blocked" if self.mode in {"disabled", "staging"} else "ok",
            error_code="wecom_group_message_disabled" if self.mode in {"disabled", "staging"} else "",
        )
        if self.mode in {"disabled", "staging"}:
            return {
                "ok": False,
                "adapter": self.adapter_name,
                "mode": self.mode,
                "operation": "create_group_message_task",
                "idempotency_key": idempotency_key,
                "target": target,
                "result": {},
                "audit_id": audit["audit_id"],
                "side_effect_executed": False,
                "exact_target_required": True,
                "exact_target_verified": False,
                "requested_chat_ids": requested_chat_ids,
                "error_code": "wecom_group_message_disabled",
                "error_message": "real WeCom customer-group message creation is disabled",
            }
        if self.mode == "fake":
            return {
                "ok": True,
                "adapter": self.adapter_name,
                "mode": self.mode,
                "operation": "create_group_message_task",
                "idempotency_key": idempotency_key,
                "target": target,
                "result": {
                    "task_id": f"fake_group_msg_{target['payload_hash']}",
                    "requested_chat_ids": requested_chat_ids,
                    "requested_chat_count": len(requested_chat_ids),
                },
                "audit_id": audit["audit_id"],
                "side_effect_executed": False,
                "exact_target_required": True,
                "exact_target_verified": True,
                "exact_target_verification_source": "fake_adapter_requested_chat_ids",
                "requested_chat_ids": requested_chat_ids,
                "error_code": "",
                "error_message": "",
            }
        if not _enabled("AICRM_ENABLE_REAL_WECOM_GROUP_MESSAGE"):
            return {
                "ok": False,
                "adapter": self.adapter_name,
                "mode": self.mode,
                "operation": "create_group_message_task",
                "idempotency_key": idempotency_key,
                "target": target,
                "result": {},
                "audit_id": audit["audit_id"],
                "side_effect_executed": False,
                "exact_target_required": True,
                "exact_target_verified": False,
                "requested_chat_ids": requested_chat_ids,
                "error_code": "production_guard_failed",
                "error_message": "AICRM_ENABLE_REAL_WECOM_GROUP_MESSAGE is not enabled",
            }
        try:
            result = self._client().create_group_message_task(normalized)
        except WeComCustomerGroupClientError as exc:
            provider_errcode = _provider_errcode((exc.payload or {}).get("errcode"))
            derived_error_code, classification = classify_wecom_provider_error(
                provider_errcode=provider_errcode,
                transport_error=exc.error_code == "wecom_group_client_http_error",
            )
            return {
                "ok": False,
                "adapter": self.adapter_name,
                "mode": self.mode,
                "operation": "create_group_message_task",
                "idempotency_key": idempotency_key,
                "target": target,
                "result": exc.payload if exc.payload else {},
                "audit_id": audit["audit_id"],
                "side_effect_executed": True,
                "exact_target_required": True,
                "exact_target_verified": False,
                "requested_chat_ids": requested_chat_ids,
                "error_code": (
                    derived_error_code
                    if provider_errcode or exc.error_code == "wecom_group_client_http_error"
                    else exc.error_code or "wecom_group_message_client_error"
                ),
                "error_message": str(exc),
                "provider_errcode": provider_errcode,
                "provider_error_classification": classification,
                "retryable": classification == "retryable",
            }
        errcode = _provider_errcode(result.get("errcode")) if isinstance(result, dict) else -1
        msgid = str((result or {}).get("msgid") or "").strip() if isinstance(result, dict) else ""
        failed_chat_ids = _provider_string_list((result or {}).get("fail_list")) if isinstance(result, dict) else []
        if errcode != 0:
            error_code, classification = classify_wecom_provider_error(provider_errcode=errcode)
            return {
                "ok": False,
                "adapter": self.adapter_name,
                "mode": self.mode,
                "operation": "create_group_message_task",
                "idempotency_key": idempotency_key,
                "target": target,
                "result": result,
                "audit_id": audit["audit_id"],
                "side_effect_executed": True,
                "exact_target_required": True,
                "exact_target_verified": False,
                "requested_chat_ids": requested_chat_ids,
                "error_code": error_code,
                "error_message": str((result or {}).get("errmsg") or "WeCom group message API failed"),
                "provider_errcode": errcode,
                "provider_error_classification": classification,
                "retryable": classification == "retryable",
            }
        if not msgid:
            return {
                "ok": False,
                "adapter": self.adapter_name,
                "mode": self.mode,
                "operation": "create_group_message_task",
                "idempotency_key": idempotency_key,
                "target": target,
                "result": result,
                "audit_id": audit["audit_id"],
                "side_effect_executed": True,
                "exact_target_required": True,
                "exact_target_verified": False,
                "requested_chat_ids": requested_chat_ids,
                "error_code": "wecom_group_exact_target_not_verified",
                "error_message": "WeCom did not return msgid for exact target verification",
            }
        if failed_chat_ids:
            return {
                "ok": False,
                "adapter": self.adapter_name,
                "mode": self.mode,
                "operation": "create_group_message_task",
                "idempotency_key": idempotency_key,
                "target": target,
                "result": result,
                "audit_id": audit["audit_id"],
                "side_effect_executed": True,
                "exact_target_required": True,
                "exact_target_verified": False,
                "requested_chat_ids": requested_chat_ids,
                "requested_chat_count": len(requested_chat_ids),
                "failed_chat_ids": failed_chat_ids,
                "failed_chat_count": len(failed_chat_ids),
                "wecom_msgid": msgid,
                "error_code": "wecom_group_message_partial_failure",
                "error_message": f"WeCom rejected {len(failed_chat_ids)} requested customer-group targets",
            }
        return {
            "ok": True,
            "adapter": self.adapter_name,
            "mode": self.mode,
            "operation": "create_group_message_task",
            "idempotency_key": idempotency_key,
            "target": target,
            "result": result,
            "audit_id": audit["audit_id"],
            "side_effect_executed": True,
            "exact_target_required": True,
            "exact_target_verified": True,
            "exact_target_verification_source": f"wecom_add_msg_template.{WECOM_GROUP_CHAT_ID_LIST_FIELD}",
            "requested_chat_ids": requested_chat_ids,
            "requested_chat_count": len(requested_chat_ids),
            "wecom_msgid": msgid,
            "error_code": "",
            "error_message": "",
        }

    def _build_wecom_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        sender = str((payload or {}).get("sender") or "").strip()
        if not sender:
            raise ValueError("sender is required for WeCom group message")
        result = {
            "chat_type": "group",
            "sender": sender,
        }
        text = (payload or {}).get("text")
        if isinstance(text, dict) and str(text.get("content") or "").strip():
            result["text"] = {"content": str(text.get("content") or "").strip()}
        attachments = (payload or {}).get("attachments")
        if isinstance(attachments, list) and attachments:
            result["attachments"] = attachments
        chat_ids = _requested_chat_ids(payload)
        if not chat_ids:
            raise ValueError("chat_ids is required for exact WeCom customer-group targeting")
        # Official WeCom add_msg_template group targeting field is chat_id_list.
        # Keep internal chat_ids out of the outgoing request so WeCom cannot
        # ignore it and fall back to sender-wide customer groups.
        result[WECOM_GROUP_CHAT_ID_LIST_FIELD] = chat_ids
        result["allow_select"] = False
        if not result.get("text") and not result.get("attachments"):
            raise ValueError("text or attachments is required for WeCom group message")
        return result


def _fake_group_chat_snapshots(owner_userid: str) -> list[dict[str, Any]]:
    rows = [
        {
            "chat_id": "wrOgAAA001",
            "group_name": "体验课 01 群",
            "owner_userid": "owner_001",
            "owner_name": "王小明",
            "admin_userids": [],
            "internal_member_count": 12,
            "external_member_count": 150,
            "status": "active",
        },
        {
            "chat_id": "wrOgAAA002",
            "group_name": "体验课 02 群",
            "owner_userid": "owner_001",
            "owner_name": "王小明",
            "admin_userids": [],
            "internal_member_count": 10,
            "external_member_count": 160,
            "status": "active",
        },
        {
            "chat_id": "wrOgAAA003",
            "group_name": "体验课 03 群",
            "owner_userid": "owner_001",
            "owner_name": "王小明",
            "admin_userids": [],
            "internal_member_count": 9,
            "external_member_count": 176,
            "status": "active",
        },
        {
            "chat_id": "wrOgBBB001",
            "group_name": "成交陪跑 01 群",
            "owner_userid": "owner_002",
            "owner_name": "李小红",
            "admin_userids": ["admin_001"],
            "internal_member_count": 8,
            "external_member_count": 88,
            "status": "active",
        },
    ]
    owner = str(owner_userid or "").strip()
    return [dict(item) for item in rows if not owner or item["owner_userid"] == owner]


def _member_counts(member_list: list[Any]) -> tuple[int, int, int, list[str]]:
    internal = 0
    external = 0
    skipped = 0
    warnings: list[str] = []
    for member in member_list:
        if not isinstance(member, dict):
            skipped += 1
            warnings.append("skipped malformed group member")
            continue
        try:
            member_type = int(member.get("type") or 0)
            if member_type == 1 or (member.get("userid") and not member.get("unionid")):
                internal += 1
            else:
                external += 1
        except (TypeError, ValueError):
            skipped += 1
            warnings.append("skipped group member with invalid type")
    return internal, external, skipped, warnings


def _normalize_group_chat_detail(detail: dict[str, Any], *, fallback_owner_userid: str = "") -> dict[str, Any]:
    group_chat = detail.get("group_chat") if isinstance(detail.get("group_chat"), dict) else detail
    members = group_chat.get("member_list") if isinstance(group_chat.get("member_list"), list) else []
    internal, external, skipped, warnings = _member_counts(members)
    owner_userid = str(group_chat.get("owner") or group_chat.get("owner_userid") or fallback_owner_userid or "").strip()
    return {
        "chat_id": str(group_chat.get("chat_id") or "").strip(),
        "group_name": str(group_chat.get("name") or group_chat.get("group_name") or group_chat.get("chat_id") or "").strip(),
        "owner_userid": owner_userid,
        "owner_name": str(group_chat.get("owner_name") or owner_userid).strip(),
        "admin_userids": normalize_group_admin_userids(group_chat.get("admin_list") or group_chat.get("admin_userids")),
        "internal_member_count": internal,
        "external_member_count": external,
        "skipped_member_count": skipped,
        "warnings": warnings,
        "status": "active",
    }


class WeComGroupAssetAdapter:
    adapter_name = "WeComGroupAssetAdapter"

    def __init__(self, *, mode: str | None = None, client_factory: Callable[[], Any] | None = None) -> None:
        self.mode = (mode or _mode()).strip().lower()
        if self.mode not in {"disabled", "fake", "staging", "production"}:
            self.mode = "disabled"
        self._client_factory = client_factory

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        return WeComCustomerGroupClient()

    def list_group_chats(self, *, owner_userid: str, limit: int = 100, cursor: str = "") -> Json:
        owner = str(owner_userid or "").strip()
        page_size = max(1, min(int(limit or 100), 200))
        audit = record_audit_event(
            adapter=self.adapter_name,
            operation="list_group_chats",
            mode=self.mode,
            idempotency_key=_hash_payload({"owner_userid": owner, "limit": page_size, "cursor": cursor}),
            side_effect_executed=False,
            status="blocked" if self.mode in {"disabled", "staging"} else "ok",
            error_code="wecom_group_sync_disabled" if self.mode in {"disabled", "staging"} else "",
        )
        if self.mode in {"disabled", "staging"}:
            return {
                "ok": False,
                "adapter": self.adapter_name,
                "mode": self.mode,
                "operation": "list_group_chats",
                "groups": [],
                "next_cursor": "",
                "audit_id": audit["audit_id"],
                "side_effect_executed": False,
                "error_code": "wecom_group_sync_disabled",
                "error_message": "real WeCom customer-group sync is disabled",
            }
        if self.mode == "fake":
            groups = _fake_group_chat_snapshots(owner)[:page_size]
            return {
                "ok": True,
                "adapter": self.adapter_name,
                "mode": self.mode,
                "operation": "list_group_chats",
                "groups": groups,
                "next_cursor": "",
                "audit_id": audit["audit_id"],
                "side_effect_executed": False,
                "error_code": "",
                "error_message": "",
            }
        if not _enabled("AICRM_ENABLE_REAL_WECOM_GROUP_SYNC"):
            return {
                "ok": False,
                "adapter": self.adapter_name,
                "mode": self.mode,
                "operation": "list_group_chats",
                "groups": [],
                "next_cursor": "",
                "audit_id": audit["audit_id"],
                "side_effect_executed": False,
                "error_code": "production_guard_failed",
                "error_message": "AICRM_ENABLE_REAL_WECOM_GROUP_SYNC is not enabled",
            }

        list_payload = {
            "status_filter": 0,
            "owner_filter": {"userid_list": [owner]} if owner else {},
            "cursor": str(cursor or ""),
            "limit": page_size,
        }
        try:
            client = self._client()
            list_result = client.list_group_chats(list_payload)
        except WeComCustomerGroupClientError as exc:
            return {
                "ok": False,
                "adapter": self.adapter_name,
                "mode": self.mode,
                "operation": "list_group_chats",
                "groups": [],
                "next_cursor": "",
                "audit_id": audit["audit_id"],
                "side_effect_executed": True,
                "error_code": exc.error_code or "wecom_group_sync_client_error",
                "error_message": str(exc),
            }
        groups: list[dict[str, Any]] = []
        warnings: list[str] = []
        skipped_count = 0
        for item in list_result.get("group_chat_list") or []:
            chat_id = str((item or {}).get("chat_id") or "").strip()
            if not chat_id:
                skipped_count += 1
                warnings.append("skipped group chat without chat_id")
                continue
            try:
                detail_result = client.get_group_chat(chat_id, need_name=1)
                group = _normalize_group_chat_detail(detail_result, fallback_owner_userid=owner)
                skipped_count += int(group.pop("skipped_member_count", 0) or 0)
                warnings.extend([str(item) for item in group.pop("warnings", []) if str(item or "").strip()])
                groups.append(group)
            except WeComCustomerGroupClientError as exc:
                skipped_count += 1
                warnings.append(str(exc) or f"skipped group chat {chat_id}")
        return {
            "ok": True,
            "adapter": self.adapter_name,
            "mode": self.mode,
            "operation": "list_group_chats",
            "groups": groups,
            "next_cursor": str(list_result.get("next_cursor") or ""),
            "audit_id": audit["audit_id"],
            "side_effect_executed": True,
            "skipped_count": skipped_count,
            "warnings": warnings,
            "error_code": "",
            "error_message": "",
        }

    def get_group_chat(self, chat_id: str = "", *, need_name: int = 1, owner_userid: str = "") -> Json:
        chat = str(chat_id or "").strip()
        owner = str(owner_userid or "").strip()
        audit = record_audit_event(
            adapter=self.adapter_name,
            operation="get_group_chat",
            mode=self.mode,
            idempotency_key=_hash_payload({"chat_id": chat, "owner_userid": owner}),
            side_effect_executed=False,
            status="blocked" if self.mode in {"disabled", "staging"} else "ok",
            error_code="wecom_group_sync_disabled" if self.mode in {"disabled", "staging"} else "",
        )
        if self.mode in {"disabled", "staging"}:
            return {
                "ok": False,
                "adapter": self.adapter_name,
                "mode": self.mode,
                "operation": "get_group_chat",
                "group": {},
                "audit_id": audit["audit_id"],
                "side_effect_executed": False,
                "error_code": "wecom_group_sync_disabled",
                "error_message": "real WeCom customer-group sync is disabled",
            }
        if self.mode == "fake":
            group = next((item for item in _fake_group_chat_snapshots(owner) if item["chat_id"] == chat), None)
            return {
                "ok": bool(group),
                "adapter": self.adapter_name,
                "mode": self.mode,
                "operation": "get_group_chat",
                "group": dict(group or {}),
                "audit_id": audit["audit_id"],
                "side_effect_executed": False,
                "error_code": "" if group else "not_found",
                "error_message": "" if group else "fake group chat not found",
            }
        if not _enabled("AICRM_ENABLE_REAL_WECOM_GROUP_SYNC"):
            return {
                "ok": False,
                "adapter": self.adapter_name,
                "mode": self.mode,
                "operation": "get_group_chat",
                "group": {},
                "audit_id": audit["audit_id"],
                "side_effect_executed": False,
                "error_code": "production_guard_failed",
                "error_message": "AICRM_ENABLE_REAL_WECOM_GROUP_SYNC is not enabled",
            }
        try:
            result = self._client().get_group_chat(chat, need_name=need_name)
        except WeComCustomerGroupClientError as exc:
            return {
                "ok": False,
                "adapter": self.adapter_name,
                "mode": self.mode,
                "operation": "get_group_chat",
                "group": {},
                "audit_id": audit["audit_id"],
                "side_effect_executed": True,
                "error_code": exc.error_code or "wecom_group_sync_client_error",
                "error_message": str(exc),
            }
        return {
            "ok": True,
            "adapter": self.adapter_name,
            "mode": self.mode,
            "operation": "get_group_chat",
            "group": _normalize_group_chat_detail(result, fallback_owner_userid=owner),
            "audit_id": audit["audit_id"],
            "side_effect_executed": True,
            "error_code": "",
            "error_message": "",
        }


class WeComGroupChatSyncAdapter(WeComGroupAssetAdapter):
    adapter_name = "WeComGroupChatSyncAdapter"


def _decode_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except ValueError:
            return {}
    return {}


class NextGroupOpsQueueStatsGateway:
    def __init__(self, *, list_jobs_fn: Callable[[], list[dict[str, Any]]] | None = None) -> None:
        self._list_jobs_fn = list_jobs_fn

    def count_group_ops_queue(self) -> int:
        if self._list_jobs_fn is not None:
            rows = list(self._list_jobs_fn())
        else:
            rows = list(
                get_db()
                .execute(
                    """
                    SELECT source_table, content_payload
                    FROM broadcast_jobs
                    WHERE status IN (?, ?, ?)
                      AND source_type = ?
                    ORDER BY id DESC
                    LIMIT 1000
                    """,
                    ("queued", "waiting_approval", "claimed", "workflow"),
                )
                .fetchall()
            )
        count = 0
        for job in rows:
            payload = _decode_payload(job.get("content_payload"))
            if job.get("source_table") == "automation_group_ops_plans" or payload.get("channel") == "wecom_customer_group":
                count += 1
        return count


def build_wecom_group_message_adapter() -> WeComGroupMessageAdapter:
    return WeComGroupMessageAdapter()


def build_wecom_group_asset_adapter() -> WeComGroupAssetAdapter:
    return WeComGroupAssetAdapter()


def build_wecom_group_chat_sync_adapter() -> WeComGroupAssetAdapter:
    return build_wecom_group_asset_adapter()


def build_group_ops_queue_stats_gateway() -> NextGroupOpsQueueStatsGateway:
    return NextGroupOpsQueueStatsGateway()
