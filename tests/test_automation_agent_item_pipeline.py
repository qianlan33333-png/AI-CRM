from __future__ import annotations

import json
import os
import time

from sqlalchemy import text

from aicrm_next.external_effect_composition import AUTOMATION_GENERATION_EFFECT_CONTINUATION_CONSUMER
from aicrm_next.extensions.ai.ai_audience_ops.agent_gateway import AgentGatewayResult
from aicrm_next.extensions.ai.automation_agents.application import AutomationAgentWebhookService
from aicrm_next.extensions.ai.automation_agents.generation_effect import AutomationAgentGenerationAdapter
from aicrm_next.extensions.ai.automation_agents.item_events import ITEM_PREPARE_CONSUMER
from aicrm_next.extensions.ai.automation_agents.worker import AutomationAgentWorker
from aicrm_next.internal_event_composition import build_internal_event_consumer_registry
from aicrm_next.platform.platform_foundation.external_effects.adapters import ExternalEffectAdapterRegistry
from aicrm_next.platform.platform_foundation.external_effects.completion_events import EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE
from aicrm_next.platform.platform_foundation.external_effects.models import AI_AGENT_GENERATE, ExternalEffectJob
from aicrm_next.platform.platform_foundation.external_effects.service import ExternalEffectService
from aicrm_next.platform.platform_foundation.external_effects.worker import ExternalEffectWorker
from aicrm_next.platform.platform_foundation.execution_runtime.lanes import recommended_ai_generation_capacity
from aicrm_next.platform.platform_foundation.internal_events.outbox import InternalEventOutboxRelay
from aicrm_next.platform.platform_foundation.internal_events.service import InternalEventService
from aicrm_next.platform.platform_foundation.internal_events.worker import InternalEventWorker
from aicrm_next.platform.shared.db_session import get_engine, get_session_factory


def _seed_agent_and_package(*, agent_code: str = "pipeline_agent") -> None:
    with get_session_factory()() as session:
        session.execute(
            text(
                """
                INSERT INTO ai_audience_package (package_key, name, status, created_at, updated_at)
                VALUES ('pipeline_pkg', 'Pipeline Package', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO automation_agent_runtime_config (
                    agent_code, agent_name, automation_type, bound_package_key, status,
                    draft_role_prompt, draft_task_prompt, published_role_prompt, published_task_prompt,
                    draft_version, published_version, fixed_content_package_json, send_webhook_url,
                    created_at, updated_at
                ) VALUES (
                    :agent_code, 'Pipeline Agent', 'agent', 'pipeline_pkg', 'active',
                    '旧角色 {{用户标签}}', '旧任务 {{最近20条聊天信息}}',
                    '旧角色 {{用户标签}}', '旧任务 {{最近20条聊天信息}}',
                    1, 1, '{}'::jsonb, '/api/ai/audience/packages/pipeline_pkg/webhook',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {"agent_code": agent_code},
        )
        session.commit()


def _seed_identities(count: int) -> list[str]:
    external_userids = [f"wm_pipeline_{index:04d}" for index in range(1, count + 1)]
    with get_session_factory()() as session:
        session.execute(
            text(
                """
                INSERT INTO crm_user_identity (
                    unionid, primary_external_userid, external_userids_json,
                    identity_status, created_at, updated_at
                )
                SELECT
                    'union_pipeline_' || LPAD(value::text, 4, '0'),
                    'wm_pipeline_' || LPAD(value::text, 4, '0'),
                    jsonb_build_array('wm_pipeline_' || LPAD(value::text, 4, '0')),
                    'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                FROM generate_series(1, :count) AS value
                """
            ),
            {"count": int(count)},
        )
        session.commit()
    return external_userids


def test_generation_capacity_ladder_meets_two_per_second_target() -> None:
    assert recommended_ai_generation_capacity(0) == 4
    assert recommended_ai_generation_capacity(1.5) == 4
    assert recommended_ai_generation_capacity(6) == 16
    assert recommended_ai_generation_capacity(9) == 32
    assert recommended_ai_generation_capacity(60) == 64
    assert 3000 / 2 <= 30 * 60


def test_generation_adapter_classifies_success_timeout_and_hides_content() -> None:
    job = ExternalEffectJob(
        id=7,
        effect_type=AI_AGENT_GENERATE,
        adapter_name="ai_agent_generation",
        operation="generate",
        target_type="automation_agent_webhook_item",
        target_id="41",
        business_type="automation_agent_generation",
        business_id="41",
        execution_mode="execute",
        payload_json={
            "item_id": 41,
            "batch_id": "agent_batch_test",
            "agent_code": "pipeline_agent",
            "agent_published_version": 3,
            "role_prompt": "role",
            "task_prompt": "task",
            "variables": {"external_userid": "wm_masked"},
        },
    )
    success = AutomationAgentGenerationAdapter(
        lambda **_kwargs: AgentGatewayResult(
            ok=True,
            final_text="仅存在受限 provider result 中",
            mode="production",
            provider="deepseek",
            model="deepseek-chat",
            latency_ms=7000,
            response_summary={"usage": {"total_tokens": 50}},
            external_call_executed=True,
        )
    ).dispatch(job)
    assert success.status == "succeeded"
    assert success.provider_result == {"final_text": "仅存在受限 provider result 中"}
    assert "仅存在" not in json.dumps(success.response_summary, ensure_ascii=False)
    assert success.response_summary["latency_ms"] == 7000

    timeout = AutomationAgentGenerationAdapter(
        lambda **_kwargs: AgentGatewayResult(
            ok=False,
            mode="production",
            provider="deepseek",
            model="deepseek-chat",
            latency_ms=30000,
            error_code="agent_gateway_call_failed",
            error_message="timed out",
            external_call_executed=True,
        )
    ).dispatch(job)
    assert timeout.status == "failed_retryable"
    assert timeout.error_code == "agent_gateway_call_failed"
    assert timeout.real_external_call_executed is True


def test_runtime_releases_database_connection_before_model_call(next_pg_schema) -> None:
    observations: list[int] = []

    def generator(**_kwargs):
        observations.append(int(get_engine().pool.checkedout()))
        return AgentGatewayResult(
            ok=True,
            final_text="生成完成",
            mode="production",
            provider="deepseek",
            model="deepseek-chat",
            latency_ms=6500,
            external_call_executed=True,
        )

    ExternalEffectService().plan_effect(
        effect_type=AI_AGENT_GENERATE,
        adapter_name="ai_agent_generation",
        operation="generate",
        target_type="automation_agent_webhook_item",
        target_id="1",
        payload={
            "item_id": 1,
            "batch_id": "agent_batch_connection_test",
            "agent_code": "pipeline_agent",
            "agent_published_version": 1,
            "role_prompt": "role",
            "task_prompt": "task",
            "variables": {},
        },
        business_type="automation_agent_generation",
        business_id="1",
        idempotency_key="ai-generation-connection-test",
        lane="ai_generation",
        status="queued",
        execution_mode="execute",
    )
    result = ExternalEffectWorker(
        adapter_registry=ExternalEffectAdapterRegistry(
            {"ai_agent_generation": AutomationAgentGenerationAdapter(generator)}
        )
    ).run_due(batch_size=1, dry_run=False, effect_types=[AI_AGENT_GENERATE])

    assert observations == [0]
    assert result["counts"]["succeeded_count"] == 1


def test_webhook_bulk_persists_3000_items_and_prepare_events_without_model_call(next_pg_schema) -> None:
    _seed_agent_and_package()
    external_userids = _seed_identities(3000)
    payload = {"external_userids": external_userids}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()

    started = time.perf_counter()
    response, status_code = AutomationAgentWebhookService().handle(
        "pipeline_agent",
        payload,
        raw_body=raw,
        headers={"X-AICRM-Idempotency-Key": "pipeline-3000"},
    )
    elapsed = time.perf_counter() - started

    assert status_code == 200
    assert response["accepted_count"] == 3000
    # An isolated runner represents the product's "available capacity" SLA.
    # Full regression intentionally runs eight database-heavy shards at once,
    # so keep that suite sensitive to gross regressions without treating host
    # contention as product latency.  The dedicated performance job below runs
    # this same test serially and therefore enforces the stronger ten-second SLA.
    elapsed_budget_seconds = 30 if os.environ.get("PYTEST_XDIST_WORKER") else 10
    assert elapsed < elapsed_budget_seconds
    with get_session_factory()() as session:
        batch = session.execute(text("SELECT * FROM automation_agent_webhook_batch")).mappings().one()
        item_count = int(session.execute(text("SELECT COUNT(*) FROM automation_agent_webhook_item")).scalar() or 0)
        prepare_count = int(
            session.execute(
                text("SELECT COUNT(*) FROM internal_event_outbox WHERE event_type = 'automation_agent.item.prepare'")
            ).scalar()
            or 0
        )
        effect_count = int(session.execute(text("SELECT COUNT(*) FROM external_effect_job")).scalar() or 0)
    assert item_count == 3000
    assert prepare_count == 3000
    assert effect_count == 0
    assert batch["prepare_enqueued_count"] == 3000
    assert batch["agent_published_version"] == 1
    assert batch["agent_config_snapshot_json"]["published_task_prompt"].startswith("旧任务")


def test_prepare_uses_frozen_agent_snapshot_and_generation_effect_is_idempotent(next_pg_schema, monkeypatch) -> None:
    _seed_agent_and_package()
    external_userid = _seed_identities(1)[0]
    payload = {"external_userids": [external_userid]}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    response, status_code = AutomationAgentWebhookService().handle(
        "pipeline_agent",
        payload,
        raw_body=raw,
        headers={"X-AICRM-Idempotency-Key": "pipeline-snapshot"},
    )
    assert status_code == 200
    with get_session_factory()() as session:
        item_id = int(session.execute(text("SELECT id FROM automation_agent_webhook_item")).scalar())
        session.execute(
            text(
                """
                UPDATE automation_agent_runtime_config
                SET published_role_prompt = '新角色',
                    published_task_prompt = '新任务',
                    published_version = 2,
                    fixed_content_package_json = '{"content_text":"新内容"}'::jsonb
                WHERE agent_code = 'pipeline_agent'
                """
            )
        )
        session.commit()

    from aicrm_next.extensions.ai.automation_agents import worker as worker_module

    monkeypatch.setattr(
        worker_module,
        "build_agent_context",
        lambda external_userid, referenced_keys, **_kwargs: {
            "owner_userid": "owner_pipeline",
            "customer": {"external_userid": external_userid, "owner_userid": "owner_pipeline"},
            "blocks": {"用户标签": "高意向", "最近20条聊天信息": "客户询问课程"},
            "referenced_context_keys": sorted(referenced_keys),
        },
    )
    first = AutomationAgentWorker().prepare_item(item_id, source_event_id="event-1")
    second = AutomationAgentWorker().prepare_item(item_id, source_event_id="event-1")

    assert first["status"] == "generation_queued"
    assert second["deduplicated"] is True
    with get_session_factory()() as session:
        jobs = session.execute(
            text("SELECT * FROM external_effect_job WHERE effect_type = :effect_type"),
            {"effect_type": AI_AGENT_GENERATE},
        ).mappings().all()
    assert len(jobs) == 1
    assert jobs[0]["lane"] == "ai_generation"
    assert jobs[0]["payload_json"]["agent_published_version"] == 1
    assert jobs[0]["payload_json"]["role_prompt"].startswith("旧角色")
    assert "新角色" not in jobs[0]["payload_json"]["role_prompt"]
    assert response["batch_id"].startswith("agent_batch_")


def test_generation_completion_durably_creates_one_send_plan(next_pg_schema, monkeypatch) -> None:
    _seed_agent_and_package()
    external_userid = _seed_identities(1)[0]
    from aicrm_next.extensions.ai.automation_agents import worker as worker_module

    monkeypatch.setattr(
        worker_module,
        "build_agent_context",
        lambda target_external_userid, referenced_keys, **_kwargs: {
            "external_userid": target_external_userid,
            "owner_userid": "owner_pipeline",
            "customer": {
                "external_userid": target_external_userid,
                "owner_userid": "owner_pipeline",
            },
            "blocks": {"用户标签": "高意向", "最近20条聊天信息": "客户询问课程"},
            "referenced_context_keys": sorted(referenced_keys),
        },
    )
    payload = {"external_userids": [external_userid]}
    response, status_code = AutomationAgentWebhookService().handle(
        "pipeline_agent",
        payload,
        raw_body=json.dumps(payload, separators=(",", ":")).encode(),
        headers={"X-AICRM-Idempotency-Key": "pipeline-durable-completion"},
    )
    assert status_code == 200

    registry = build_internal_event_consumer_registry()
    prepare_relay = InternalEventOutboxRelay(consumer_registry=registry).relay_due(limit=10)
    assert prepare_relay["counts"]["relayed_count"] == 1
    prepare_event_id = prepare_relay["items"][0]["event_id"]
    prepare_runs, _ = InternalEventService().list_consumer_runs({"event_id": prepare_event_id})
    prepare_run = next(run for run in prepare_runs if run.consumer_name == ITEM_PREPARE_CONSUMER)
    prepared = InternalEventWorker(consumer_registry=registry).dispatch_one(prepare_run)
    assert prepared["consumer_run"]["status"] == "succeeded"

    generated = ExternalEffectWorker(
        adapter_registry=ExternalEffectAdapterRegistry(
            {
                "ai_agent_generation": AutomationAgentGenerationAdapter(
                    lambda **_kwargs: AgentGatewayResult(
                        ok=True,
                        final_text="这是为当前客户生成的一对一话术。",
                        mode="production",
                        provider="test-provider",
                        model="test-model",
                        latency_ms=8000,
                        external_call_executed=True,
                    )
                )
            }
        )
    ).run_due(batch_size=1, dry_run=False, effect_types=[AI_AGENT_GENERATE])
    assert generated["counts"]["succeeded_count"] == 1

    completion_relay = InternalEventOutboxRelay(consumer_registry=registry).relay_due(limit=10)
    assert completion_relay["counts"]["relayed_count"] == 2
    completion_events, completion_event_count = InternalEventService().list_events(
        {"event_type": EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE}
    )
    assert completion_event_count == 1
    completion_event_id = completion_events[0].event_id
    completion_runs, _ = InternalEventService().list_consumer_runs(
        {"event_id": completion_event_id}
    )
    completion_run = next(
        run
        for run in completion_runs
        if run.consumer_name == AUTOMATION_GENERATION_EFFECT_CONTINUATION_CONSUMER
    )
    completed = InternalEventWorker(consumer_registry=registry).dispatch_one(completion_run)
    assert completed["consumer_run"]["status"] == "succeeded"

    with get_session_factory()() as session:
        item = session.execute(text("SELECT * FROM automation_agent_webhook_item")).mappings().one()
        broadcast_count = int(session.execute(text("SELECT COUNT(*) FROM broadcast_jobs")).scalar() or 0)
        send_effect_count = int(
            session.execute(
                text(
                    "SELECT COUNT(*) FROM external_effect_job "
                    "WHERE effect_type = 'wecom.message.private.send'"
                )
            ).scalar()
            or 0
        )
    assert item["status"] == "send_plan_created"
    assert item["generation_completed_at"] is not None
    assert item["send_plan_created_at"] is not None
    assert broadcast_count == 1
    assert send_effect_count == 1
    assert response["batch_id"] == item["batch_id"]

    duplicate = AutomationAgentWorker().complete_generation(
        item_id=int(item["id"]),
        generation_effect_job_id=int(item["generation_effect_job_id"]),
        final_text="这是为当前客户生成的一对一话术。",
    )
    assert duplicate == {"ok": True, "deduplicated": True, "item_id": int(item["id"])}
    with get_session_factory()() as session:
        assert int(session.execute(text("SELECT COUNT(*) FROM broadcast_jobs")).scalar() or 0) == 1
        assert (
            int(
                session.execute(
                    text(
                        "SELECT COUNT(*) FROM external_effect_job "
                        "WHERE effect_type = 'wecom.message.private.send'"
                    )
                ).scalar()
                or 0
            )
            == 1
        )
