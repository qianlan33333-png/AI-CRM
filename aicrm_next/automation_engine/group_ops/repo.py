from __future__ import annotations

import os
from copy import deepcopy
from typing import Any, Protocol

from aicrm_next.shared.db_session import get_engine
from aicrm_next.shared.errors import ContractError, NotFoundError
from aicrm_next.shared.repository_provider import assert_repository_allowed
from aicrm_next.shared.runtime import production_data_ready, raw_database_url

from .domain import (
    binding_stats,
    clamp_limit,
    clean_text,
    derive_node_scheduled_time,
    generate_webhook_key,
    group_manageable_by_userid,
    mask_sensitive_payload,
    normalize_group_admin_userids,
    normalize_plan_payload,
    normalize_scope_ids,
    utc_now_iso,
)

GROUP_OPS_BACKEND_ENV = "AICRM_GROUP_OPS_REPO_BACKEND"
GROUP_OPS_DATABASE_URL_ENV = "AICRM_GROUP_OPS_DATABASE_URL"
GROUP_OPS_SQL_BACKENDS = {"sql", "sqlalchemy", "postgres", "postgresql"}


class GroupOpsRepository(Protocol):
    def list_plans(self, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]: ...
    def get_plan(self, plan_id: int) -> dict[str, Any] | None: ...
    def get_plan_by_webhook_key(self, webhook_key: str) -> dict[str, Any] | None: ...
    def create_plan(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def update_plan(self, plan_id: int, payload: dict[str, Any]) -> dict[str, Any]: ...
    def archive_plan(self, plan_id: int, *, operator: str = "system") -> dict[str, Any]: ...
    def replace_plan_scopes(self, plan_id: int, *, scope_type: str, scope_ref_ids: list[str]) -> list[dict[str, Any]]: ...
    def list_plan_scopes(self, plan_id: int, scope_type: str = "") -> list[dict[str, Any]]: ...
    def list_bound_groups(self, plan_id: int) -> list[dict[str, Any]]: ...
    def bind_group(self, plan_id: int, group: dict[str, Any]) -> dict[str, Any]: ...
    def remove_group(self, plan_id: int, chat_id: str) -> bool: ...
    def list_group_assets(self, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]: ...
    def get_group_asset(self, chat_id: str) -> dict[str, Any] | None: ...
    def upsert_group_asset(self, snapshot: dict[str, Any]) -> tuple[dict[str, Any], str]: ...
    def upsert_group_snapshots(self, groups: list[dict[str, Any]]) -> int: ...
    def mark_owner_group_assets_inactive_except(self, owner_userid: str, active_chat_ids: list[str]) -> list[str]: ...
    def list_admin_group_assets(self, owner_userid: str) -> list[dict[str, Any]]: ...
    def list_admin_candidate_group_assets(self, owner_userid: str, *, limit: int = 100) -> list[dict[str, Any]]: ...
    def list_owners(self) -> list[dict[str, Any]]: ...
    def list_nodes(self, plan_id: int) -> list[dict[str, Any]]: ...
    def create_node(self, plan_id: int, payload: dict[str, Any]) -> dict[str, Any]: ...
    def update_node(self, plan_id: int, node_id: int, payload: dict[str, Any]) -> dict[str, Any]: ...
    def delete_node(self, plan_id: int, node_id: int) -> bool: ...
    def list_plan_members(self, plan_id: int, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]: ...
    def upsert_plan_members(self, plan_id: int, members: list[dict[str, Any]], *, source_type: str, source_ref_id: str = "") -> int: ...
    def get_segmentation(self, plan_id: int) -> dict[str, Any] | None: ...
    def save_segmentation(self, plan_id: int, payload: dict[str, Any]) -> dict[str, Any]: ...
    def list_audience_rules(self, filters: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], int]: ...
    def create_audience_rule(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def get_audience_rule(self, rule_key: str) -> dict[str, Any] | None: ...
    def create_audience_rule_version(self, rule_key: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def get_audience_rule_version(self, rule_key: str, version: int) -> dict[str, Any] | None: ...
    def replace_audience_rule_results(self, rule_key: str, version: int, plan_id: int, results: list[dict[str, Any]]) -> int: ...
    def list_audience_rule_results(
        self, rule_key: str, version: int, plan_id: int, filters: dict[str, Any] | None = None
    ) -> tuple[list[dict[str, Any]], int]: ...
    def create_trigger_event(self, plan_id: int, payload: dict[str, Any]) -> dict[str, Any]: ...
    def find_trigger_event(self, plan_id: int, idempotency_key: str) -> dict[str, Any] | None: ...
    def update_trigger_event(self, event_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def create_execution_log(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def list_execution_logs(self, plan_id: int, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]: ...
    def find_webhook_event(self, plan_id: int, idempotency_key: str) -> dict[str, Any] | None: ...
    def create_webhook_event(self, plan_id: int, payload: dict[str, Any]) -> dict[str, Any]: ...
    def update_webhook_event(self, event_id: int, payload: dict[str, Any]) -> dict[str, Any]: ...


def _fixture_groups() -> dict[str, dict[str, Any]]:
    return {
        "wrOgAAA001": {
            "chat_id": "wrOgAAA001",
            "group_name": "体验课 01 群",
            "owner_userid": "owner_001",
            "owner_name": "王小明",
            "admin_userids": [],
            "internal_member_count": 12,
            "external_member_count": 150,
            "synced_at": "2026-05-25T08:00:00Z",
            "status": "active",
        },
        "wrOgAAA002": {
            "chat_id": "wrOgAAA002",
            "group_name": "体验课 02 群",
            "owner_userid": "owner_001",
            "owner_name": "王小明",
            "admin_userids": [],
            "internal_member_count": 10,
            "external_member_count": 160,
            "synced_at": "2026-05-25T08:00:00Z",
            "status": "active",
        },
        "wrOgAAA003": {
            "chat_id": "wrOgAAA003",
            "group_name": "体验课 03 群",
            "owner_userid": "owner_001",
            "owner_name": "王小明",
            "admin_userids": [],
            "internal_member_count": 9,
            "external_member_count": 176,
            "synced_at": "2026-05-25T08:00:00Z",
            "status": "active",
        },
        "wrOgBBB001": {
            "chat_id": "wrOgBBB001",
            "group_name": "成交陪跑 01 群",
            "owner_userid": "owner_002",
            "owner_name": "李小红",
            "admin_userids": ["admin_001"],
            "internal_member_count": 8,
            "external_member_count": 88,
            "synced_at": "2026-05-25T08:00:00Z",
            "status": "active",
        },
    }


class InMemoryGroupOpsRepository:
    source_status = "fixture_local_contract"

    def __init__(self, *, seed_groups: bool = True) -> None:
        now = utc_now_iso()
        self._plans: dict[int, dict[str, Any]] = {
            1: {
                "id": 1,
                "plan_code": "group_plan_001",
                "plan_name": "体验课 7 日群运营",
                "plan_type": "standard",
                "owner_userid": "owner_001",
                "owner_name": "王小明",
                "status": "active",
                "default_action_type": "record_only",
                "allow_no_sop": True,
                "allow_external_recipients": True,
                "description": "",
                "webhook_key": "",
                "created_by": "fixture",
                "updated_by": "fixture",
                "created_at": now,
                "updated_at": now,
                "archived_at": "",
            },
            2: {
                "id": 2,
                "plan_code": "group_webhook_001",
                "plan_name": "每日课程 Webhook 群运营",
                "plan_type": "webhook",
                "owner_userid": "owner_001",
                "owner_name": "王小明",
                "status": "active",
                "default_action_type": "enqueue",
                "allow_no_sop": True,
                "allow_external_recipients": True,
                "description": "Fixture webhook plan",
                "webhook_key": "daily-lesson-8f3a",
                "created_by": "fixture",
                "updated_by": "fixture",
                "created_at": now,
                "updated_at": now,
                "archived_at": "",
            },
            3: {
                "id": 3,
                "plan_code": "group_plan_002",
                "plan_name": "成交陪跑 3 日群运营",
                "plan_type": "standard",
                "owner_userid": "owner_002",
                "owner_name": "李小红",
                "status": "draft",
                "default_action_type": "record_only",
                "allow_no_sop": True,
                "allow_external_recipients": True,
                "description": "",
                "webhook_key": "",
                "created_by": "fixture",
                "updated_by": "fixture",
                "created_at": now,
                "updated_at": now,
                "archived_at": "",
            },
        }
        self._groups = _fixture_groups() if seed_groups else {}
        self._next_plan_group_id = 1
        self._plan_groups: dict[int, dict[str, dict[str, Any]]] = {1: {}, 2: {}}
        if seed_groups:
            self._plan_groups = {
                1: {
                    "wrOgAAA001": self._snapshot_group(1, self._groups["wrOgAAA001"]),
                    "wrOgAAA002": self._snapshot_group(1, self._groups["wrOgAAA002"]),
                },
                2: {
                    "wrOgAAA001": self._snapshot_group(2, self._groups["wrOgAAA001"]),
                },
            }
        self._nodes: dict[int, dict[int, dict[str, Any]]] = {
            1: {
                1: {
                    "id": 1,
                    "plan_id": 1,
                    "day_index": 1,
                    "scheduled_time": "20:00",
                    "trigger_time_label": "20:00",
                    "action_title": "欢迎语 + 课程入口",
                    "text_content": "欢迎加入体验课群。",
                    "content_package_json": {
                        "content_text": "欢迎加入体验课群。",
                        "image_library_ids": [],
                        "miniprogram_library_ids": [],
                        "attachment_library_ids": [],
                        "group_invite_library_ids": [],
                    },
                    "attachments": [],
                    "sort_order": 10,
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                }
            }
        }
        self._webhook_events: dict[int, dict[str, Any]] = {}
        self._plan_scopes: dict[int, list[dict[str, Any]]] = {}
        self._plan_members: dict[int, dict[str, dict[str, Any]]] = {}
        self._segmentations: dict[int, dict[str, Any]] = {}
        self._audience_rules: dict[str, dict[str, Any]] = {}
        self._audience_rule_versions: dict[tuple[str, int], dict[str, Any]] = {}
        self._audience_rule_results: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
        self._trigger_events: dict[str, dict[str, Any]] = {}
        self._execution_logs: dict[int, dict[str, Any]] = {}
        self._next_plan_id = 4
        self._next_node_id = 2
        self._next_event_id = 1
        self._next_scope_id = 1
        self._next_member_id = 1
        self._next_rule_id = 1
        self._next_rule_version_id = 1
        self._next_rule_result_id = 1
        self._next_execution_id = 1
        self._seed_builtin_rules()

    def _snapshot_group(self, plan_id: int, group: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        row = {
            "id": self._next_plan_group_id,
            "plan_id": int(plan_id),
            "chat_id": group["chat_id"],
            "group_name_snapshot": group["group_name"],
            "owner_userid_snapshot": group["owner_userid"],
            "internal_member_count_snapshot": int(group.get("internal_member_count") or 0),
            "external_member_count_snapshot": int(group.get("external_member_count") or 0),
            "status": "active",
            "created_at": now,
            "removed_at": "",
        }
        self._next_plan_group_id += 1
        return row

    def _seed_builtin_rules(self) -> None:
        self.create_audience_rule(
            {
                "rule_key": "has_used_core_feature",
                "display_name": "是否使用核心功能",
                "description": "判断用户在指定时间窗口内是否使用过 AI-CRM 核心功能",
                "rule_type": "module",
                "owner": "growth_platform",
                "status": "active",
            }
        )
        self.create_audience_rule_version(
            "has_used_core_feature",
            {
                "version": 1,
                "executor_type": "module",
                "code_or_sql": "builtin:has_used_core_feature",
                "params_schema": {
                    "lookback_days": {"type": "integer", "default": 30, "label": "观察窗口"},
                    "feature_codes": {
                        "type": "array",
                        "default": ["crm_task_publish", "group_activation", "ai_followup"],
                        "label": "核心功能列表",
                    },
                    "min_usage_count": {"type": "integer", "default": 1, "label": "最小使用次数"},
                    "high_intent_chat_count": {"type": "integer", "default": 3, "label": "高意向聊天次数"},
                },
                "output_schema": {
                    "user_id": "string",
                    "external_user_id": "string",
                    "layer_key": "string",
                    "score": "number",
                    "reason": "string",
                    "computed_at": "datetime",
                    "rule_version": "integer",
                },
                "refresh_policy": {"mode": "manual_or_cron", "cron": "0 */2 * * *", "timezone": "Asia/Shanghai"},
                "status": "active",
            },
        )

    def list_plans(self, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        keyword = clean_text(filters.get("keyword")).lower()
        plan_type = clean_text(filters.get("plan_type")).lower()
        operator_member_id = clean_text(filters.get("operator_member_id") or filters.get("operatorMemberId"))
        status = clean_text(filters.get("status")).lower()
        rows = []
        for plan in self._plans.values():
            if plan.get("archived_at"):
                continue
            haystack = f"{plan.get('plan_name')} {plan.get('plan_code')} {plan.get('owner_userid')}".lower()
            if keyword and keyword not in haystack:
                continue
            if plan_type and plan.get("plan_type") != plan_type:
                continue
            if operator_member_id and plan.get("owner_userid") != operator_member_id:
                continue
            if status and plan.get("status") != status:
                continue
            rows.append(deepcopy(plan))
        rows.sort(key=lambda item: int(item["id"]))
        offset = max(0, int(filters.get("offset") or 0))
        limit = max(1, int(filters.get("limit") or 50))
        return rows[offset : offset + limit], len(rows)

    def get_plan(self, plan_id: int) -> dict[str, Any] | None:
        plan = self._plans.get(int(plan_id))
        return deepcopy(plan) if plan and not plan.get("archived_at") else None

    def get_plan_by_webhook_key(self, webhook_key: str) -> dict[str, Any] | None:
        key = clean_text(webhook_key)
        for plan in self._plans.values():
            if plan.get("webhook_key") == key and not plan.get("archived_at"):
                return deepcopy(plan)
        return None

    def create_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_plan_payload(payload)
        plan_id = self._next_plan_id
        self._next_plan_id += 1
        now = utc_now_iso()
        plan_code = normalized["plan_code"] or f"group_plan_{plan_id:03d}"
        webhook_key = ""
        if normalized["plan_type"] == "webhook":
            webhook_key = generate_webhook_key(normalized["plan_name"])
        row = {
            "id": plan_id,
            **normalized,
            "plan_code": plan_code,
            "owner_name": "",
            "webhook_key": webhook_key,
            "created_at": now,
            "updated_at": now,
            "archived_at": "",
        }
        self._plans[plan_id] = row
        self._plan_groups.setdefault(plan_id, {})
        self._nodes.setdefault(plan_id, {})
        self._replace_payload_scopes(plan_id, payload)
        return deepcopy(row)

    def update_plan(self, plan_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        current = self._plans.get(int(plan_id))
        if not current:
            raise NotFoundError("group ops plan not found")
        normalized = normalize_plan_payload(payload, existing=current)
        current.update(normalized)
        current["plan_code"] = normalized["plan_code"] or current["plan_code"]
        current["updated_at"] = utc_now_iso()
        self._replace_payload_scopes(int(plan_id), payload)
        return deepcopy(current)

    def archive_plan(self, plan_id: int, *, operator: str = "system") -> dict[str, Any]:
        current = self._plans.get(int(plan_id))
        if not current:
            raise NotFoundError("group ops plan not found")
        current["status"] = "disabled"
        current["updated_by"] = clean_text(operator) or "system"
        current["updated_at"] = utc_now_iso()
        current["archived_at"] = utc_now_iso()
        return deepcopy(current)

    def _replace_payload_scopes(self, plan_id: int, payload: dict[str, Any]) -> None:
        group_ids = normalize_scope_ids(payload, "boundGroupIds", "bound_group_ids")
        audience_ids = normalize_scope_ids(payload, "boundAudienceIds", "bound_audience_ids")
        if "boundGroupIds" in payload or "bound_group_ids" in payload:
            self.replace_plan_scopes(plan_id, scope_type="group", scope_ref_ids=group_ids)
        if "boundAudienceIds" in payload or "bound_audience_ids" in payload:
            self.replace_plan_scopes(plan_id, scope_type="audience", scope_ref_ids=audience_ids)

    def replace_plan_scopes(self, plan_id: int, *, scope_type: str, scope_ref_ids: list[str]) -> list[dict[str, Any]]:
        existing = [item for item in self._plan_scopes.get(int(plan_id), []) if item.get("scope_type") != clean_text(scope_type)]
        for ref_id in scope_ref_ids:
            row = {
                "id": self._next_scope_id,
                "plan_id": int(plan_id),
                "scope_type": clean_text(scope_type),
                "scope_ref_id": clean_text(ref_id),
                "created_at": utc_now_iso(),
            }
            self._next_scope_id += 1
            existing.append(row)
        self._plan_scopes[int(plan_id)] = existing
        return self.list_plan_scopes(int(plan_id), scope_type=scope_type)

    def list_plan_scopes(self, plan_id: int, scope_type: str = "") -> list[dict[str, Any]]:
        rows = [deepcopy(item) for item in self._plan_scopes.get(int(plan_id), [])]
        normalized_scope = clean_text(scope_type)
        if normalized_scope:
            rows = [item for item in rows if item.get("scope_type") == normalized_scope]
        return rows

    def list_bound_groups(self, plan_id: int) -> list[dict[str, Any]]:
        rows = list(self._plan_groups.get(int(plan_id), {}).values())
        return [deepcopy(item) for item in rows if item.get("status") == "active"]

    def bind_group(self, plan_id: int, group: dict[str, Any]) -> dict[str, Any]:
        self._plan_groups.setdefault(int(plan_id), {})
        existing = self._plan_groups[int(plan_id)].get(group["chat_id"])
        if existing:
            existing["status"] = "active"
            existing["removed_at"] = ""
            if int(plan_id) in self._plans:
                self._plans[int(plan_id)]["updated_at"] = utc_now_iso()
            return deepcopy(existing)
        row = self._snapshot_group(int(plan_id), group)
        self._plan_groups[int(plan_id)][group["chat_id"]] = row
        if int(plan_id) in self._plans:
            self._plans[int(plan_id)]["updated_at"] = utc_now_iso()
        return deepcopy(row)

    def remove_group(self, plan_id: int, chat_id: str) -> bool:
        item = self._plan_groups.get(int(plan_id), {}).get(clean_text(chat_id))
        if not item or item.get("status") != "active":
            return False
        item["status"] = "removed"
        item["removed_at"] = utc_now_iso()
        if int(plan_id) in self._plans:
            self._plans[int(plan_id)]["updated_at"] = utc_now_iso()
        return True

    def list_group_assets(self, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        keyword = clean_text(filters.get("keyword")).lower()
        owner_userid = clean_text(filters.get("owner_userid"))
        plan_id = int(filters.get("plan_id") or 0)
        bind_status = clean_text(filters.get("bind_status")).lower()
        rows = []
        for group in self._groups.values():
            if clean_text(group.get("status")) != "active":
                continue
            if keyword and keyword not in f"{group.get('group_name')} {group.get('chat_id')}".lower():
                continue
            if owner_userid and not group_manageable_by_userid(group, owner_userid):
                continue
            bound_plan = self._bound_plan_for_group(group["chat_id"], plan_id=plan_id)
            is_bound = bool(bound_plan)
            if bind_status == "bound" and not is_bound:
                continue
            if bind_status == "unbound" and is_bound:
                continue
            row = deepcopy(group)
            row["bound_plan_id"] = int(bound_plan.get("id") or 0) if bound_plan else 0
            row["plan_name"] = clean_text(bound_plan.get("plan_name")) if bound_plan else ""
            row["bind_status"] = "bound" if is_bound else "unbound"
            rows.append(row)
        offset = max(0, int(filters.get("offset") or 0))
        limit = max(1, int(filters.get("limit") or 50))
        return rows[offset : offset + limit], len(rows)

    def _bound_plan_for_group(self, chat_id: str, *, plan_id: int = 0) -> dict[str, Any] | None:
        for current_plan_id, groups in self._plan_groups.items():
            if plan_id and current_plan_id != plan_id:
                continue
            binding = groups.get(chat_id)
            if binding and binding.get("status") == "active":
                return self._plans.get(current_plan_id)
        return None

    def get_group_asset(self, chat_id: str) -> dict[str, Any] | None:
        group = self._groups.get(clean_text(chat_id))
        return deepcopy(group) if group else None

    def upsert_group_snapshots(self, groups: list[dict[str, Any]]) -> int:
        count = 0
        for group in groups:
            if not clean_text((group or {}).get("chat_id")):
                continue
            _saved, action = self.upsert_group_asset(group)
            if action in {"created", "updated"}:
                count += 1
        return count

    def upsert_group_asset(self, snapshot: dict[str, Any]) -> tuple[dict[str, Any], str]:
        chat_id = clean_text(snapshot.get("chat_id"))
        if not chat_id:
            raise ContractError("chat_id is required")
        action = "updated" if chat_id in self._groups else "created"
        self._groups[chat_id] = {
            "chat_id": chat_id,
            "group_name": clean_text(snapshot.get("group_name") or chat_id),
            "owner_userid": clean_text(snapshot.get("owner_userid")),
            "owner_name": clean_text(snapshot.get("owner_name") or snapshot.get("owner_userid")),
            "admin_userids": normalize_group_admin_userids(snapshot.get("admin_userids") or snapshot.get("admin_list")),
            "internal_member_count": int(snapshot.get("internal_member_count") or 0),
            "external_member_count": int(snapshot.get("external_member_count") or 0),
            "synced_at": utc_now_iso(),
            "status": clean_text(snapshot.get("status") or "active"),
        }
        return deepcopy(self._groups[chat_id]), action

    def mark_owner_group_assets_inactive_except(self, owner_userid: str, active_chat_ids: list[str]) -> list[str]:
        owner = clean_text(owner_userid)
        active = {clean_text(chat_id) for chat_id in active_chat_ids if clean_text(chat_id)}
        changed: list[str] = []
        for group in self._groups.values():
            if clean_text(group.get("owner_userid")) != owner or clean_text(group.get("status")) != "active":
                continue
            if clean_text(group.get("chat_id")) in active:
                continue
            group["status"] = "inactive"
            group["synced_at"] = utc_now_iso()
            changed.append(clean_text(group.get("chat_id")))
        return changed

    def list_admin_group_assets(self, owner_userid: str) -> list[dict[str, Any]]:
        owner = clean_text(owner_userid)
        if not owner:
            return []
        return [
            deepcopy(group) for group in self._groups.values() if group_manageable_by_userid(group, owner) and clean_text(group.get("owner_userid")) != owner
        ]

    def list_admin_candidate_group_assets(self, owner_userid: str, *, limit: int = 100) -> list[dict[str, Any]]:
        owner = clean_text(owner_userid)
        if not owner:
            return []
        max_items = clamp_limit(limit, default=100)
        candidates = [deepcopy(group) for group in self._groups.values() if clean_text(group.get("owner_userid")) != owner]
        return candidates[:max_items]

    def list_owners(self) -> list[dict[str, Any]]:
        owners: dict[str, dict[str, Any]] = {}
        for group in self._groups.values():
            userid = clean_text(group.get("owner_userid"))
            if not userid:
                continue
            current = owners.setdefault(userid, {"userid": userid, "name": clean_text(group.get("owner_name")) or userid, "group_count": 0})
            current["group_count"] += 1
            if group.get("owner_name"):
                current["name"] = clean_text(group.get("owner_name"))
            for admin_userid in normalize_group_admin_userids(group.get("admin_userids")):
                owners.setdefault(admin_userid, {"userid": admin_userid, "name": admin_userid, "group_count": 0})
        for plan in self._plans.values():
            userid = clean_text(plan.get("owner_userid"))
            if userid and userid not in owners:
                owners[userid] = {"userid": userid, "name": clean_text(plan.get("owner_name")) or userid, "group_count": 0}
        return [deepcopy(item) for item in sorted(owners.values(), key=lambda item: item["userid"])]

    def list_nodes(self, plan_id: int) -> list[dict[str, Any]]:
        rows = list(self._nodes.get(int(plan_id), {}).values())
        rows = [item for item in rows if item.get("status") != "deleted"]
        rows.sort(key=lambda item: (int(item.get("day_index") or 0), int(item.get("sort_order") or 0), int(item["id"])))
        normalized_rows = []
        for row in deepcopy(rows):
            row["scheduled_time"] = derive_node_scheduled_time(row) or "20:00"
            normalized_rows.append(row)
        return normalized_rows

    def create_node(self, plan_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        node_id = self._next_node_id
        self._next_node_id += 1
        row = {
            "id": node_id,
            "plan_id": int(plan_id),
            **payload,
            "created_at": now,
            "updated_at": now,
        }
        self._nodes.setdefault(int(plan_id), {})[node_id] = row
        return deepcopy(row)

    def update_node(self, plan_id: int, node_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        current = self._nodes.get(int(plan_id), {}).get(int(node_id))
        if not current:
            raise NotFoundError("group ops node not found")
        current.update(payload)
        current["updated_at"] = utc_now_iso()
        return deepcopy(current)

    def delete_node(self, plan_id: int, node_id: int) -> bool:
        current = self._nodes.get(int(plan_id), {}).get(int(node_id))
        if not current:
            return False
        current["status"] = "deleted"
        current["updated_at"] = utc_now_iso()
        return True

    def find_webhook_event(self, plan_id: int, idempotency_key: str) -> dict[str, Any] | None:
        key = clean_text(idempotency_key)
        for event in self._webhook_events.values():
            if int(event.get("plan_id") or 0) == int(plan_id) and event.get("idempotency_key") == key:
                return deepcopy(event)
        return None

    def create_webhook_event(self, plan_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = self._next_event_id
        self._next_event_id += 1
        row = {
            "id": event_id,
            "plan_id": int(plan_id),
            "idempotency_key": clean_text(payload.get("idempotency_key")),
            "request_payload": deepcopy(payload.get("request_payload") or {}),
            "normalized_content_payload": deepcopy(payload.get("normalized_content_payload") or {}),
            "scheduled_at": clean_text(payload.get("scheduled_at")),
            "status": clean_text(payload.get("status") or "accepted"),
            "broadcast_job_ids": list(payload.get("broadcast_job_ids") or []),
            "error_message": clean_text(payload.get("error_message")),
            "created_at": utc_now_iso(),
        }
        self._webhook_events[event_id] = row
        return deepcopy(row)

    def update_webhook_event(self, event_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        current = self._webhook_events.get(int(event_id))
        if not current:
            raise NotFoundError("group ops webhook event not found")
        current.update(deepcopy(payload))
        return deepcopy(current)

    def list_plan_members(self, plan_id: int, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        rows = list(self._plan_members.get(int(plan_id), {}).values())
        layer_key = clean_text(filters.get("layer_key") or filters.get("layerKey"))
        source_type = clean_text(filters.get("source_type") or filters.get("sourceType"))
        keyword = clean_text(filters.get("keyword")).lower()
        if layer_key:
            rows = [item for item in rows if item.get("layer_key") == layer_key]
        if source_type:
            rows = [item for item in rows if item.get("source_type") == source_type]
        if keyword:
            rows = [item for item in rows if keyword in f"{item.get('user_id')} {item.get('external_user_id')} {item.get('group_id')}".lower()]
        rows.sort(key=lambda item: int(item["id"]))
        offset = max(0, int(filters.get("offset") or 0))
        limit = max(1, int(filters.get("limit") or 50))
        return [deepcopy(item) for item in rows[offset : offset + limit]], len(rows)

    def upsert_plan_members(self, plan_id: int, members: list[dict[str, Any]], *, source_type: str, source_ref_id: str = "") -> int:
        bucket = self._plan_members.setdefault(int(plan_id), {})
        count = 0
        for member in members:
            external_user_id = clean_text(member.get("external_user_id") or member.get("external_userid") or member.get("externalUserId"))
            user_id = clean_text(member.get("user_id") or member.get("userId"))
            group_id = clean_text(member.get("group_id") or member.get("groupId") or member.get("chat_id"))
            key = external_user_id or user_id or group_id
            if not key:
                continue
            current = bucket.get(key)
            if not current:
                current = {
                    "id": self._next_member_id,
                    "plan_id": int(plan_id),
                    "joined_at": utc_now_iso(),
                }
                self._next_member_id += 1
                bucket[key] = current
            current.update(
                {
                    "user_id": user_id,
                    "external_user_id": external_user_id,
                    "group_id": group_id,
                    "layer_key": clean_text(member.get("layer_key") or member.get("layerKey")),
                    "source_type": clean_text(source_type),
                    "source_ref_id": clean_text(source_ref_id or member.get("source_ref_id") or member.get("sourceRefId")),
                    "status": clean_text(member.get("status") or "active"),
                    "updated_at": utc_now_iso(),
                }
            )
            count += 1
        return count

    def get_segmentation(self, plan_id: int) -> dict[str, Any] | None:
        value = self._segmentations.get(int(plan_id))
        return deepcopy(value) if value else None

    def save_segmentation(self, plan_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "plan_id": int(plan_id),
            "segmentation_type": clean_text(payload.get("segmentation_type") or payload.get("segmentationType") or "preset_rule"),
            "rule_key": clean_text(payload.get("rule_key") or payload.get("ruleKey")),
            "rule_version": int(payload.get("rule_version") or payload.get("ruleVersion") or 0),
            "params": deepcopy(payload.get("params") or {}),
            "layer_actions": deepcopy(payload.get("layer_actions") or payload.get("layerActions") or {}),
            "updated_at": utc_now_iso(),
        }
        self._segmentations[int(plan_id)] = row
        return deepcopy(row)

    def list_audience_rules(self, filters: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], int]:
        filters = filters or {}
        status = clean_text(filters.get("status"))
        rows = [deepcopy(item) for item in self._audience_rules.values()]
        if status:
            rows = [item for item in rows if item.get("status") == status]
        rows.sort(key=lambda item: clean_text(item.get("rule_key")))
        return rows, len(rows)

    def create_audience_rule(self, payload: dict[str, Any]) -> dict[str, Any]:
        rule_key = clean_text(payload.get("rule_key") or payload.get("ruleKey"))
        if not rule_key:
            raise ContractError("rule_key is required")
        existing = self._audience_rules.get(rule_key)
        if existing:
            existing.update(
                {
                    "display_name": clean_text(payload.get("display_name") or payload.get("displayName") or existing.get("display_name")),
                    "description": clean_text(payload.get("description") or existing.get("description")),
                    "rule_type": clean_text(payload.get("rule_type") or payload.get("ruleType") or existing.get("rule_type")),
                    "owner": clean_text(payload.get("owner") or existing.get("owner")),
                    "status": clean_text(payload.get("status") or existing.get("status") or "active"),
                    "updated_at": utc_now_iso(),
                }
            )
            return deepcopy(existing)
        row = {
            "id": self._next_rule_id,
            "rule_key": rule_key,
            "display_name": clean_text(payload.get("display_name") or payload.get("displayName") or rule_key),
            "description": clean_text(payload.get("description")),
            "rule_type": clean_text(payload.get("rule_type") or payload.get("ruleType") or "module"),
            "owner": clean_text(payload.get("owner") or "growth_platform"),
            "status": clean_text(payload.get("status") or "active"),
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        }
        self._next_rule_id += 1
        self._audience_rules[rule_key] = row
        return deepcopy(row)

    def get_audience_rule(self, rule_key: str) -> dict[str, Any] | None:
        row = self._audience_rules.get(clean_text(rule_key))
        return deepcopy(row) if row else None

    def create_audience_rule_version(self, rule_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        rule = self.get_audience_rule(rule_key)
        if not rule:
            raise NotFoundError("audience rule not found")
        version = int(payload.get("version") or 0)
        if version < 1:
            raise ContractError("version is required")
        key = (clean_text(rule_key), version)
        row = {
            "id": self._next_rule_version_id,
            "rule_id": int(rule["id"]),
            "rule_key": clean_text(rule_key),
            "version": version,
            "executor_type": clean_text(payload.get("executor_type") or payload.get("executorType") or "module"),
            "code_or_sql": clean_text(payload.get("code_or_sql") or payload.get("codeOrSql")),
            "params_schema": deepcopy(payload.get("params_schema") or payload.get("paramsSchema") or {}),
            "output_schema": deepcopy(payload.get("output_schema") or payload.get("outputSchema") or {}),
            "refresh_policy": deepcopy(payload.get("refresh_policy") or payload.get("refreshPolicy") or {}),
            "status": clean_text(payload.get("status") or "active"),
            "published_at": utc_now_iso(),
            "created_at": utc_now_iso(),
        }
        self._next_rule_version_id += 1
        self._audience_rule_versions[key] = row
        return deepcopy(row)

    def get_audience_rule_version(self, rule_key: str, version: int) -> dict[str, Any] | None:
        row = self._audience_rule_versions.get((clean_text(rule_key), int(version)))
        return deepcopy(row) if row else None

    def replace_audience_rule_results(self, rule_key: str, version: int, plan_id: int, results: list[dict[str, Any]]) -> int:
        rows: list[dict[str, Any]] = []
        for result in results:
            row = {
                "id": self._next_rule_result_id,
                "rule_key": clean_text(rule_key),
                "rule_version": int(version),
                "plan_id": int(plan_id),
                "user_id": clean_text(result.get("user_id") or result.get("userId")),
                "external_user_id": clean_text(result.get("external_user_id") or result.get("external_userid") or result.get("externalUserId")),
                "layer_key": clean_text(result.get("layer_key") or result.get("layerKey")),
                "score": float(result.get("score") or 0),
                "reason": clean_text(result.get("reason")),
                "evidence_json": deepcopy(result.get("evidence_json") or result.get("evidence") or {}),
                "computed_at": clean_text(result.get("computed_at") or utc_now_iso()),
            }
            self._next_rule_result_id += 1
            rows.append(row)
        self._audience_rule_results[(clean_text(rule_key), int(version), int(plan_id))] = rows
        return len(rows)

    def list_audience_rule_results(self, rule_key: str, version: int, plan_id: int, filters: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], int]:
        filters = filters or {}
        layers = set(filters.get("layers") or [])
        rows = [deepcopy(item) for item in self._audience_rule_results.get((clean_text(rule_key), int(version), int(plan_id)), [])]
        if layers:
            rows = [item for item in rows if item.get("layer_key") in layers]
        offset = max(0, int(filters.get("offset") or 0))
        limit = max(1, int(filters.get("limit") or 50))
        return rows[offset : offset + limit], len(rows)

    def create_trigger_event(self, plan_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = f"evt_{len(self._trigger_events) + 1:06d}"
        row = {
            "id": event_id,
            "plan_id": int(plan_id),
            "endpoint_key": clean_text(payload.get("endpoint_key")),
            "event_name": clean_text(payload.get("event_name") or payload.get("event")),
            "source": clean_text(payload.get("source")),
            "idempotency_key": clean_text(payload.get("idempotency_key")),
            "payload_json": mask_sensitive_payload(payload.get("payload_json") or payload.get("payload") or {}),
            "status": clean_text(payload.get("status") or "accepted"),
            "received_at": utc_now_iso(),
            "processed_at": "",
            "error_message": "",
        }
        self._trigger_events[event_id] = row
        return deepcopy(row)

    def find_trigger_event(self, plan_id: int, idempotency_key: str) -> dict[str, Any] | None:
        key = clean_text(idempotency_key)
        for row in self._trigger_events.values():
            if int(row.get("plan_id") or 0) == int(plan_id) and row.get("idempotency_key") == key:
                return deepcopy(row)
        return None

    def update_trigger_event(self, event_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        row = self._trigger_events.get(clean_text(event_id))
        if not row:
            raise NotFoundError("group ops trigger event not found")
        row.update({key: deepcopy(value) for key, value in payload.items()})
        if "status" in payload:
            row["processed_at"] = utc_now_iso()
        return deepcopy(row)

    def create_execution_log(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "id": self._next_execution_id,
            "trigger_event_id": clean_text(payload.get("trigger_event_id")),
            "plan_id": int(payload.get("plan_id") or 0),
            "event_name": clean_text(payload.get("event_name")),
            "recipient": deepcopy(payload.get("recipient") or {}),
            "sender": deepcopy(payload.get("sender") or {}),
            "user_id": clean_text(payload.get("user_id")),
            "external_user_id": clean_text(payload.get("external_user_id")),
            "layer_key": clean_text(payload.get("layer_key")),
            "action_type": clean_text(payload.get("action_type")),
            "action_ref_id": clean_text(payload.get("action_ref_id")),
            "status": clean_text(payload.get("status") or "success"),
            "error_message": clean_text(payload.get("error_message")),
            "idempotency_key": clean_text(payload.get("idempotency_key")),
            "received_at": clean_text(payload.get("received_at")),
            "processed_at": utc_now_iso(),
            "created_at": utc_now_iso(),
        }
        self._next_execution_id += 1
        self._execution_logs[int(row["id"])] = row
        return deepcopy(row)

    def list_execution_logs(self, plan_id: int, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        rows = [deepcopy(item) for item in self._execution_logs.values() if int(item.get("plan_id") or 0) == int(plan_id)]
        for key in ("trigger_event_id", "status", "action_type", "layer_key"):
            value = clean_text(filters.get(key))
            if value:
                rows = [item for item in rows if clean_text(item.get(key)) == value]
        recipient = clean_text(filters.get("recipient")).lower()
        if recipient:
            rows = [item for item in rows if recipient in f"{item.get('external_user_id')} {item.get('user_id')}".lower()]
        rows.sort(key=lambda item: int(item["id"]), reverse=True)
        offset = max(0, int(filters.get("offset") or 0))
        limit = max(1, int(filters.get("limit") or 50))
        return rows[offset : offset + limit], len(rows)


_fixture_repo = InMemoryGroupOpsRepository()


def build_group_ops_repository() -> GroupOpsRepository:
    backend = clean_text(os.getenv(GROUP_OPS_BACKEND_ENV)).lower()
    if production_data_ready() or backend in GROUP_OPS_SQL_BACKENDS:
        database_url = clean_text(os.getenv(GROUP_OPS_DATABASE_URL_ENV)) or raw_database_url()
        if not database_url:
            raise ContractError(f"{GROUP_OPS_DATABASE_URL_ENV} or DATABASE_URL is required for group ops Postgres repository")
        from .postgres_repo import PostgresGroupOpsRepository

        return assert_repository_allowed(
            PostgresGroupOpsRepository(get_engine(database_url)),
            capability_owner="aicrm_next.automation_engine.group_ops",
        )
    return assert_repository_allowed(_fixture_repo, capability_owner="aicrm_next.automation_engine.group_ops")


def _sqlalchemy_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def reset_group_ops_fixture_state(*, seed_groups: bool = True) -> None:
    global _fixture_repo
    _fixture_repo = InMemoryGroupOpsRepository(seed_groups=seed_groups)


def plan_binding_summary(repo: GroupOpsRepository, plan_id: int) -> dict[str, int]:
    return binding_stats(repo.list_bound_groups(int(plan_id)))
