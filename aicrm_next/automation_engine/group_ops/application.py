from __future__ import annotations

import os
from typing import Any

from aicrm_next.integration_gateway.wecom_group_contract import WeComGroupAssetAdapterContract
from aicrm_next.shared.admin_read_fallback import admin_read_unavailable_payload
from aicrm_next.shared.errors import ContractError, NotFoundError
from aicrm_next.shared.repository_provider import RepositoryProviderError, blocked_production_payload

from . import CAPABILITY_OWNER
from .domain import (
    assert_run_due_guard,
    build_node_group_message_content,
    binding_stats,
    clean_text,
    clamp_limit,
    group_manageable_by_userid,
    mask_sensitive_payload,
    normalize_action_payload,
    normalize_group_snapshots,
    normalize_plan_type,
    normalize_recipients,
)
from .dto import (
    AudienceRuleCreateRequest,
    AudienceRuleRunRequest,
    AudienceRuleVersionCreateRequest,
    GroupOpsBindGroupRequest,
    GroupOpsExecutionsRequest,
    GroupOpsGroupSyncRequest,
    GroupOpsGroupsRequest,
    GroupOpsMemberImportRequest,
    GroupOpsMembersRequest,
    GroupOpsNodeRequest,
    GroupOpsPlanCreateRequest,
    GroupOpsPlanListRequest,
    GroupOpsRunDueRequest,
    GroupOpsPlanUpdateRequest,
    GroupOpsSegmentationRequest,
    GroupOpsWebhookReceiveRequest,
)
from .external_effects import (
    group_ops_effect_action_type,
    parse_external_effect_scheduled_at,
    plan_group_ops_action_effect,
)
from .durable_effects_repository import (
    GroupOpsEffectGraphRepository,
    GroupOpsEffectGraphRequest,
    build_group_ops_effect_graph_repository,
)
from .legacy_bundle import receive_trusted_group_bundle
from . import plan_effect_lifecycle
from .projections import group_asset_item, plan_list_item, plan_public_payload
from .repo import GroupOpsRepository, build_group_ops_repository, plan_binding_summary
from .runtime_guards import ConflictError, assert_webhook_rate_limit


def group_ops_side_effect_safety(**overrides: bool) -> dict[str, bool]:
    safety = {
        "real_wecom_call_executed": False,
        "real_outbound_send_executed": False,
        "real_external_call_executed": False,
        "real_timer_executed": False,
        "real_queue_worker_created": False,
        "real_group_notice_executed": False,
        "real_mention_all_executed": False,
        "db_write_executed": False,
        "outbound_send_executed": False,
        "no_db_write": True,
        "no_outbound_send": True,
    }
    safety.update({key: bool(value) for key, value in overrides.items() if key in safety})
    return safety


def _response(payload: dict[str, Any], *, status_code: int = 200, repo: GroupOpsRepository | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "source_status": str(getattr(repo, "source_status", "fixture_local_contract")),
        "route_owner": "ai_crm_next",
        "capability_owner": CAPABILITY_OWNER,
        "status_code": status_code,
        "side_effect_safety": group_ops_side_effect_safety(),
        **payload,
    }


def _production_unavailable() -> dict[str, Any]:
    payload = blocked_production_payload(
        capability_owner=CAPABILITY_OWNER,
        detail="group ops production repository is not enabled; real WeCom group outbound remains disabled.",
    )
    payload.update(
        {
            "status_code": 503,
            "route_owner": "ai_crm_next",
            "side_effect_safety": group_ops_side_effect_safety(),
        }
    )
    return payload


def _read_production_unavailable(exc: Exception) -> dict[str, Any]:
    return admin_read_unavailable_payload(
        capability_owner=CAPABILITY_OWNER,
        page_error="群运营读模型暂不可用，请稍后重试。",
        exc=exc,
        items_keys=("items",),
        count_keys=("total",),
        extra={
            "queue_count": 0,
            "side_effect_safety": group_ops_side_effect_safety(),
            "capability_owner": CAPABILITY_OWNER,
        },
    )


def _repo_or_block(repo: GroupOpsRepository | None) -> GroupOpsRepository | None:
    return repo or build_group_ops_repository()


def _public_base_url() -> str:
    for key in ("AICRM_PUBLIC_BASE_URL", "PUBLIC_BASE_URL", "EXTERNAL_BASE_URL"):
        value = str(os.getenv(key, "") or "").strip().rstrip("/")
        if value:
            return value
    return "https://www.youcangogogo.com"


def _webhook_path(webhook_key: str) -> str:
    return f"/api/automation/group-ops/webhooks/{clean_text(webhook_key)}"


def _webhook_url(webhook_key: str) -> str:
    return f"{_public_base_url()}{_webhook_path(webhook_key)}"


def _coerce_plan_id(value: Any) -> int:
    text = clean_text(value)
    if text.startswith("plan_"):
        text = text.removeprefix("plan_")
    return int(text or 0)


def _queue_count() -> int:
    try:
        from aicrm_next.integration_gateway.wecom_group_adapter import build_group_ops_queue_stats_gateway

        return int(build_group_ops_queue_stats_gateway().count_group_ops_queue())
    except Exception:
        return 0


def _plan_or_404(repo: GroupOpsRepository, plan_id: int) -> dict[str, Any]:
    plan = repo.get_plan(int(plan_id))
    if not plan:
        raise NotFoundError("group ops plan not found")
    return plan


class ListGroupOpsPlansQuery:
    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(self, request: GroupOpsPlanListRequest) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        try:
            rows, total = repo.list_plans(
                {
                    "keyword": request.keyword,
                    "plan_type": normalize_plan_type(request.plan_type) if clean_text(request.plan_type) else "",
                    "operator_member_id": request.operator_member_id,
                    "status": request.status,
                    "limit": clamp_limit(request.limit),
                    "offset": max(0, int(request.offset or 0)),
                }
            )
            items = [plan_list_item(plan, groups=repo.list_bound_groups(int(plan["id"])), owner_name=clean_text(plan.get("owner_name"))) for plan in rows]
        except RepositoryProviderError as exc:
            return _read_production_unavailable(exc)
        return _response({"items": items, "total": total, "queue_count": _queue_count()}, repo=repo)


class ListGroupOpsOwnersQuery:
    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(self) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        owners = [
            {
                "userid": clean_text(item.get("userid")),
                "name": clean_text(item.get("name") or item.get("userid")),
                "group_count": int(item.get("group_count") or 0),
            }
            for item in repo.list_owners()
            if clean_text(item.get("userid"))
        ]
        return _response({"items": owners, "total": len(owners)}, repo=repo)


class CreateGroupOpsPlanCommand:
    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(self, request: GroupOpsPlanCreateRequest) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        plan = repo.create_plan(request.model_dump(exclude_none=True))
        return _response(
            {"item": plan, **plan_public_payload(repo, plan)},
            status_code=201,
            repo=repo,
        )


class GetGroupOpsPlanQuery:
    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(self, plan_id: int) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        plan = _plan_or_404(repo, plan_id)
        return _response(
            {
                "item": plan,
                "plan": plan_public_payload(repo, plan),
                "groups_summary": plan_binding_summary(repo, int(plan_id)),
                "nodes": repo.list_nodes(int(plan_id)),
            },
            repo=repo,
        )


class UpdateGroupOpsPlanCommand:
    def __init__(
        self,
        repo: GroupOpsRepository | None = None,
        effect_graph_repo: GroupOpsEffectGraphRepository | None = None,
    ) -> None:
        self._repo = repo
        self._effect_graph_repo = effect_graph_repo

    def __call__(self, plan_id: int, request: GroupOpsPlanUpdateRequest) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        return _response(
            plan_effect_lifecycle.update_plan(
                repo,
                self._effect_graph_repo,
                plan_id,
                request,
            ),
            repo=repo,
        )


class EnableGroupOpsPlanCommand:
    def __init__(
        self,
        repo: GroupOpsRepository | None = None,
        effect_graph_repo: GroupOpsEffectGraphRepository | None = None,
    ) -> None:
        self._repo = repo
        self._effect_graph_repo = effect_graph_repo

    def __call__(self, plan_id: int, *, operator: str = "system") -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        return _response(
            plan_effect_lifecycle.enable_plan(
                repo,
                self._effect_graph_repo,
                plan_id,
                operator=operator,
            ),
            repo=repo,
        )


class DisableGroupOpsPlanCommand:
    def __init__(
        self,
        repo: GroupOpsRepository | None = None,
        effect_graph_repo: GroupOpsEffectGraphRepository | None = None,
    ) -> None:
        self._repo = repo
        self._effect_graph_repo = effect_graph_repo

    def __call__(self, plan_id: int, *, operator: str = "system") -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        return _response(
            plan_effect_lifecycle.disable_plan(
                repo,
                self._effect_graph_repo,
                plan_id,
                operator=operator,
            ),
            repo=repo,
        )


class ArchiveGroupOpsPlanCommand:
    def __init__(
        self,
        repo: GroupOpsRepository | None = None,
        effect_graph_repo: GroupOpsEffectGraphRepository | None = None,
    ) -> None:
        self._repo = repo
        self._effect_graph_repo = effect_graph_repo

    def __call__(self, plan_id: int, *, operator: str = "system") -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        return _response(
            plan_effect_lifecycle.archive_plan(
                repo,
                self._effect_graph_repo,
                plan_id,
                operator=operator,
            ),
            repo=repo,
        )


class ListGroupOpsPlanGroupsQuery:
    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(self, plan_id: int) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        _plan_or_404(repo, plan_id)
        groups = repo.list_bound_groups(int(plan_id))
        return _response({"items": groups, "summary": binding_stats(groups), "total": len(groups)}, repo=repo)


class AddGroupOpsPlanGroupCommand:
    def __init__(
        self,
        repo: GroupOpsRepository | None = None,
        effect_graph_repo: GroupOpsEffectGraphRepository | None = None,
    ) -> None:
        self._repo = repo
        self._effect_graph_repo = effect_graph_repo

    def __call__(self, plan_id: int, request: GroupOpsBindGroupRequest) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        return _response(
            plan_effect_lifecycle.add_group(
                repo,
                self._effect_graph_repo,
                plan_id,
                request,
            ),
            status_code=201,
            repo=repo,
        )


class RemoveGroupOpsPlanGroupCommand:
    def __init__(
        self,
        repo: GroupOpsRepository | None = None,
        effect_graph_repo: GroupOpsEffectGraphRepository | None = None,
    ) -> None:
        self._repo = repo
        self._effect_graph_repo = effect_graph_repo

    def __call__(self, plan_id: int, chat_id: str) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        return _response(
            plan_effect_lifecycle.remove_group(
                repo,
                self._effect_graph_repo,
                plan_id,
                chat_id,
            ),
            repo=repo,
        )


class ListGroupOpsNodesQuery:
    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(self, plan_id: int) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        _plan_or_404(repo, plan_id)
        items = repo.list_nodes(int(plan_id))
        return _response({"items": items, "total": len(items)}, repo=repo)


class CreateGroupOpsNodeCommand:
    def __init__(
        self,
        repo: GroupOpsRepository | None = None,
        effect_graph_repo: GroupOpsEffectGraphRepository | None = None,
    ) -> None:
        self._repo = repo
        self._effect_graph_repo = effect_graph_repo

    def __call__(self, plan_id: int, request: GroupOpsNodeRequest) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        return _response(
            plan_effect_lifecycle.create_node(
                repo,
                self._effect_graph_repo,
                plan_id,
                request,
            ),
            status_code=201,
            repo=repo,
        )


class UpdateGroupOpsNodeCommand:
    def __init__(
        self,
        repo: GroupOpsRepository | None = None,
        effect_graph_repo: GroupOpsEffectGraphRepository | None = None,
    ) -> None:
        self._repo = repo
        self._effect_graph_repo = effect_graph_repo

    def __call__(self, plan_id: int, node_id: int, request: GroupOpsNodeRequest) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        return _response(
            plan_effect_lifecycle.update_node(
                repo,
                self._effect_graph_repo,
                plan_id,
                node_id,
                request,
            ),
            repo=repo,
        )


class DeleteGroupOpsNodeCommand:
    def __init__(
        self,
        repo: GroupOpsRepository | None = None,
        effect_graph_repo: GroupOpsEffectGraphRepository | None = None,
    ) -> None:
        self._repo = repo
        self._effect_graph_repo = effect_graph_repo

    def __call__(self, plan_id: int, node_id: int) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        return _response(
            plan_effect_lifecycle.delete_node(
                repo,
                self._effect_graph_repo,
                plan_id,
                node_id,
            ),
            repo=repo,
        )


class ListGroupOpsGroupsQuery:
    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(self, request: GroupOpsGroupsRequest) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        try:
            rows, total = repo.list_group_assets(
                {
                    "keyword": request.keyword,
                    "owner_userid": request.owner_userid,
                    "plan_id": request.plan_id or 0,
                    "bind_status": request.bind_status,
                    "limit": clamp_limit(request.limit),
                    "offset": max(0, int(request.offset or 0)),
                }
            )
            items = [
                group_asset_item(row, plan_name=clean_text(row.get("plan_name")), bind_status=clean_text(row.get("bind_status") or "unbound")) for row in rows
            ]
        except RepositoryProviderError as exc:
            return _read_production_unavailable(exc)
        return _response({"items": items, "total": total}, repo=repo)


def _group_sync_adapter() -> WeComGroupAssetAdapterContract:
    from aicrm_next.integration_gateway.wecom_group_adapter import build_wecom_group_asset_adapter

    return build_wecom_group_asset_adapter()


def _group_sync_blocked_response(
    *,
    owner_userid: str,
    result: dict[str, Any],
    repo: GroupOpsRepository,
) -> dict[str, Any]:
    mode = clean_text(result.get("mode"))
    error_code = clean_text(result.get("error_code") or "wecom_group_sync_blocked")
    status = "disabled" if mode in {"disabled", "staging"} or "disabled" in error_code else "blocked"
    return {
        "ok": False,
        "source_status": str(getattr(repo, "source_status", "fixture_local_contract")),
        "route_owner": "ai_crm_next",
        "capability_owner": CAPABILITY_OWNER,
        "status_code": 409,
        "owner_userid": owner_userid,
        "status": status,
        "sync_status": status,
        "adapter_mode": mode,
        "synced_count": 0,
        "new_count": 0,
        "updated_count": 0,
        "skipped_count": int(result.get("skipped_count") or 0),
        "next_cursor": "",
        "items": [],
        "warnings": [clean_text(result.get("error_message")) or "wecom group sync blocked"],
        "error_code": error_code,
        "error_message": clean_text(result.get("error_message")) or "wecom group sync blocked",
        "side_effect_safety": group_ops_side_effect_safety(),
    }


def _merge_group_sync_items(groups: list[dict[str, Any]], extra_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_chat_id: dict[str, dict[str, Any]] = {}
    for group in [*groups, *extra_groups]:
        chat_id = clean_text(group.get("chat_id"))
        if not chat_id:
            continue
        by_chat_id[chat_id] = group
    return list(by_chat_id.values())


def _refresh_admin_candidate_groups(
    *,
    repo: GroupOpsRepository,
    adapter: WeComGroupAssetAdapterContract,
    owner_userid: str,
    known_groups: list[dict[str, Any]],
    limit: int,
) -> tuple[list[dict[str, Any]], int, int, list[str]]:
    candidate_loader = getattr(repo, "list_admin_candidate_group_assets", None)
    if not callable(candidate_loader):
        return [], 0, 0, []
    known_chat_ids = {clean_text(group.get("chat_id")) for group in known_groups if clean_text(group.get("chat_id"))}
    refreshed_groups: list[dict[str, Any]] = []
    attempted_count = 0
    skipped_count = 0
    warnings: list[str] = []
    for candidate in candidate_loader(owner_userid, limit=limit):
        chat_id = clean_text(candidate.get("chat_id"))
        if not chat_id or chat_id in known_chat_ids:
            continue
        attempted_count += 1
        try:
            detail = adapter.get_group_chat(chat_id=chat_id, owner_userid=owner_userid)
        except TypeError:
            detail = adapter.get_group_chat(chat_id=chat_id)
        except Exception as exc:
            skipped_count += 1
            warnings.append(f"skipped_admin_candidate_refresh={chat_id}: {exc}")
            continue
        if not detail.get("ok") or not detail.get("group"):
            skipped_count += 1
            message = clean_text(detail.get("error_message")) or clean_text(detail.get("error_code")) or "not_found"
            warnings.append(f"skipped_admin_candidate_refresh={chat_id}: {message}")
            continue
        normalized = normalize_group_snapshots([dict(detail["group"])])
        if not normalized:
            skipped_count += 1
            warnings.append(f"skipped_admin_candidate_refresh={chat_id}: invalid group detail")
            continue
        group = normalized[0]
        if clean_text(group.get("owner_userid")) == owner_userid or group_manageable_by_userid(group, owner_userid):
            refreshed_groups.append(group)
            known_chat_ids.add(chat_id)
    return refreshed_groups, attempted_count, skipped_count, warnings


class PreviewGroupOpsOwnerGroupsSyncCommand:
    def __init__(self, repo: GroupOpsRepository | None = None, sync_adapter: WeComGroupAssetAdapterContract | None = None) -> None:
        self._repo = repo
        self._sync_adapter = sync_adapter

    def __call__(self, request: GroupOpsGroupSyncRequest) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        owner = clean_text(request.owner_userid)
        if not owner:
            raise ContractError("owner_userid is required")
        adapter = self._sync_adapter or _group_sync_adapter()
        sync_limit = clamp_limit(request.limit, default=100)
        cursor = clean_text(request.cursor)
        visited_cursors: set[str] = set()
        groups: list[dict[str, Any]] = []
        warnings: list[str] = []
        skipped_count = 0
        adapter_mode = ""
        for _page_number in range(100):
            if cursor in visited_cursors:
                warnings.append("stopped_repeated_wecom_cursor")
                break
            visited_cursors.add(cursor)
            result = adapter.list_group_chats(owner_userid=owner, limit=sync_limit, cursor=cursor)
            if not result.get("ok"):
                return _group_sync_blocked_response(owner_userid=owner, result=result, repo=repo)
            adapter_mode = adapter_mode or clean_text(result.get("mode"))
            groups = _merge_group_sync_items(groups, normalize_group_snapshots(list(result.get("groups") or [])))
            warnings.extend([clean_text(item) for item in list(result.get("warnings") or []) if clean_text(item)])
            skipped_count += int(result.get("skipped_count") or 0)
            next_cursor = clean_text(result.get("next_cursor"))
            if not next_cursor:
                cursor = ""
                break
            cursor = next_cursor
        else:
            warnings.append("stopped_after_max_wecom_pages=100")
        extra_groups = normalize_group_snapshots(repo.list_admin_group_assets(owner))
        refreshed_groups, refreshed_attempted_count, refreshed_skipped_count, refresh_warnings = _refresh_admin_candidate_groups(
            repo=repo,
            adapter=adapter,
            owner_userid=owner,
            known_groups=groups,
            limit=sync_limit,
        )
        # Local admin cache is only a fallback. Current WeCom list/detail data
        # must win when the same chat_id exists in both sources, otherwise a
        # stale cached name or owner makes a live group disappear from search.
        groups = _merge_group_sync_items(extra_groups, [*groups, *refreshed_groups])
        if extra_groups:
            warnings.append(f"included_admin_groups_from_local_cache={len(extra_groups)}")
        if refreshed_groups:
            warnings.append(f"included_admin_groups_from_refreshed_candidates={len(refreshed_groups)}")
        if refreshed_attempted_count:
            warnings.append(f"refreshed_admin_group_candidates={refreshed_attempted_count}")
        warnings.extend(refresh_warnings)
        return _response(
            {
                "owner_userid": owner,
                "status": "preview",
                "sync_status": "preview",
                "adapter_mode": adapter_mode,
                "items": groups,
                "total": len(groups),
                "synced_count": 0,
                "new_count": 0,
                "updated_count": 0,
                "skipped_count": skipped_count + refreshed_skipped_count,
                "next_cursor": cursor,
                "warnings": warnings,
                "side_effect_safety": group_ops_side_effect_safety(),
            },
            repo=repo,
        )


class SyncGroupOpsOwnerGroupsCommand(PreviewGroupOpsOwnerGroupsSyncCommand):
    def __call__(self, request: GroupOpsGroupSyncRequest) -> dict[str, Any]:
        preview = super().__call__(request)
        if preview.get("ok") is False:
            return preview
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        groups = list(preview.get("items") or [])
        saved_items: list[dict[str, Any]] = []
        new_count = 0
        updated_count = 0
        skipped_count = int(preview.get("skipped_count") or 0)
        warnings = list(preview.get("warnings") or [])
        for group in groups:
            try:
                saved, action = repo.upsert_group_asset(group)
            except Exception as exc:
                skipped_count += 1
                warnings.append(str(exc))
                continue
            saved_items.append(saved)
            if action == "created":
                new_count += 1
            elif action == "updated":
                updated_count += 1
        inactive_chat_ids: list[str] = []
        if not clean_text(request.cursor) and not clean_text(preview.get("next_cursor")):
            inactive_chat_ids = repo.mark_owner_group_assets_inactive_except(
                clean_text(preview.get("owner_userid") or request.owner_userid),
                [clean_text(group.get("chat_id")) for group in groups],
            )
            from aicrm_next.media_library.repo import build_media_library_repository

            build_media_library_repository().reconcile_group_invite_bindings(
                [clean_text(group.get("chat_id")) for group in groups],
                inactive_chat_ids,
            )
        return _response(
            {
                "owner_userid": clean_text(preview.get("owner_userid") or request.owner_userid),
                "status": "synced",
                "sync_status": "synced",
                "adapter_mode": clean_text(preview.get("adapter_mode")),
                "items": saved_items,
                "total": len(saved_items),
                "synced_count": len(saved_items),
                "new_count": new_count,
                "updated_count": updated_count,
                "inactive_count": len(inactive_chat_ids),
                "skipped_count": skipped_count,
                "next_cursor": clean_text(preview.get("next_cursor")),
                "warnings": warnings,
                "side_effect_safety": group_ops_side_effect_safety(
                    db_write_executed=bool(saved_items),
                    no_db_write=False,
                    no_outbound_send=True,
                ),
            },
            repo=repo,
        )


PreviewGroupOpsGroupsSyncCommand = PreviewGroupOpsOwnerGroupsSyncCommand
SyncGroupOpsGroupsCommand = SyncGroupOpsOwnerGroupsCommand


def _run_due_candidates(
    *,
    repo: GroupOpsRepository,
    plan: dict[str, Any],
    allow_plan_ids: list[int] | None = None,
    allow_node_ids: list[int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    if plan.get("plan_type") != "standard":
        raise ContractError("run-due is only available for standard group ops plans")
    if plan.get("status") != "active":
        raise ConflictError("group ops plan is not active")
    groups = repo.list_bound_groups(int(plan["id"]))
    if not groups:
        raise ConflictError("standard plan has no bound groups")
    nodes = [item for item in repo.list_nodes(int(plan["id"])) if clean_text(item.get("status") or "active") == "active"]
    allowed_plans = {int(item) for item in allow_plan_ids or []}
    allowed_nodes = {int(item) for item in allow_node_ids or []}
    if allowed_nodes and int(plan["id"]) not in allowed_plans:
        nodes = [item for item in nodes if int(item.get("id") or 0) in allowed_nodes]
    stats = binding_stats(groups)
    chat_ids = [clean_text(item.get("chat_id")) for item in groups if clean_text(item.get("chat_id"))]
    candidates: list[dict[str, Any]] = []
    for node in nodes:
        content = build_node_group_message_content(node=node, sender=clean_text(plan.get("owner_userid")))
        content_payload = dict(content)
        content_payload["channel"] = "wecom_customer_group"
        content_payload["sender"] = clean_text(plan.get("owner_userid"))
        content_payload["chat_ids"] = chat_ids
        candidates.append(
            {
                "plan_id": int(plan["id"]),
                "node_id": int(node["id"]),
                "day_index": int(node.get("day_index") or 0),
                "trigger_time_label": clean_text(node.get("trigger_time_label")),
                "action_title": clean_text(node.get("action_title")),
                "chat_ids": chat_ids,
                "group_count": len(chat_ids),
                "estimated_reach": int(stats["estimated_reach"]),
                "content_payload": content_payload,
                "content_summary": (content.get("text") or {}).get("content", "") or clean_text(node.get("action_title")),
            }
        )
    return candidates, groups, stats


class PreviewGroupOpsPlanRunDueCommand:
    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(self, plan_id: int, request: GroupOpsRunDueRequest) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        plan = _plan_or_404(repo, plan_id)
        candidates, groups, stats = _run_due_candidates(
            repo=repo,
            plan=plan,
            allow_plan_ids=request.allow_plan_ids,
            allow_node_ids=request.allow_node_ids,
        )
        if request.max_outbound_tasks:
            candidates = candidates[: max(0, int(request.max_outbound_tasks))]
        return _response(
            {
                "status": "preview",
                "plan_id": int(plan_id),
                "items": candidates,
                "groups": groups,
                "summary": stats,
                "total": len(candidates),
            },
            repo=repo,
        )


class RunGroupOpsPlanDueCommand:
    def __init__(
        self,
        repo: GroupOpsRepository | None = None,
        effect_graph_repo: GroupOpsEffectGraphRepository | None = None,
    ) -> None:
        self._repo = repo
        self._effect_graph_repo = effect_graph_repo

    def __call__(self, plan_id: int, request: GroupOpsRunDueRequest) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        plan = _plan_or_404(repo, plan_id)
        candidates, groups, stats = _run_due_candidates(
            repo=repo,
            plan=plan,
            allow_plan_ids=request.allow_plan_ids,
            allow_node_ids=request.allow_node_ids,
        )
        node_ids = [int(item["node_id"]) for item in candidates]
        assert_run_due_guard(
            plan_id=int(plan_id),
            node_ids=node_ids,
            operator=request.operator,
            allow_plan_ids=request.allow_plan_ids,
            allow_node_ids=request.allow_node_ids,
            max_outbound_tasks=request.max_outbound_tasks,
        )
        candidates = candidates[: int(request.max_outbound_tasks)]
        external_effect_job_ids: list[int] = []
        execution_ids: list[str] = []
        status_urls: list[str] = []
        parse_external_effect_scheduled_at(request.scheduled_at)
        outbound_mode = "external_effect"
        graph_repo = self._effect_graph_repo or build_group_ops_effect_graph_repository()
        for candidate in candidates:
            source_id = f"{plan_id}:node:{candidate['node_id']}"
            planned = graph_repo.plan(
                GroupOpsEffectGraphRequest(
                    idempotency_key=f"group-ops-run-due:{plan_id}:node:{candidate['node_id']}:{clean_text(request.scheduled_at)}",
                    source_kind="plan_node",
                    plan_id=int(plan_id),
                    node_id=candidate["node_id"],
                    chat_ids=list(candidate["chat_ids"]),
                    content_summary=clean_text(candidate["content_summary"]),
                    content_payload=dict(candidate["content_payload"]),
                    actor_id=clean_text(request.operator) or clean_text(plan.get("owner_userid")),
                    owner_userid=clean_text(plan.get("owner_userid")),
                    webhook_key=clean_text(plan.get("webhook_key")),
                    source_module="automation_engine.group_ops.run_due",
                    source_route="/api/admin/automation-conversion/group-ops/plans/{plan_id}/run-due",
                    source_command_id=source_id,
                    scheduled_at=request.scheduled_at,
                    version_fingerprint=f"manual:{plan_id}:{candidate['node_id']}:{clean_text(request.scheduled_at)}",
                    test_loopback=bool(request.external_effect_test_loopback),
                    test_receiver_base_url=clean_text(request.test_receiver_base_url),
                    test_receiver_response_status=int(request.test_receiver_response_status or 200),
                )
            )
            external_effect_job_ids.extend(int(item) for item in planned.get("job_ids") or [])
            execution_ids.append(clean_text(planned.get("execution_id")))
            status_urls.append(clean_text(planned.get("status_url")))
        return _response(
            {
                "status": "queued",
                "plan_id": int(plan_id),
                "execution_id": execution_ids[0] if len(execution_ids) == 1 else "",
                "execution_ids": execution_ids,
                "status_url": status_urls[0] if len(status_urls) == 1 else "",
                "status_urls": status_urls,
                "broadcast_job_ids": [],
                "legacy_broadcast_job_ids": [],
                "external_effect_job_ids": external_effect_job_ids,
                "outbound_mode": outbound_mode,
                "legacy_outbound_disabled": outbound_mode == "external_effect",
                "external_effect_required": outbound_mode == "external_effect",
                "real_external_call_executed": False,
                "wecom_send_executed": False,
                "real_wecom_call_executed": False,
                "real_group_notice_executed": False,
                "real_mention_all_executed": False,
                "items": candidates,
                "groups": groups,
                "summary": stats,
                "total": len(candidates),
            },
            status_code=202,
            repo=repo,
        )


class GetGroupOpsWebhookConfigQuery:
    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(self, plan_id: int) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        plan = _plan_or_404(repo, plan_id)
        if plan.get("plan_type") != "webhook":
            raise ContractError("webhook config is only available for webhook plans")
        webhook_key = clean_text(plan.get("webhook_key"))
        return _response(
            {
                "planId": f"plan_{int(plan_id)}",
                "endpointKey": webhook_key,
                "method": "POST",
                "url": _webhook_path(webhook_key),
                "webhook_url": _webhook_url(webhook_key),
                "auth_mode": "aicrm_hmac_sha256",
                "authMode": "aicrm_hmac_sha256",
            },
            repo=repo,
        )


class ListGroupOpsMembersQuery:
    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(self, plan_id: int, request: GroupOpsMembersRequest) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        _plan_or_404(repo, plan_id)
        rows, total = repo.list_plan_members(
            int(plan_id),
            {
                "layer_key": request.layer_key,
                "source_type": request.source_type,
                "keyword": request.keyword,
                "limit": clamp_limit(request.limit),
                "offset": max(0, int(request.offset or 0)),
            },
        )
        return _response({"items": rows, "total": total}, repo=repo)


class ImportGroupOpsMembersCommand:
    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(self, plan_id: int, request: GroupOpsMemberImportRequest) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        _plan_or_404(repo, plan_id)
        members = normalize_recipients(request.recipients)
        for group_id in request.group_ids:
            if clean_text(group_id):
                members.append({"group_id": clean_text(group_id)})
        for audience_id in request.audience_ids:
            if clean_text(audience_id):
                members.append({"source_ref_id": clean_text(audience_id)})
        count = repo.upsert_plan_members(int(plan_id), members, source_type=request.source_type, source_ref_id="")
        return _response({"imported": count, "total": count}, status_code=201, repo=repo)


class RefreshGroupOpsMembersFromGroupsCommand:
    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(self, plan_id: int) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        _plan_or_404(repo, plan_id)
        groups = repo.list_bound_groups(int(plan_id))
        members = [
            {
                "group_id": clean_text(item.get("chat_id")),
                "source_ref_id": clean_text(item.get("chat_id")),
            }
            for item in groups
            if clean_text(item.get("chat_id"))
        ]
        count = repo.upsert_plan_members(int(plan_id), members, source_type="group_snapshot", source_ref_id="bound_groups")
        return _response({"refreshed": count, "groups": groups}, repo=repo)


class ListAudienceRulesQuery:
    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(self) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        rows, total = repo.list_audience_rules({})
        return _response({"items": rows, "total": total}, repo=repo)


class CreateAudienceRuleCommand:
    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(self, request: AudienceRuleCreateRequest) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        item = repo.create_audience_rule(request.model_dump())
        return _response({"item": item}, status_code=201, repo=repo)


class CreateAudienceRuleVersionCommand:
    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(self, rule_key: str, request: AudienceRuleVersionCreateRequest) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        item = repo.create_audience_rule_version(rule_key, request.model_dump())
        return _response({"item": item}, status_code=201, repo=repo)


def _rule_result_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in rows:
        layer = clean_text(row.get("layer_key"))
        counts[layer] = counts.get(layer, 0) + 1
    return [{"layerKey": key, "count": value} for key, value in sorted(counts.items())]


def _execute_builtin_rule(repo: GroupOpsRepository, *, rule_key: str, version: int, plan_id: int, params: dict[str, Any]) -> list[dict[str, Any]]:
    if rule_key != "has_used_core_feature":
        raise ContractError("audience rule executor is not registered")
    members, total = repo.list_plan_members(plan_id, {"limit": 10000, "offset": 0})
    if total <= 0:
        raise ContractError("缺少 plan_members 数据源，不能执行 has_used_core_feature")
    raise ContractError("缺少 feature usage / chat activity 数据源，不能伪造命中结果")


class PreviewAudienceRuleCommand:
    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(self, rule_key: str, request: AudienceRuleRunRequest) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        plan_id = _coerce_plan_id(request.planId or request.plan_id or 0)
        version = int(request.version)
        rule_version = repo.get_audience_rule_version(rule_key, version)
        if not rule_version:
            raise NotFoundError("audience rule version not found")
        rows, total = repo.list_audience_rule_results(rule_key, version, plan_id, {"limit": clamp_limit(request.limit), "layers": request.layers})
        if not rows and total == 0:
            rows = _execute_builtin_rule(repo, rule_key=rule_key, version=version, plan_id=plan_id, params=request.params)
            total = len(rows)
        return _response(
            {
                "ruleKey": rule_key,
                "version": version,
                "total": total,
                "layers": _rule_result_summary(rows),
                "samples": [
                    {
                        "userId": clean_text(item.get("user_id")),
                        "externalUserId": clean_text(item.get("external_user_id")),
                        "layerKey": clean_text(item.get("layer_key")),
                        "score": item.get("score") or 0,
                        "reason": clean_text(item.get("reason")),
                    }
                    for item in rows[: clamp_limit(request.limit, default=20)]
                ],
            },
            repo=repo,
        )


class RefreshAudienceRuleCommand(PreviewAudienceRuleCommand):
    def __call__(self, rule_key: str, request: AudienceRuleRunRequest) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        plan_id = _coerce_plan_id(request.planId or request.plan_id or 0)
        version = int(request.version)
        rule_version = repo.get_audience_rule_version(rule_key, version)
        if not rule_version:
            raise NotFoundError("audience rule version not found")
        rows = _execute_builtin_rule(repo, rule_key=rule_key, version=version, plan_id=plan_id, params=request.params)
        count = repo.replace_audience_rule_results(rule_key, version, plan_id, rows)
        return _response({"ruleKey": rule_key, "version": version, "refreshed": count}, repo=repo)


class GetAudienceRuleResultsQuery:
    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(self, rule_key: str, *, plan_id: int, version: int, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        rows, total = repo.list_audience_rule_results(rule_key, int(version), int(plan_id), {"limit": clamp_limit(limit), "offset": offset})
        return _response({"items": rows, "total": total, "ruleKey": rule_key, "version": int(version)}, repo=repo)


class SaveGroupOpsSegmentationCommand:
    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(self, plan_id: int, request: GroupOpsSegmentationRequest) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        _plan_or_404(repo, plan_id)
        item = repo.save_segmentation(int(plan_id), request.model_dump())
        if not item.get("rule_key") or not item.get("rule_version"):
            raise ContractError("ruleKey and ruleVersion are required for preset_rule segmentation")
        if not repo.get_audience_rule_version(clean_text(item["rule_key"]), int(item["rule_version"])):
            raise NotFoundError("audience rule version not found")
        return _response({"item": item}, repo=repo)


class PreviewGroupOpsSegmentationCommand:
    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(self, plan_id: int) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        segmentation = repo.get_segmentation(int(plan_id))
        if not segmentation:
            raise NotFoundError("group ops segmentation not found")
        request = AudienceRuleRunRequest(
            planId=plan_id,
            version=int(segmentation["rule_version"]),
            params=dict(segmentation.get("params") or {}),
            limit=20,
        )
        return PreviewAudienceRuleCommand(repo)(clean_text(segmentation["rule_key"]), request)


class ListGroupOpsExecutionsQuery:
    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(self, plan_id: int, request: GroupOpsExecutionsRequest) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        rows, total = repo.list_execution_logs(
            int(plan_id),
            {
                "trigger_event_id": request.trigger_event_id,
                "status": request.status,
                "action_type": request.action_type,
                "layer_key": request.layer_key,
                "recipient": request.recipient,
                "limit": clamp_limit(request.limit),
                "offset": max(0, int(request.offset or 0)),
            },
        )
        return _response({"items": rows, "total": total}, repo=repo)


class ReceiveGroupOpsWebhookCommand:
    def __init__(
        self,
        repo: GroupOpsRepository | None = None,
        action_port: Any | None = None,
    ) -> None:
        self._repo = repo
        self._action_port = action_port

    def __call__(
        self,
        webhook_key: str,
        request: GroupOpsWebhookReceiveRequest,
        *,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        plan = repo.get_plan_by_webhook_key(webhook_key)
        if not plan:
            raise NotFoundError("group ops webhook not found")
        if plan.get("plan_type") != "webhook":
            raise NotFoundError("group ops webhook not found")
        if plan.get("status") != "active":
            raise ConflictError("group ops webhook plan is not active")
        assert_webhook_rate_limit(webhook_key)
        if self._is_legacy_group_bundle_request(request):
            return self._receive_legacy_group_bundle(repo, plan, request, idempotency_key=idempotency_key)
        idem = clean_text(idempotency_key or request.idempotency_key)
        if not idem:
            raise ContractError("X-Idempotency-Key or idempotency_key is required")
        duplicate = repo.find_trigger_event(int(plan["id"]), idem)
        if duplicate:
            return _response(
                {
                    "accepted": True,
                    "duplicate": True,
                    "triggerEventId": clean_text(duplicate.get("id")),
                    "planId": f"plan_{int(plan['id'])}",
                    "matchedRecipients": 0,
                    "executed": 0,
                    "skipped": 0,
                    "failed": 0,
                    "status": "duplicate",
                },
                repo=repo,
            )
        event = repo.create_trigger_event(
            int(plan["id"]),
            {
                "endpoint_key": webhook_key,
                "event_name": request.event,
                "source": request.source,
                "idempotency_key": idem,
                "payload_json": mask_sensitive_payload(request.model_dump()),
                "status": "accepted",
            },
        )
        try:
            recipients = self._resolve_recipients(repo, plan, request)
            sender = request.sender if isinstance(request.sender, dict) else {}
            operator_account = clean_text(sender.get("operatorAccount") or sender.get("operator_account"))
            default_action = clean_text(plan.get("default_action_type") or "record_only")
            action_by_layer = {
                clean_text(key): normalize_action_payload(value, default_action_type=default_action) for key, value in dict(request.actions or {}).items()
            }
            default_payload = request.action or {}
            executed = 0
            skipped = 0
            failed = 0
            outbound_mode = "external_effect"
            external_effect_job_ids: list[int] = []
            port = self._action_port
            if port is None:
                from .action_port import build_group_ops_action_port

                port = build_group_ops_action_port()
            for recipient in recipients:
                layer_key = clean_text(recipient.get("layer_key"))
                action = action_by_layer.get(layer_key) or normalize_action_payload(default_payload, default_action_type=default_action)
                try:
                    result = port.dispatch(
                        {
                            "plan_id": int(plan["id"]),
                            "planId": int(plan["id"]),
                            "trigger_event_id": clean_text(event["id"]),
                            "triggerEventId": clean_text(event["id"]),
                            "operator_member_id": clean_text(plan.get("owner_userid")),
                            "operatorMemberId": clean_text(plan.get("owner_userid")),
                            "operator_account": operator_account,
                            "operatorAccount": operator_account,
                            "recipient": recipient,
                            "action": action,
                            "context": {"event": request.event, "payload": request.payload},
                        }
                    )
                    status = "success" if result.get("ok", True) else "failed"
                    if status == "success":
                        executed += 1
                    else:
                        failed += 1
                    if clean_text(result.get("execution_owner")) == "external_effect_job":
                        try:
                            action_effect_job_id = int(result.get("action_ref_id") or 0)
                        except (TypeError, ValueError):
                            action_effect_job_id = 0
                        if action_effect_job_id > 0:
                            external_effect_job_ids.append(action_effect_job_id)
                    if group_ops_effect_action_type(action["action_type"]):
                        planned = plan_group_ops_action_effect(
                            plan_id=int(plan["id"]),
                            trigger_event_id=clean_text(event["id"]),
                            recipient=recipient,
                            action=action,
                            operator_member_id=clean_text(plan.get("owner_userid")),
                            source_route="/api/automation/group-ops/webhooks/{webhook_key}",
                            idempotency_key=idem,
                            owner_userid=clean_text(plan.get("owner_userid")),
                            webhook_key=clean_text(plan.get("webhook_key")),
                            test_loopback=bool(request.external_effect_test_loopback),
                            test_receiver_base_url=clean_text(request.test_receiver_base_url),
                            test_receiver_response_status=int(request.test_receiver_response_status or 200),
                        )
                        if planned and int(planned.get("id") or 0):
                            external_effect_job_ids.append(int(planned["id"]))
                    repo.create_execution_log(
                        {
                            "trigger_event_id": event["id"],
                            "plan_id": int(plan["id"]),
                            "event_name": request.event,
                            "recipient": recipient,
                            "sender": sender,
                            "user_id": recipient.get("user_id"),
                            "external_user_id": recipient.get("external_user_id"),
                            "layer_key": layer_key,
                            "action_type": action["action_type"],
                            "action_ref_id": result.get("action_ref_id"),
                            "status": status,
                            "error_message": result.get("error_message", ""),
                            "idempotency_key": idem,
                            "received_at": event.get("received_at"),
                        }
                    )
                except Exception as exc:
                    failed += 1
                    repo.create_execution_log(
                        {
                            "trigger_event_id": event["id"],
                            "plan_id": int(plan["id"]),
                            "event_name": request.event,
                            "recipient": recipient,
                            "sender": sender,
                            "user_id": recipient.get("user_id"),
                            "external_user_id": recipient.get("external_user_id"),
                            "layer_key": layer_key,
                            "action_type": action["action_type"],
                            "status": "failed",
                            "error_message": str(exc),
                            "idempotency_key": idem,
                            "received_at": event.get("received_at"),
                        }
                    )
            status = "success" if failed == 0 else ("partial_failed" if executed else "failed")
            repo.update_trigger_event(clean_text(event["id"]), {"status": status, "error_message": ""})
            return _response(
                {
                    "accepted": True,
                    "duplicate": False,
                    "triggerEventId": clean_text(event["id"]),
                    "planId": f"plan_{int(plan['id'])}",
                    "matchedRecipients": len(recipients),
                    "executed": executed,
                    "skipped": skipped,
                    "failed": failed,
                    "status": status,
                    "external_effect_job_ids": external_effect_job_ids,
                    "legacy_broadcast_job_ids": [],
                    "outbound_mode": outbound_mode,
                    "legacy_outbound_disabled": outbound_mode == "external_effect",
                    "external_effect_required": outbound_mode == "external_effect",
                    "real_external_call_executed": False,
                    "wecom_send_executed": False,
                    "real_wecom_call_executed": False,
                    "real_group_notice_executed": False,
                    "real_mention_all_executed": False,
                },
                status_code=202,
                repo=repo,
            )
        except Exception as exc:
            repo.update_trigger_event(clean_text(event["id"]), {"status": "failed", "error_message": str(exc)})
            raise

    def _is_legacy_group_bundle_request(self, request: GroupOpsWebhookReceiveRequest) -> bool:
        return bool(request.content) and not request.recipients and not request.rule and not request.action and not request.actions

    def _receive_legacy_group_bundle(
        self,
        repo: GroupOpsRepository,
        plan: dict[str, Any],
        request: GroupOpsWebhookReceiveRequest,
        *,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        payload, status_code = receive_trusted_group_bundle(
            repo=repo,
            plan=plan,
            request=request,
            idempotency_key=idempotency_key,
        )
        return _response(payload, status_code=status_code, repo=repo)

    def _resolve_recipients(
        self,
        repo: GroupOpsRepository,
        plan: dict[str, Any],
        request: GroupOpsWebhookReceiveRequest,
    ) -> list[dict[str, Any]]:
        if request.recipients:
            recipients = normalize_recipients(request.recipients)
        elif request.rule:
            rule = dict(request.rule or {})
            rule_key = clean_text(rule.get("rule_key") or rule.get("ruleKey"))
            version = int(rule.get("version") or 0)
            layers = [clean_text(item) for item in list(rule.get("layers") or []) if clean_text(item)]
            rows, _total = repo.list_audience_rule_results(
                rule_key,
                version,
                int(plan["id"]),
                {"limit": 10000, "layers": layers},
            )
            recipients = [
                {
                    "user_id": clean_text(item.get("user_id")),
                    "external_user_id": clean_text(item.get("external_user_id")),
                    "wechat_user_id": "",
                    "group_id": "",
                    "layer_key": clean_text(item.get("layer_key")),
                }
                for item in rows
            ]
        else:
            members, _total = repo.list_plan_members(int(plan["id"]), {"limit": 10000, "offset": 0})
            recipients = [
                {
                    "user_id": clean_text(item.get("user_id")),
                    "external_user_id": clean_text(item.get("external_user_id")),
                    "wechat_user_id": "",
                    "group_id": clean_text(item.get("group_id")),
                    "layer_key": clean_text(item.get("layer_key")),
                }
                for item in members
            ]
        scopes = repo.list_plan_scopes(int(plan["id"]))
        group_scope = {clean_text(item.get("scope_ref_id")) for item in scopes if item.get("scope_type") == "group"}
        audience_scope = {clean_text(item.get("scope_ref_id")) for item in scopes if item.get("scope_type") == "audience"}
        if group_scope:
            recipients = [item for item in recipients if not item.get("group_id") or item.get("group_id") in group_scope]
        if audience_scope:
            allowed = {
                clean_text(item.get("external_user_id"))
                for item in repo.list_plan_members(int(plan["id"]), {"limit": 10000, "offset": 0})[0]
                if clean_text(item.get("source_ref_id")) in audience_scope
            }
            if allowed:
                recipients = [item for item in recipients if clean_text(item.get("external_user_id")) in allowed]
        if not recipients:
            raise ContractError("no matched recipients for group ops webhook")
        return recipients


class ReceiveTrustedGroupOpsBroadcastCommand:
    """Queue a legacy group bundle after the caller has passed machine auth."""

    def __init__(self, repo: GroupOpsRepository | None = None) -> None:
        self._repo = repo

    def __call__(
        self,
        plan_id: int,
        request: GroupOpsWebhookReceiveRequest,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        repo = _repo_or_block(self._repo)
        if repo is None:
            return _production_unavailable()
        plan = _plan_or_404(repo, int(plan_id))
        if plan.get("plan_type") != "webhook":
            raise ContractError("broadcast plan must be a webhook plan")
        if plan.get("status") != "active":
            raise ConflictError("group ops webhook plan is not active")
        assert_webhook_rate_limit(clean_text(plan.get("webhook_key")) or f"plan:{int(plan_id)}")
        return ReceiveGroupOpsWebhookCommand(repo)._receive_legacy_group_bundle(
            repo,
            plan,
            request,
            idempotency_key=idempotency_key,
        )
