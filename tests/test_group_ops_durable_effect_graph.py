from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from aicrm_next.automation_engine.group_ops.durable_effects_repository import (
    GroupOpsEffectGraphRequest,
    GroupOpsEffectMaterial,
    InMemoryGroupOpsEffectGraphRepository,
)
from aicrm_next.platform_foundation.external_effects.repo_memory import InMemoryExternalEffectRepository


PNG = b"\x89PNG\r\n\x1a\n" + b"group-ops-durable-effect"


def _request(
    key: str,
    *,
    materials: int = 0,
    source_kind: str = "direct_send",
    version: str = "v1",
    scheduled_at: datetime | None = None,
) -> GroupOpsEffectGraphRequest:
    return GroupOpsEffectGraphRequest(
        idempotency_key=key,
        source_kind=source_kind,
        plan_id=7,
        node_id=8 if source_kind == "plan_node" else 0,
        chat_ids=["chat-1"],
        content_payload={
            "channel": "wecom_customer_group",
            "sender": "owner-1",
            "text": {"content": f"hello {version}"},
            "attachments": [],
        },
        content_summary=f"hello {version}",
        actor_id="operator-1",
        owner_userid="owner-1",
        source_module="pytest.group_ops",
        source_route="/pytest/group-ops",
        source_command_id=key,
        scheduled_at=scheduled_at,
        version_fingerprint=version,
        materials=tuple(
            GroupOpsEffectMaterial(
                material_key=f"image:{index}",
                role="image",
                file_name=f"image-{index}.png",
                content_type="image/png",
                file_bytes=PNG + bytes([index]),
            )
            for index in range(1, materials + 1)
        ),
    )


def _repositories():
    effects = InMemoryExternalEffectRepository()
    return effects, InMemoryGroupOpsEffectGraphRepository(effects)


def test_uploads_and_final_effect_are_independent_children_of_one_execution():
    effects, graphs = _repositories()
    planned = graphs.plan(_request("graph-order", materials=2))

    assert planned["status"] == "waiting_dependencies"
    assert len(planned["upload_effect_job_ids"]) == 2
    assert planned["job_ids"] == [*planned["upload_effect_job_ids"], planned["final_effect_job_id"]]
    assert effects.get_job(planned["final_effect_job_id"]).status == "planned"
    for job_id in planned["upload_effect_job_ids"]:
        upload = effects.get_job(job_id)
        assert upload.status == "queued"
        assert upload.parent_execution_id == planned["execution_id"]
    assert effects.get_job(planned["final_effect_job_id"]).parent_execution_id == planned["execution_id"]


def test_future_send_opens_media_upload_only_inside_preparation_window():
    effects, graphs = _repositories()
    due_at = datetime.now(timezone.utc) + timedelta(days=7)
    planned = graphs.plan(_request("graph-future-media", materials=1, scheduled_at=due_at))

    upload = effects.get_job(planned["upload_effect_job_ids"][0])
    final = effects.get_job(planned["final_effect_job_id"])
    upload_available_at = datetime.fromisoformat(upload.available_at.replace("Z", "+00:00"))

    assert upload.scheduled_at == final.scheduled_at
    assert timedelta(hours=11, minutes=59) <= due_at - upload_available_at <= timedelta(hours=12, seconds=1)
    assert upload_available_at > datetime.now(timezone.utc)
    assert final.status == "planned"


def test_partial_success_and_failure_never_release_final_effect():
    effects, graphs = _repositories()
    planned = graphs.plan(_request("graph-partial", materials=2))
    first, second = planned["upload_effect_job_ids"]

    first_result = graphs.complete_fixture_upload(first, media_id="media-first")
    graphs.fail_fixture_upload(second)

    assert first_result["released"] is False
    assert first_result["remaining"] == 1
    assert effects.get_job(planned["final_effect_job_id"]).status == "planned"


def test_all_success_releases_once_and_duplicate_completion_is_idempotent():
    effects, graphs = _repositories()
    planned = graphs.plan(_request("graph-complete", materials=2))
    first, second = planned["upload_effect_job_ids"]

    assert graphs.complete_fixture_upload(first, media_id="media-first")["released"] is False
    released = graphs.complete_fixture_upload(second, media_id="media-second")
    duplicate = graphs.complete_fixture_upload(second, media_id="media-second")

    assert released["released"] is True
    assert effects.get_job(planned["final_effect_job_id"]).status == "queued"
    assert duplicate["released"] is False
    assert duplicate["reason"] == "final_effect_already_released"


def test_cancelled_graph_is_not_released_by_late_completion():
    effects, graphs = _repositories()
    planned = graphs.plan(_request("graph-cancel", materials=1))
    upload_id = planned["upload_effect_job_ids"][0]

    cancelled = graphs.cancel(planned["execution_id"], actor="operator-1", reason="no longer needed")
    late = graphs.complete_fixture_upload(upload_id, media_id="late-media")

    assert cancelled["cancelled"] is True
    assert set(cancelled["cancelled_job_ids"]) == set(planned["job_ids"])
    assert effects.get_job(planned["final_effect_job_id"]).status == "cancelled"
    assert late["released"] is False
    assert late["reason"] == "graph_cancelled"


def test_plan_edit_versions_cancel_only_pre_provider_old_effects():
    effects, graphs = _repositories()
    old = graphs.plan(_request("plan-v1", source_kind="plan_node", version="v1"))
    old_final = effects.acquire_job(old["final_effect_job_id"], locked_by="provider-worker")
    assert old_final is not None
    assert effects.begin_provider_attempt(job=old_final, request_summary={}) is not None

    new = graphs.plan(_request("plan-v2", source_kind="plan_node", version="v2"))

    assert old["source_version"] == 1
    assert new["source_version"] == 2
    assert effects.get_job(old["final_effect_job_id"]).status == "dispatching"
    assert effects.get_job(old["final_effect_job_id"]).provider_call_started_at
    assert effects.get_job(new["final_effect_job_id"]).status == "queued"


def test_same_plan_version_and_idempotency_reuse_without_duplicate_effects():
    effects, graphs = _repositories()
    first = graphs.plan(_request("same-plan-unit", source_kind="plan_node", version="v1"))
    duplicate = graphs.plan(_request("same-plan-unit", source_kind="plan_node", version="v1"))

    assert duplicate["duplicate"] is True
    assert duplicate["execution_id"] == first["execution_id"]
    assert duplicate["job_ids"] == first["job_ids"]
    assert len(effects.list_jobs({}, limit=20)[0]) == 1


def test_terminal_upload_closes_graph_and_cancels_unreleased_final_effect():
    effects, graphs = _repositories()
    planned = graphs.plan(_request("graph-terminal-upload", materials=1))
    upload_id = planned["upload_effect_job_ids"][0]

    settled = graphs.settle_effect(
        upload_id,
        status="failed_terminal",
        attempt_id="eea_terminal_upload",
    )
    duplicate = graphs.settle_effect(
        upload_id,
        status="failed_terminal",
        attempt_id="eea_terminal_upload",
    )

    assert settled["settled"] is True
    assert settled["effect_status"] == "failed_terminal"
    assert effects.get_job(planned["final_effect_job_id"]).status == "cancelled"
    assert duplicate["settled"] is False
    assert duplicate["reason"] == "graph_terminal"


def test_plan_scope_cancel_invalidates_all_pre_provider_node_graphs():
    effects, graphs = _repositories()
    first = graphs.plan(_request("plan-scope-node-1", source_kind="plan_node", version="revision-1"))
    second_request = _request("plan-scope-node-2", source_kind="plan_node", version="revision-1")
    second_request = replace(second_request, node_id=9)
    second = graphs.plan(second_request)

    cancelled = graphs.cancel_plan(
        7,
        actor="operator-1",
        reason="plan owner changed",
    )

    assert cancelled["matched_graph_count"] == 2
    assert set(cancelled["cancelled_execution_ids"]) == {first["execution_id"], second["execution_id"]}
    assert effects.get_job(first["final_effect_job_id"]).status == "cancelled"
    assert effects.get_job(second["final_effect_job_id"]).status == "cancelled"
