from __future__ import annotations

from typing import Any

from aicrm_next.platform.shared.repository_provider import RepositoryProviderError

from .domain import binding_stats, clean_text, normalize_group_admin_userids


def plan_list_item(plan: dict[str, Any], *, groups: list[dict[str, Any]], owner_name: str = "") -> dict[str, Any]:
    stats = binding_stats(groups)
    return {
        "id": int(plan["id"]),
        "plan_code": clean_text(plan.get("plan_code")),
        "plan_name": clean_text(plan.get("plan_name")),
        "plan_type": clean_text(plan.get("plan_type")),
        "owner_userid": clean_text(plan.get("owner_userid")),
        "owner_name": owner_name or clean_text(plan.get("owner_name")),
        "bound_group_count": stats["bound_group_count"],
        "today_estimated_reach": stats["estimated_reach"],
        "status": clean_text(plan.get("status")),
    }


def group_asset_item(group: dict[str, Any], *, plan_name: str = "", bind_status: str = "unbound") -> dict[str, Any]:
    internal_member_count = int(group.get("internal_member_count") or 0)
    external_member_count = int(group.get("external_member_count") or 0)
    return {
        "chat_id": clean_text(group.get("chat_id")),
        "group_name": clean_text(group.get("group_name")),
        "owner_userid": clean_text(group.get("owner_userid")),
        "owner_name": clean_text(group.get("owner_name")),
        "admin_userids": normalize_group_admin_userids(group.get("admin_userids")),
        "plan_name": plan_name,
        "bind_status": bind_status,
        "internal_member_count": internal_member_count,
        "external_member_count": external_member_count,
        "member_count": internal_member_count + external_member_count,
        "synced_at": clean_text(group.get("synced_at")),
        "status": clean_text(group.get("status") or "active"),
    }


def plan_public_payload(repo: Any, plan: dict[str, Any]) -> dict[str, Any]:
    plan_id = int(plan["id"])
    scopes = _optional_detail([], lambda: repo.list_plan_scopes(plan_id)) if hasattr(repo, "list_plan_scopes") else []
    groups = repo.list_bound_groups(plan_id)
    segmentation = _optional_detail(None, lambda: repo.get_segmentation(plan_id)) if hasattr(repo, "get_segmentation") else None
    rule_stats = {"total": 0, "layers": []}
    if segmentation and segmentation.get("rule_key") and segmentation.get("rule_version"):
        rows, total = ([], 0)
        if hasattr(repo, "list_audience_rule_results"):
            rows, total = _optional_detail(
                ([], 0),
                lambda: repo.list_audience_rule_results(
                    clean_text(segmentation["rule_key"]),
                    int(segmentation["rule_version"]),
                    plan_id,
                    {"limit": 10000, "offset": 0},
                ),
            )
        counts: dict[str, int] = {}
        for row in rows:
            layer = clean_text(row.get("layer_key"))
            counts[layer] = counts.get(layer, 0) + 1
        rule_stats = {
            "total": total,
            "layers": [{"layerKey": key, "count": value} for key, value in sorted(counts.items())],
        }
    execution_rows, execution_total = (
        _optional_detail(([], 0), lambda: repo.list_execution_logs(plan_id, {"limit": 1, "offset": 0})) if hasattr(repo, "list_execution_logs") else ([], 0)
    )
    result_rows = []
    if segmentation and hasattr(repo, "list_audience_rule_results"):
        result_rows = _optional_detail(
            ([], 0),
            lambda: repo.list_audience_rule_results(
                clean_text(segmentation.get("rule_key")),
                int(segmentation.get("rule_version") or 0),
                plan_id,
                {"limit": 10000},
            ),
        )[0]
    webhook_key = clean_text(plan.get("webhook_key"))
    payload = {
        "planId": f"plan_{plan_id}",
        "id": plan_id,
        "name": clean_text(plan.get("plan_name")),
        "plan_name": clean_text(plan.get("plan_name")),
        "type": "webhook_receiver" if plan.get("plan_type") == "webhook" else "standard",
        "plan_type": clean_text(plan.get("plan_type")),
        "status": clean_text(plan.get("status")),
        "operatorMemberId": clean_text(plan.get("owner_userid")),
        "owner_userid": clean_text(plan.get("owner_userid")),
        "operator_member": {
            "userid": clean_text(plan.get("owner_userid")),
            "name": clean_text(plan.get("owner_name")),
        },
        "defaultActionType": clean_text(plan.get("default_action_type") or "record_only"),
        "allowNoSop": bool(plan.get("allow_no_sop", True)),
        "allowExternalRecipients": bool(plan.get("allow_external_recipients", True)),
        "description": clean_text(plan.get("description")),
        "boundGroups": groups,
        "boundGroupIds": [clean_text(item.get("chat_id") or item.get("scope_ref_id")) for item in groups]
        + [clean_text(item.get("scope_ref_id")) for item in scopes if item.get("scope_type") == "group"],
        "boundAudienceIds": [clean_text(item.get("scope_ref_id")) for item in scopes if item.get("scope_type") == "audience"],
        "webhook": {
            "endpointKey": webhook_key,
            "url": f"/api/automation/group-ops/webhooks/{webhook_key}" if webhook_key else "",
            "method": "POST",
            "authMode": "aicrm_hmac_sha256",
        },
        "segmentation": segmentation or {},
        "lastRefreshAt": max([clean_text(item.get("computed_at")) for item in result_rows] or [""]),
        "segmentationStats": rule_stats,
        "executionStats": {
            "total": execution_total,
            "lastStatus": clean_text(execution_rows[0].get("status")) if execution_rows else "",
        },
        "created_at": clean_text(plan.get("created_at")),
        "updated_at": clean_text(plan.get("updated_at")),
        "archived_at": clean_text(plan.get("archived_at")),
    }
    return payload


def _optional_detail(default: Any, getter: Any) -> Any:
    try:
        return getter()
    except RepositoryProviderError:
        return default
