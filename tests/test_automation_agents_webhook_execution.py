from __future__ import annotations

import json
from urllib.parse import urlparse

from sqlalchemy import text

from aicrm_next.automation_agents.context_builder import referenced_context_keys
from aicrm_next.automation_agents.worker import AutomationAgentWorker
from aicrm_next.ai_audience_ops.event_types import INBOUND_RECEIVED_EVENT
from aicrm_next.external_effect_composition import (
    AUTOMATION_EXTERNAL_EFFECT_CONTINUATION_CONSUMER,
    build_external_effect_continuation_consumers,
    build_external_effect_continuation_registry,
)
from aicrm_next.internal_event_composition import build_internal_event_consumer_registry
from aicrm_next.platform_foundation.command_bus.models import CommandContext
from aicrm_next.platform_foundation.external_effects.adapters import ExternalEffectAdapterRegistry, WebhookAdapter
from aicrm_next.platform_foundation.external_effects.models import WEBHOOK_GENERIC_PUSH
from aicrm_next.platform_foundation.external_effects.completion_events import (
    EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE,
)
from aicrm_next.platform_foundation.external_effects.service import ExternalEffectService
from aicrm_next.platform_foundation.external_effects.worker import ExternalEffectWorker
from aicrm_next.platform_foundation.internal_events import InternalEventService
from aicrm_next.platform_foundation.internal_events.outbox import InternalEventOutboxRelay
from aicrm_next.platform_foundation.internal_events.worker import InternalEventWorker
from aicrm_next.platform_foundation.auth_platform.webhook_hmac import WebhookHmacSigner
from aicrm_next.shared.db_session import get_session_factory
from tests.webhook_hmac_test_helpers import (
    install_webhook_hmac_client,
    outbound_webhook_hmac_signer,
    signed_headers,
)


def _insert_package(session, *, package_key: str = "agent_callback_pkg") -> int:
    row = (
        session.execute(
            text(
                """
            INSERT INTO ai_audience_package (
                package_key, name, status, created_at, updated_at
            )
            VALUES (:package_key, 'Agent Callback Package', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            RETURNING id
            """
            ),
            {"package_key": package_key},
        )
        .mappings()
        .one()
    )
    return int(row["id"])


def _dispatch_ai_audience_inbound_events() -> list[dict]:
    registry = build_internal_event_consumer_registry()
    relayed = InternalEventOutboxRelay(consumer_registry=registry).relay_due(limit=100)
    assert relayed["ok"] is True
    events, _ = InternalEventService().list_events({"event_type": INBOUND_RECEIVED_EVENT}, limit=100)
    worker = InternalEventWorker(consumer_registry=registry)
    results: list[dict] = []
    for event in events:
        runs, _ = InternalEventService().list_consumer_runs({"event_id": event.event_id}, limit=100)
        for run in runs:
            if run.status in {"pending", "failed_retryable"}:
                results.append(worker.dispatch_one(run))
    assert results
    assert {item["consumer_run"]["status"] for item in results} == {"succeeded"}
    return results


def _insert_agent(
    session,
    *,
    agent_code: str = "activation_agent",
    automation_type: str = "agent",
    status: str = "active",
    package_key: str = "agent_callback_pkg",
    role_prompt: str = "你是助手，参考{{用户标签}}",
    task_prompt: str = "输出话术：{{最近20条聊天信息}}",
    fixed_content_package: str = '{"image_library_ids":[12],"miniprogram_library_ids":[],"attachment_library_ids":[],"content_text":""}',
) -> int:
    row = (
        session.execute(
            text(
                """
            INSERT INTO automation_agent_runtime_config (
                agent_code, agent_name, automation_type, bound_package_key, status,
                draft_role_prompt, draft_task_prompt, published_role_prompt, published_task_prompt,
                draft_version, published_version, fixed_content_package_json, send_webhook_url,
                created_at, updated_at
            )
            VALUES (
                :agent_code, '激活 Agent', :automation_type, :package_key, :status,
                :role_prompt, :task_prompt, :role_prompt, :task_prompt,
                1, 1, CAST(:fixed_content_package AS jsonb), :send_webhook_url,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            RETURNING id
            """
            ),
            {
                "agent_code": agent_code,
                "automation_type": automation_type,
                "package_key": package_key,
                "status": status,
                "role_prompt": role_prompt,
                "task_prompt": task_prompt,
                "fixed_content_package": fixed_content_package,
                "send_webhook_url": f"/api/ai/audience/packages/{package_key}/webhook",
            },
        )
        .mappings()
        .one()
    )
    return int(row["id"])


def _unionid_for_external_userid(external_userid: str) -> str:
    return "union_" + external_userid.removeprefix("wm_")


def _insert_identities(session, *external_userids: str) -> None:
    for external_userid in external_userids:
        unionid = _unionid_for_external_userid(external_userid)
        session.execute(
            text(
                """
                INSERT INTO crm_user_identity (
                    unionid, primary_external_userid, external_userids_json, identity_status, created_at, updated_at
                )
                VALUES (
                    :unionid, :external_userid, jsonb_build_array(CAST(:external_userid AS text)),
                    'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (unionid) DO UPDATE SET
                    primary_external_userid = EXCLUDED.primary_external_userid,
                    external_userids_json = EXCLUDED.external_userids_json,
                    identity_status = EXCLUDED.identity_status,
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            {"unionid": unionid, "external_userid": external_userid},
        )


def _count(table: str) -> int:
    with get_session_factory()() as session:
        return int(session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)


def _json_mapping(value):
    return json.loads(value) if isinstance(value, str) else value


def _registry_with_post(fake_post, *, signer: WebhookHmacSigner | None = None) -> ExternalEffectAdapterRegistry:
    registry = ExternalEffectAdapterRegistry()
    actual_signer = signer or outbound_webhook_hmac_signer()
    registry._adapters["outbound_webhook"] = WebhookAdapter(http_post=fake_post, signer=actual_signer)  # type: ignore[attr-defined]
    registry._adapters["webhook"] = WebhookAdapter(http_post=fake_post, signer=actual_signer)  # type: ignore[attr-defined]
    return registry


def test_agent_webhook_uses_registered_hmac_and_business_idempotency(next_client, next_pg_schema, monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    with get_session_factory()() as session:
        _insert_package(session)
        _insert_agent(session)
        _insert_identities(session, "wm_001", "wm_002")
        session.commit()

    raw = json.dumps(["wm_001", "", "wm_001", "wm_002"], ensure_ascii=False, separators=(",", ":")).encode()
    credentials = install_webhook_hmac_client(
        next_client,
        capability="automation_agent_webhook_receive",
        client_id="pytest-automation-agent-source",
    )
    headers = {
        **signed_headers(credentials, body=raw, event_id="agent-source-event-0001"),
        "X-AICRM-Event-Type": "audience.entered",
        "X-AICRM-Idempotency-Key": "dedupe-key-1",
    }
    accepted = next_client.post(
        "/api/ai/agents/activation_agent/audience-webhook",
        content=raw,
        headers=headers,
    )
    assert accepted.status_code == 200
    assert accepted.json()["mode"] == "queued"
    assert accepted.json()["received_count"] == 4
    assert accepted.json()["deduped_count"] == 2
    assert accepted.json()["accepted_count"] == 2
    assert _count("automation_agent_webhook_batch") == 1
    assert _count("automation_agent_webhook_item") == 2

    replay = next_client.post(
        "/api/ai/agents/activation_agent/audience-webhook",
        content=raw,
        headers=headers,
    )
    assert replay.status_code == 401
    assert replay.json()["error"] == "webhook_event_replayed"

    query_token_only = next_client.post(
        "/api/ai/agents/activation_agent/audience-webhook?token=obsolete-token",
        content=raw,
        headers={"Content-Type": "application/json"},
    )
    assert query_token_only.status_code == 401

    business_replay = next_client.post(
        "/api/ai/agents/activation_agent/audience-webhook",
        content=raw,
        headers={
            **signed_headers(credentials, body=raw, event_id="agent-source-event-0002"),
            "X-AICRM-Idempotency-Key": "dedupe-key-1",
        },
    )
    assert business_replay.status_code == 200
    assert _count("automation_agent_webhook_batch") == 1
    assert _count("automation_agent_webhook_item") == 2


def test_agent_webhook_rejects_inactive_and_large_payload(next_client, next_pg_schema, monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    with get_session_factory()() as session:
        _insert_package(session)
        _insert_agent(session, agent_code="paused_agent", status="paused")
        _insert_agent(session, agent_code="large_agent")
        _insert_identities(session, "wm_001")
        session.commit()

    raw = b'{"external_userids":["wm_001"]}'
    credentials = install_webhook_hmac_client(
        next_client,
        capability="automation_agent_webhook_receive",
        client_id="pytest-automation-agent-validation",
    )
    paused = next_client.post(
        "/api/ai/agents/paused_agent/audience-webhook",
        content=raw,
        headers=signed_headers(credentials, body=raw, event_id="agent-paused-event-0001"),
    )
    assert paused.status_code == 409
    assert paused.json()["error"] == "agent_not_active"

    large_payload = {"external_userids": [f"wm_{i:03d}" for i in range(201)]}
    large_raw = json.dumps(large_payload, separators=(",", ":")).encode()
    large = next_client.post(
        "/api/ai/agents/large_agent/audience-webhook",
        content=large_raw,
        headers=signed_headers(credentials, body=large_raw, event_id="agent-large-event-000001"),
    )
    assert large.status_code == 400
    assert large.json()["error"] == "too_many_external_userids"


def test_prompt_context_key_detection_uses_chinese_placeholders() -> None:
    assert referenced_context_keys("角色{{用户标签}}", "任务{{问卷信息}}{{激活信息}}") == {"tags", "questionnaire", "activation"}
    assert referenced_context_keys("无占位", "只看{{最近20条聊天信息}}") == {"recent_messages"}


def test_context_builder_hydrates_questionnaire_from_bound_audience_payload(monkeypatch) -> None:
    from aicrm_next.automation_agents import context_builder

    class FakeBoundRepository:
        def get_bound_audience_context_for_item(self, **kwargs):
            return {
                "member_event": {
                    "id": 88,
                    "owner_userid": "HuangYouCan",
                    "payload_json": {"submission_id": 1420, "questionnaire_id": 37},
                }
            }

        def list_questionnaire_submission_answers(self, **kwargs):
            assert kwargs["submission_id"] == 1420
            assert kwargs["questionnaire_id"] == 37
            return [
                {
                    "submission_id": 1420,
                    "questionnaire_id": 37,
                    "questionnaire_title": "填写问卷激活黄小璨AI",
                    "question_id": 604,
                    "question": "请输入手机号",
                    "question_type": "mobile",
                    "text_value": "17640055576",
                    "selected_option_texts_snapshot": [],
                },
                {
                    "submission_id": 1420,
                    "questionnaire_id": 37,
                    "questionnaire_title": "填写问卷激活黄小璨AI",
                    "question_id": 607,
                    "question": "你目前在「一人公司」这条路上的状态是？",
                    "question_type": "single_choice",
                    "text_value": "",
                    "selected_option_texts_snapshot": ["有主业，副业探索中，方向还不清晰"],
                },
            ]

    monkeypatch.setattr(
        context_builder,
        "GetCustomerContextQuery",
        lambda *args, **kwargs: lambda request: {"customer": {"external_userid": request.external_userid}, "recent_messages": []},
    )
    monkeypatch.setattr(
        context_builder,
        "get_customer_business_profile",
        lambda external_userid, limit=20: {"business_profile": {"questionnaire_answers": [], "tags": []}},
    )

    context = context_builder.build_agent_context(
        "wm_questionnaire_001",
        {"questionnaire"},
        agent_code="activation_agent",
        batch_id="batch_001",
        repository=FakeBoundRepository(),
    )

    block = context["blocks"]["问卷信息"]
    assert "有主业，副业探索中，方向还不清晰" in block
    assert "已填写（已脱敏）" in block
    assert "17640055576" not in block
    assert context["bound_audience_context"]["questionnaire_answer_count"] == 2
    assert context["bound_audience_context"]["member_event_id"] == 88


def test_worker_fake_mode_generates_package_and_enqueues_send_plan(next_client, next_pg_schema, monkeypatch) -> None:
    monkeypatch.setenv("AICRM_AI_AUDIENCE_AGENT_MODE", "fake")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_AGENT_FAKE_ALLOWED", "1")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_AGENT_FAKE_OUTPUT", "你好，这是 Agent 生成的话术")

    with get_session_factory()() as session:
        _insert_package(session)
        _insert_agent(session)
        _insert_identities(session, "wm_001")
        session.commit()

    raw = b'{"external_userids":["wm_001"]}'
    accepted = next_client.post(
        "/api/ai/agents/activation_agent/audience-webhook",
        content=raw,
        headers={"Content-Type": "application/json"},
    )
    batch_id = accepted.json()["batch_id"]

    from aicrm_next.automation_agents import worker as worker_module

    seen_keys = {}

    def fake_context(external_userid, referenced_keys, **kwargs):
        seen_keys["keys"] = set(referenced_keys)
        return {
            "owner_userid": "owner_001",
            "customer": {"external_userid": external_userid, "owner_userid": "owner_001"},
            "recent_messages": [{"sender": external_userid, "content": "我想了解课程", "send_time": "2026-06-25 10:00"}],
            "tags": ["高意向"],
            "blocks": {"用户标签": "高意向", "最近20条聊天信息": "2026-06-25 10:00 wm_001: 我想了解课程"},
            "referenced_context_keys": sorted(referenced_keys),
        }

    monkeypatch.setattr(worker_module, "build_agent_context", fake_context)

    result = AutomationAgentWorker().run_batch(batch_id)

    assert result["status"] == "succeeded"
    assert seen_keys["keys"] == {"tags", "recent_messages"}
    with get_session_factory()() as session:
        assert int(session.execute(text("SELECT COUNT(*) FROM cloud_broadcast_plans")).scalar() or 0) == 0
        assert int(session.execute(text("SELECT COUNT(*) FROM internal_event_outbox WHERE event_type = :event_type"), {"event_type": INBOUND_RECEIVED_EVENT}).scalar() or 0) == 1
    _dispatch_ai_audience_inbound_events()
    with get_session_factory()() as session:
        item = session.execute(text("SELECT * FROM automation_agent_webhook_item")).mappings().one()
        plan_count = int(session.execute(text("SELECT COUNT(*) FROM cloud_broadcast_plans")).scalar() or 0)
        message = session.execute(text("SELECT * FROM cloud_broadcast_plan_recipient_messages")).mappings().one()
        effect_count = int(session.execute(text("SELECT COUNT(*) FROM external_effect_job WHERE effect_type = 'WECOM_MESSAGE_PRIVATE_SEND'")).scalar() or 0)
    assert item["status"] == "callback_succeeded"
    assert item["owner_userid"] == "owner_001"
    assert item["content_package_json"]["content_text"] == "你好，这是 Agent 生成的话术"
    assert item["content_package_json"]["image_library_ids"] == [12]
    assert plan_count == 1
    assert message["content_text"] == "你好，这是 Agent 生成的话术"
    assert effect_count == 0


def test_worker_rejects_prompt_like_llm_output_before_callback(next_client, next_pg_schema, monkeypatch) -> None:
    monkeypatch.setenv("AICRM_AI_AUDIENCE_AGENT_MODE", "fake")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_AGENT_FAKE_ALLOWED", "1")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_AGENT_FAKE_OUTPUT", "输出话术：{{最近20条聊天信息}}")

    with get_session_factory()() as session:
        _insert_package(session)
        _insert_agent(session)
        _insert_identities(session, "wm_001")
        session.commit()

    raw = b'{"external_userids":["wm_001"]}'
    accepted = next_client.post(
        "/api/ai/agents/activation_agent/audience-webhook",
        content=raw,
        headers={"Content-Type": "application/json"},
    )
    batch_id = accepted.json()["batch_id"]

    from aicrm_next.automation_agents import worker as worker_module

    monkeypatch.setattr(
        worker_module,
        "build_agent_context",
        lambda external_userid, referenced_keys, **kwargs: {
            "owner_userid": "owner_001",
            "customer": {"external_userid": external_userid, "owner_userid": "owner_001"},
            "blocks": {"用户标签": "高意向", "最近20条聊天信息": "2026-06-25 wm_001: 我想了解课程"},
            "referenced_context_keys": sorted(referenced_keys),
        },
    )

    result = AutomationAgentWorker().run_batch(batch_id)

    assert result["status"] == "failed"
    with get_session_factory()() as session:
        item = session.execute(text("SELECT * FROM automation_agent_webhook_item")).mappings().one()
        plan_count = int(session.execute(text("SELECT COUNT(*) FROM cloud_broadcast_plans")).scalar() or 0)
        message_count = int(session.execute(text("SELECT COUNT(*) FROM cloud_broadcast_plan_recipient_messages")).scalar() or 0)
    assert item["status"] == "failed"
    assert item["error_code"] == "llm_output_rejected"
    assert plan_count == 0
    assert message_count == 0


def test_worker_human_review_gate_blocks_auto_send(next_client, next_pg_schema, monkeypatch) -> None:
    monkeypatch.setenv("AICRM_AI_AUDIENCE_AGENT_MODE", "fake")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_AGENT_FAKE_ALLOWED", "1")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_AGENT_FAKE_OUTPUT", "你好，这是需要审核的话术")

    with get_session_factory()() as session:
        _insert_package(session)
        _insert_agent(session)
        _insert_identities(session, "wm_001")
        session.execute(text("UPDATE automation_agent_runtime_config SET need_human_review = TRUE WHERE agent_code = 'activation_agent'"))
        session.commit()

    raw = b'{"external_userids":["wm_001"]}'
    accepted = next_client.post(
        "/api/ai/agents/activation_agent/audience-webhook",
        content=raw,
        headers={"Content-Type": "application/json"},
    )
    batch_id = accepted.json()["batch_id"]

    from aicrm_next.automation_agents import worker as worker_module

    monkeypatch.setattr(
        worker_module,
        "build_agent_context",
        lambda external_userid, referenced_keys, **kwargs: {
            "owner_userid": "owner_001",
            "customer": {"external_userid": external_userid, "owner_userid": "owner_001"},
            "blocks": {"用户标签": "高意向", "最近20条聊天信息": "2026-06-25 wm_001: 我想了解课程"},
            "referenced_context_keys": sorted(referenced_keys),
        },
    )

    result = AutomationAgentWorker().run_batch(batch_id)

    assert result["status"] == "failed"
    with get_session_factory()() as session:
        item = session.execute(text("SELECT * FROM automation_agent_webhook_item")).mappings().one()
        plan_count = int(session.execute(text("SELECT COUNT(*) FROM cloud_broadcast_plans")).scalar() or 0)
    assert item["status"] == "failed"
    assert item["error_code"] == "human_review_required"
    assert plan_count == 0


def test_external_effect_agent_webhook_continuation_enqueues_broadcast_job(next_client, next_pg_schema, monkeypatch) -> None:
    with get_session_factory()() as session:
        _insert_package(session)
        _insert_agent(
            session,
            automation_type="fixed_script",
            fixed_content_package='{"image_library_ids":[],"miniprogram_library_ids":[],"attachment_library_ids":[],"content_text":"收到问卷啦，开始体验。"}',
        )
        _insert_identities(session, "wm_001")
        session.commit()

    from aicrm_next.automation_agents import worker as worker_module

    monkeypatch.setattr(
        worker_module,
        "build_agent_context",
        lambda external_userid, referenced_keys, **kwargs: {
            "owner_userid": "owner_001",
            "customer": {"external_userid": external_userid, "owner_userid": "owner_001"},
            "blocks": {},
            "referenced_context_keys": sorted(referenced_keys),
        },
    )
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES", WEBHOOK_GENERIC_PUSH)
    monkeypatch.setenv("AICRM_PUBLIC_BASE_URL", "https://testserver")

    calls: list[dict] = []
    credentials = install_webhook_hmac_client(
        next_client,
        capability="automation_agent_webhook_receive",
        client_id="pytest-agent-continuation-source",
    )
    signer = WebhookHmacSigner(client_id=credentials.client_id, secret=credentials.secret)

    def loopback_post(url, *, data, headers, timeout):
        parsed = urlparse(url)
        calls.append({"url": url, "json": json.loads(data.decode("utf-8")), "headers": headers, "timeout": timeout})
        with monkeypatch.context() as route_policy:
            route_policy.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
            return next_client.post(parsed.path, content=data, headers=headers)

    job = ExternalEffectService().plan_effect(
        effect_type=WEBHOOK_GENERIC_PUSH,
        adapter_name="outbound_webhook",
        operation="post",
        target_type="automation_agent_audience_webhook",
        target_id="activation_agent",
        payload={
            "webhook_url": "https://testserver/api/ai/agents/activation_agent/audience-webhook",
            "body": {"external_userids": ["wm_001"]},
            "headers": {
                "X-AICRM-Event-Type": "audience.incremental.entered",
                "X-AICRM-Idempotency-Key": "agent-continuation-external-effect",
            },
        },
        context=CommandContext(actor_id="pytest", actor_type="system", trace_id="trace-agent-continuation"),
        business_type="ai_audience_package",
        business_id="agent_callback_pkg",
        source_module="tests",
        idempotency_key="external-effect-agent-continuation",
        execution_mode="execute",
        status="queued",
    )

    result = ExternalEffectWorker(
        adapter_registry=_registry_with_post(loopback_post, signer=signer),
        continuation_registry=build_external_effect_continuation_registry(),
    ).run_due(
        batch_size=1,
        dry_run=False,
        effect_types=[WEBHOOK_GENERIC_PUSH],
    )

    assert result["counts"]["succeeded_count"] == 1
    assert len(calls) == 1
    assert result["items"][0]["post_success_continuation"]["reason"] == "durable_completion_event_pending"
    registry = build_internal_event_consumer_registry()
    relayed = InternalEventOutboxRelay(consumer_registry=registry).relay_due(limit=1)
    assert relayed["counts"]["relayed_count"] == 1
    completion_event = InternalEventService().list_events({"event_type": EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE})[0][0]
    completion_runs, completion_run_count = InternalEventService().list_consumer_runs({"event_id": completion_event.event_id})
    assert completion_run_count == len(build_external_effect_continuation_consumers())
    internal_worker = InternalEventWorker(consumer_registry=registry)
    completion_results = {
        run.consumer_name: internal_worker.dispatch_one(run)
        for run in completion_runs
    }
    assert {item["consumer_run"]["status"] for item in completion_results.values()} == {"succeeded"}
    completion_result = completion_results[AUTOMATION_EXTERNAL_EFFECT_CONTINUATION_CONSUMER]
    assert completion_result["ok"] is True
    assert completion_result["consumer_run"]["result_summary_json"]["continuation"] == "dict"
    _dispatch_ai_audience_inbound_events()
    with get_session_factory()() as session:
        job_row = session.execute(text("SELECT * FROM external_effect_job WHERE id = :job_id"), {"job_id": job["id"]}).mappings().one()
        attempt = session.execute(text("SELECT * FROM external_effect_attempt WHERE job_id = :job_id"), {"job_id": job["id"]}).mappings().one()
        item = session.execute(text("SELECT * FROM automation_agent_webhook_item")).mappings().one()
        recipient = session.execute(text("SELECT * FROM cloud_broadcast_plan_recipients")).mappings().one()
        message = session.execute(text("SELECT * FROM cloud_broadcast_plan_recipient_messages")).mappings().one()
        broadcast = session.execute(text("SELECT * FROM broadcast_jobs")).mappings().one()
        outbound_task_count = int(session.execute(text("SELECT COUNT(*) FROM outbound_tasks")).scalar() or 0)
    response_summary = _json_mapping(attempt["response_summary_json"])
    assert job_row["status"] == "succeeded"
    assert response_summary["automation_agent_batch_id"].startswith("agent_batch_")
    assert response_summary["provider_result_received"] is True
    assert "post_success_continuation" not in response_summary
    assert item["status"] == "callback_succeeded"
    assert recipient["approval_status"] == "approved"
    assert recipient["send_status"] == "queued"
    assert recipient["broadcast_job_id"] == broadcast["id"]
    assert message["status"] == "queued"
    assert message["content_text"] == "收到问卷啦，开始体验。"
    assert broadcast["status"] == "queued"
    assert _json_mapping(broadcast["target_unionids_json"]) == [_unionid_for_external_userid("wm_001")]
    assert outbound_task_count == 0


def test_worker_fixed_script_uses_configured_text_without_agent_generation(next_client, next_pg_schema, monkeypatch) -> None:
    with get_session_factory()() as session:
        _insert_package(session)
        _insert_agent(
            session,
            agent_code="fixed_script_agent",
            automation_type="fixed_script",
            fixed_content_package='{"image_library_ids":[12],"miniprogram_library_ids":[],"attachment_library_ids":[],"content_text":"你好，这是固定话术"}',
        )
        _insert_identities(session, "wm_001")
        session.commit()

    raw = b'{"external_userids":["wm_001"]}'
    accepted = next_client.post(
        "/api/ai/agents/fixed_script_agent/audience-webhook",
        content=raw,
        headers={"Content-Type": "application/json"},
    )
    batch_id = accepted.json()["batch_id"]

    from aicrm_next.automation_agents import worker as worker_module

    seen_keys = {}

    def fake_context(external_userid, referenced_keys, **kwargs):
        seen_keys["keys"] = set(referenced_keys)
        return {
            "owner_userid": "owner_001",
            "customer": {"external_userid": external_userid, "owner_userid": "owner_001"},
            "blocks": {},
            "referenced_context_keys": sorted(referenced_keys),
        }

    def fail_generation(*args, **kwargs):
        raise AssertionError("fixed_script must not call generate_agent_reply")

    monkeypatch.setattr(worker_module, "build_agent_context", fake_context)
    monkeypatch.setattr(worker_module, "generate_agent_reply", fail_generation)

    result = AutomationAgentWorker().run_batch(batch_id)

    assert result["status"] == "succeeded"
    assert seen_keys["keys"] == set()
    _dispatch_ai_audience_inbound_events()
    with get_session_factory()() as session:
        item = session.execute(text("SELECT * FROM automation_agent_webhook_item")).mappings().one()
        message = session.execute(text("SELECT * FROM cloud_broadcast_plan_recipient_messages")).mappings().one()
        effect_count = int(session.execute(text("SELECT COUNT(*) FROM external_effect_job WHERE effect_type = 'WECOM_MESSAGE_PRIVATE_SEND'")).scalar() or 0)
    assert item["status"] == "callback_succeeded"
    assert item["raw_agent_output"] == "你好，这是固定话术"
    assert item["content_package_json"]["content_text"] == "你好，这是固定话术"
    assert item["content_package_json"]["image_library_ids"] == [12]
    assert message["content_text"] == "你好，这是固定话术"
    assert effect_count == 0


def test_worker_fixed_script_fails_when_content_text_missing(next_client, next_pg_schema, monkeypatch) -> None:
    with get_session_factory()() as session:
        _insert_package(session)
        _insert_agent(session, agent_code="empty_fixed_script", automation_type="fixed_script")
        _insert_identities(session, "wm_001")
        session.commit()

    raw = b'{"external_userids":["wm_001"]}'
    accepted = next_client.post(
        "/api/ai/agents/empty_fixed_script/audience-webhook",
        content=raw,
        headers={"Content-Type": "application/json"},
    )
    batch_id = accepted.json()["batch_id"]

    from aicrm_next.automation_agents import worker as worker_module

    monkeypatch.setattr(
        worker_module,
        "build_agent_context",
        lambda external_userid, referenced_keys, **kwargs: {"owner_userid": "owner_001", "blocks": {}, "referenced_context_keys": sorted(referenced_keys)},
    )
    monkeypatch.setattr(
        worker_module,
        "generate_agent_reply",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fixed_script must not call generate_agent_reply")),
    )

    result = AutomationAgentWorker().run_batch(batch_id)

    assert result["status"] == "failed"
    with get_session_factory()() as session:
        item = session.execute(text("SELECT * FROM automation_agent_webhook_item")).mappings().one()
        plan_count = int(session.execute(text("SELECT COUNT(*) FROM cloud_broadcast_plans")).scalar() or 0)
        effect_count = int(session.execute(text("SELECT COUNT(*) FROM external_effect_job WHERE effect_type = 'WECOM_MESSAGE_PRIVATE_SEND'")).scalar() or 0)
    assert item["status"] == "failed"
    assert item["error_code"] == "fixed_content_missing"
    assert plan_count == 0
    assert effect_count == 0


def test_worker_hydrates_questionnaire_prompt_from_bound_audience_submission(next_client, next_pg_schema, monkeypatch) -> None:
    monkeypatch.setenv("AICRM_AI_AUDIENCE_AGENT_MODE", "fake")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_AGENT_FAKE_ALLOWED", "1")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_AGENT_FAKE_OUTPUT", "收到问卷啦～\n\n已根据问卷生成。")

    external_userid = "wm_questionnaire_001"
    with get_session_factory()() as session:
        package_id = _insert_package(session, package_key="bound_questionnaire_pkg")
        _insert_agent(
            session,
            package_key="bound_questionnaire_pkg",
            role_prompt="你是问卷跟进 Agent。",
            task_prompt="请根据{{问卷信息}}生成话术。",
        )
        _insert_identities(session, external_userid)
        session.execute(
            text(
                """
                INSERT INTO questionnaires (id, slug, name, title)
                VALUES (37, 'activate-ai', 'activate-ai', '填写问卷激活黄小璨AI')
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO questionnaire_questions (id, questionnaire_id, type, title, sort_order)
                VALUES
                    (604, 37, 'mobile', '请输入手机号', 1),
                    (607, 37, 'single_choice', '你目前在「一人公司」这条路上的状态是？', 2),
                    (608, 37, 'single_choice', '目前在AI上的实际使用情况？', 3),
                    (609, 37, 'single_choice', '做「一人公司」业务上，你目前最大的卡点是？', 4)
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO questionnaire_submissions (
                    id, questionnaire_id, unionid, follow_user_userid, staff_id,
                    total_score, submitted_at
                ) VALUES (
                    1420, 37, :unionid, 'HuangYouCan', 'HuangYouCan',
                    0, CURRENT_TIMESTAMP
                )
                """
            ),
            {"unionid": _unionid_for_external_userid(external_userid)},
        )
        session.execute(
            text(
                """
                INSERT INTO questionnaire_submission_answers (
                    submission_id, question_id, question_type, question_title_snapshot,
                    selected_option_texts_snapshot, text_value
                ) VALUES
                    (1420, 604, 'mobile', '请输入手机号', '[]'::jsonb, '17640055576'),
                    (1420, 607, 'single_choice', '你目前在「一人公司」这条路上的状态是？', '["有主业，副业探索中，方向还不清晰"]'::jsonb, ''),
                    (1420, 608, 'single_choice', '目前在AI上的实际使用情况？', '["基本不用，想了解能怎么用"]'::jsonb, ''),
                    (1420, 609, 'single_choice', '做「一人公司」业务上，你目前最大的卡点是？', '["不知道做什么方向，定位还不知道"]'::jsonb, '')
                """
            )
        )
        run_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO ai_audience_package_run (
                        package_id, run_type, status, returned_count, entered_count, member_event_count
                    ) VALUES (
                        :package_id, 'incremental', 'succeeded', 1, 1, 1
                    )
                    RETURNING id
                    """
                ),
                {"package_id": package_id},
            ).scalar()
        )
        session.execute(
            text(
                """
                INSERT INTO ai_audience_member_event (
                    package_id, run_id, event_type, identity_type, identity_value,
                    unionid, owner_userid, event_source_key, payload_hash,
                    payload_json, idempotency_key
                ) VALUES (
                    :package_id, :run_id, 'entered', 'external_userid', :external_userid,
                    :unionid, 'HuangYouCan', 'questionnaire_submission:1420', 'hash-1420',
                    CAST(:payload_json AS jsonb),
                    'audience-event-1420'
                )
                """
            ),
            {
                "package_id": package_id,
                "run_id": run_id,
                "external_userid": external_userid,
                "unionid": _unionid_for_external_userid(external_userid),
                "payload_json": json.dumps(
                    {"submission_id": 1420, "questionnaire_id": 37, "owner_userid": "HuangYouCan"},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        )
        session.commit()

    raw = json.dumps([external_userid], ensure_ascii=False, separators=(",", ":")).encode()
    accepted = next_client.post(
        "/api/ai/agents/activation_agent/audience-webhook",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-AICRM-Event-Type": "audience.incremental.entered",
            "X-AICRM-Refresh-Run-Id": str(run_id),
            "X-AICRM-Idempotency-Key": "bound-questionnaire-run",
        },
    )
    assert accepted.status_code == 200

    result = AutomationAgentWorker().run_batch(accepted.json()["batch_id"])

    assert result["status"] == "succeeded"
    with get_session_factory()() as session:
        item = session.execute(text("SELECT * FROM automation_agent_webhook_item")).mappings().one()
    context = _json_mapping(item["context_snapshot_json"])
    questionnaire_block = context["blocks"]["问卷信息"]
    assert "有主业，副业探索中，方向还不清晰" in questionnaire_block
    assert "基本不用，想了解能怎么用" in questionnaire_block
    assert "不知道做什么方向，定位还不知道" in questionnaire_block
    assert "17640055576" not in questionnaire_block
    assert context["bound_audience_context"]["questionnaire_answer_count"] == 4
    assert context["bound_audience_context"]["member_event_id"]
