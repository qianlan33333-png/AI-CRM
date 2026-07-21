from __future__ import annotations

from typing import Any

from aicrm_next.shared.errors import NotFoundError
from aicrm_next.shared.runtime import production_data_ready

from .domain import binding_stats, clean_text, normalize_node_payload
from .dto import GroupOpsBindGroupRequest, GroupOpsNodeRequest, GroupOpsPlanUpdateRequest
from .durable_effects_repository import (
    GroupOpsEffectGraphRepository,
    build_group_ops_effect_graph_repository,
)
from .projections import plan_public_payload
from .repo import GroupOpsRepository


def resolve_effect_graph_repository(
    explicit: GroupOpsEffectGraphRepository | None,
) -> GroupOpsEffectGraphRepository | None:
    if explicit is not None:
        return explicit
    if not production_data_ready():
        return None
    return build_group_ops_effect_graph_repository()


def _plan(repo: GroupOpsRepository, plan_id: int) -> dict[str, Any]:
    plan = repo.get_plan(int(plan_id))
    if not plan:
        raise NotFoundError("group ops plan not found")
    return plan


def _invalidate(
    effect_graph_repo: GroupOpsEffectGraphRepository | None,
    *,
    plan_id: int,
    operator: str,
    reason: str,
    node_id: int | None = None,
) -> dict[str, Any]:
    if effect_graph_repo is None:
        return {}
    return effect_graph_repo.cancel_plan(
        int(plan_id),
        actor=clean_text(operator) or "group_ops_plan_editor",
        reason=clean_text(reason) or "group_ops_plan_revised",
        node_id=node_id,
    )


def _materialize(
    *,
    repo: GroupOpsRepository,
    plan: dict[str, Any],
    operator: str,
    effect_graph_repo: GroupOpsEffectGraphRepository | None,
    node_ids: set[int] | None = None,
) -> dict[str, Any]:
    if effect_graph_repo is None or clean_text(plan.get("status")) != "active":
        return {}
    from .scheduler import materialize_group_ops_plan_node

    items: list[dict[str, Any]] = []
    for node in repo.list_nodes(int(plan["id"])):
        node_id = int(node.get("id") or 0)
        if node_ids is not None and node_id not in node_ids:
            continue
        if clean_text(node.get("status") or "active") != "active":
            continue
        items.append(
            {
                "node_id": node_id,
                "summary": materialize_group_ops_plan_node(
                    repo=repo,
                    plan=plan,
                    node=node,
                    operator=(
                        clean_text(operator)
                        or clean_text(plan.get("owner_userid"))
                        or "group_ops_plan_editor"
                    ),
                    effect_graph_repo=effect_graph_repo,
                ),
            }
        )
    return {"plan_id": int(plan["id"]), "node_count": len(items), "items": items}


def update_plan(
    repo: GroupOpsRepository,
    effect_graph_repo: GroupOpsEffectGraphRepository | None,
    plan_id: int,
    request: GroupOpsPlanUpdateRequest,
) -> dict[str, Any]:
    existing = _plan(repo, plan_id)
    graphs = resolve_effect_graph_repository(effect_graph_repo)
    operator = clean_text(existing.get("owner_userid")) or "group_ops_plan_editor"
    plan = repo.update_plan(int(plan_id), request.model_dump(exclude_none=True))
    invalidation = _invalidate(
        graphs,
        plan_id=plan_id,
        operator=operator,
        reason="group_ops_plan_updated",
    )
    materialization = _materialize(
        repo=repo,
        plan=plan,
        operator=clean_text(plan.get("owner_userid")) or operator,
        effect_graph_repo=graphs,
    )
    return {
        "item": plan,
        "effect_invalidation": invalidation,
        "effect_materialization": materialization,
        **plan_public_payload(repo, plan),
    }


def enable_plan(
    repo: GroupOpsRepository,
    effect_graph_repo: GroupOpsEffectGraphRepository | None,
    plan_id: int,
    *,
    operator: str,
) -> dict[str, Any]:
    existing = _plan(repo, plan_id)
    from aicrm_next.send_content.application import assert_group_invite_bindings_ready

    for node in repo.list_nodes(int(plan_id)):
        if clean_text(node.get("status") or "active") == "active":
            assert_group_invite_bindings_ready(
                node.get("content_package_json") or {},
                channel="group_ops",
            )
    graphs = resolve_effect_graph_repository(effect_graph_repo)
    invalidation = _invalidate(
        graphs,
        plan_id=plan_id,
        operator=operator,
        reason="group_ops_plan_enabled_new_epoch",
    )
    plan = repo.update_plan(int(plan_id), {"status": "active", "operator": operator})
    materialization = _materialize(
        repo=repo,
        plan=plan,
        operator=operator or clean_text(existing.get("owner_userid")),
        effect_graph_repo=graphs,
    )
    return {
        "item": plan,
        "effect_invalidation": invalidation,
        "effect_materialization": materialization,
        **plan_public_payload(repo, plan),
    }


def disable_plan(
    repo: GroupOpsRepository,
    effect_graph_repo: GroupOpsEffectGraphRepository | None,
    plan_id: int,
    *,
    operator: str,
) -> dict[str, Any]:
    _plan(repo, plan_id)
    invalidation = _invalidate(
        resolve_effect_graph_repository(effect_graph_repo),
        plan_id=plan_id,
        operator=operator,
        reason="group_ops_plan_disabled",
    )
    plan = repo.update_plan(int(plan_id), {"status": "disabled", "operator": operator})
    return {
        "item": plan,
        "effect_invalidation": invalidation,
        **plan_public_payload(repo, plan),
    }


def archive_plan(
    repo: GroupOpsRepository,
    effect_graph_repo: GroupOpsEffectGraphRepository | None,
    plan_id: int,
    *,
    operator: str,
) -> dict[str, Any]:
    _plan(repo, plan_id)
    invalidation = _invalidate(
        resolve_effect_graph_repository(effect_graph_repo),
        plan_id=plan_id,
        operator=operator,
        reason="group_ops_plan_archived",
    )
    plan = repo.archive_plan(int(plan_id), operator=operator)
    return {
        "archived": True,
        "item": plan,
        "effect_invalidation": invalidation,
        **plan_public_payload(repo, plan),
    }


def add_group(
    repo: GroupOpsRepository,
    effect_graph_repo: GroupOpsEffectGraphRepository | None,
    plan_id: int,
    request: GroupOpsBindGroupRequest,
) -> dict[str, Any]:
    plan = _plan(repo, plan_id)
    group = repo.get_group_asset(request.chat_id)
    if not group:
        raise NotFoundError("group chat snapshot not found")
    graphs = resolve_effect_graph_repository(effect_graph_repo)
    operator = clean_text(plan.get("owner_userid")) or "group_ops_plan_editor"
    invalidation = _invalidate(
        graphs,
        plan_id=plan_id,
        operator=operator,
        reason="group_ops_plan_group_added",
    )
    item = repo.bind_group(int(plan_id), group)
    groups = repo.list_bound_groups(int(plan_id))
    materialization = _materialize(
        repo=repo,
        plan=repo.get_plan(int(plan_id)) or plan,
        operator=operator,
        effect_graph_repo=graphs,
    )
    return {
        "item": item,
        "summary": binding_stats(groups),
        "effect_invalidation": invalidation,
        "effect_materialization": materialization,
    }


def remove_group(
    repo: GroupOpsRepository,
    effect_graph_repo: GroupOpsEffectGraphRepository | None,
    plan_id: int,
    chat_id: str,
) -> dict[str, Any]:
    plan = _plan(repo, plan_id)
    if not any(
        clean_text(item.get("chat_id")) == clean_text(chat_id)
        for item in repo.list_bound_groups(int(plan_id))
    ):
        raise NotFoundError("group binding not found")
    graphs = resolve_effect_graph_repository(effect_graph_repo)
    operator = clean_text(plan.get("owner_userid")) or "group_ops_plan_editor"
    invalidation = _invalidate(
        graphs,
        plan_id=plan_id,
        operator=operator,
        reason="group_ops_plan_group_removed",
    )
    if not repo.remove_group(int(plan_id), chat_id):
        raise NotFoundError("group binding not found")
    materialization = _materialize(
        repo=repo,
        plan=repo.get_plan(int(plan_id)) or plan,
        operator=operator,
        effect_graph_repo=graphs,
    )
    return {
        "removed": True,
        "summary": binding_stats(repo.list_bound_groups(int(plan_id))),
        "effect_invalidation": invalidation,
        "effect_materialization": materialization,
    }


def create_node(
    repo: GroupOpsRepository,
    effect_graph_repo: GroupOpsEffectGraphRepository | None,
    plan_id: int,
    request: GroupOpsNodeRequest,
) -> dict[str, Any]:
    plan = _plan(repo, plan_id)
    item = repo.create_node(int(plan_id), normalize_node_payload(request.model_dump()))
    graphs = resolve_effect_graph_repository(effect_graph_repo)
    materialization = _materialize(
        repo=repo,
        plan=plan,
        operator=clean_text(plan.get("owner_userid")),
        effect_graph_repo=graphs,
        node_ids={int(item["id"])},
    )
    items = materialization.get("items") or []
    return {
        "item": item,
        "effect_materialization": dict(items[0].get("summary") or {}) if items else {},
    }


def update_node(
    repo: GroupOpsRepository,
    effect_graph_repo: GroupOpsEffectGraphRepository | None,
    plan_id: int,
    node_id: int,
    request: GroupOpsNodeRequest,
) -> dict[str, Any]:
    plan = _plan(repo, plan_id)
    existing = next(
        (item for item in repo.list_nodes(int(plan_id)) if int(item["id"]) == int(node_id)),
        None,
    )
    if not existing:
        raise NotFoundError("group ops node not found")
    graphs = resolve_effect_graph_repository(effect_graph_repo)
    operator = clean_text(plan.get("owner_userid")) or "group_ops_plan_editor"
    invalidation = _invalidate(
        graphs,
        plan_id=plan_id,
        node_id=node_id,
        operator=operator,
        reason="group_ops_plan_node_updated",
    )
    item = repo.update_node(
        int(plan_id),
        int(node_id),
        normalize_node_payload(request.model_dump(), existing=existing),
    )
    materialization = _materialize(
        repo=repo,
        plan=plan,
        operator=operator,
        effect_graph_repo=graphs,
        node_ids={int(node_id)},
    )
    items = materialization.get("items") or []
    return {
        "item": item,
        "effect_invalidation": invalidation,
        "effect_materialization": dict(items[0].get("summary") or {}) if items else {},
    }


def delete_node(
    repo: GroupOpsRepository,
    effect_graph_repo: GroupOpsEffectGraphRepository | None,
    plan_id: int,
    node_id: int,
) -> dict[str, Any]:
    plan = _plan(repo, plan_id)
    if not any(int(item["id"]) == int(node_id) for item in repo.list_nodes(int(plan_id))):
        raise NotFoundError("group ops node not found")
    invalidation = _invalidate(
        resolve_effect_graph_repository(effect_graph_repo),
        plan_id=plan_id,
        node_id=node_id,
        operator=clean_text(plan.get("owner_userid")) or "group_ops_plan_editor",
        reason="group_ops_plan_node_deleted",
    )
    if not repo.delete_node(int(plan_id), int(node_id)):
        raise NotFoundError("group ops node not found")
    return {"deleted": True, "effect_invalidation": invalidation}


__all__ = [
    "add_group",
    "archive_plan",
    "create_node",
    "delete_node",
    "disable_plan",
    "enable_plan",
    "remove_group",
    "resolve_effect_graph_repository",
    "update_node",
    "update_plan",
]
