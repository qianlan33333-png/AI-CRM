from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aicrm_next.platform_foundation.command_bus import CommandContext
from aicrm_next.platform_foundation.external_effects import (
    AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK,
    GROUP_OPS_MESSAGE_LOOPBACK,
    WECOM_MESSAGE_GROUP_SEND,
    WECOM_MESSAGE_PRIVATE_SEND,
    WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
    ExternalEffectService,
    reset_external_effect_fixture_state,
)
from aicrm_next.platform_foundation.external_effects.repo import build_external_effect_repository
from aicrm_next.platform_foundation.push_center.projection import BroadcastJobAdapter, PushCenterProjectionService
from aicrm_next.platform_foundation.push_center import api as push_center_api
from aicrm_next.platform_foundation.push_center.repository import PushCenterRepository
from aicrm_next.platform_foundation.push_center.section_mapper import effect_types_for_section, label_for_section, section_for_job
from aicrm_next.platform_foundation.push_center.sql_read_model import (
    InvalidPushCenterCursor,
    _FAST_PAGE_SQL,
    _public_item,
    decode_push_center_cursor,
    encode_push_center_cursor,
)
from aicrm_next.platform_foundation.push_center.view_model import (
    build_job_detail_payload,
    build_job_reconciliation_payload,
    build_jobs_payload,
    build_stats_payload,
)
from tests.admin_auth_test_helpers import install_admin_action_tokens
from tests.wechat_identity_test_support import authorize_wechat_client

pytest_plugins = ("tests.group_ops_test_helpers",)


def test_push_center_only_materializes_narrow_filter_rows() -> None:
    assert "WITH runtime_control AS MATERIALIZED (" in _FAST_PAGE_SQL
    assert "wide_source_rows AS NOT MATERIALIZED (" in _FAST_PAGE_SQL
    assert "), filtered AS MATERIALIZED (" in _FAST_PAGE_SQL
    assert "summary_counts AS MATERIALIZED (" in _FAST_PAGE_SQL
    assert "CROSS JOIN LATERAL (" in _FAST_PAGE_SQL
    assert "FROM wide_source_rows candidate" in _FAST_PAGE_SQL
    assert ") < (CAST(:cursor_created_at AS timestamptz), :cursor_projection_id)" in _FAST_PAGE_SQL


def test_push_center_cursor_is_signed_and_bound_to_filters(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_PUSH_CENTER_CURSOR_SECRET", "pytest-push-center-cursor-secret")
    filters = {"section": "group_ops", "status": "pending"}
    cursor = encode_push_center_cursor(
        created_at="2026-07-17T10:00:00+08:00",
        projection_id="external_effect_job:42",
        filters=filters,
    )

    assert decode_push_center_cursor(cursor, filters=filters) == (
        "2026-07-17T10:00:00+08:00",
        "external_effect_job:42",
    )
    with pytest.raises(InvalidPushCenterCursor, match="does not match"):
        decode_push_center_cursor(cursor, filters={**filters, "status": "failed"})
    with pytest.raises(InvalidPushCenterCursor, match="signature"):
        decode_push_center_cursor(cursor[:-1] + ("a" if cursor[-1] != "a" else "b"), filters=filters)


def test_push_center_fast_sql_row_keeps_source_fields_and_delivery_semantics() -> None:
    item = _public_item(
        {
            "projection_id": "external_effect_job:42",
            "record_type": "external_effect_job",
            "source_record_id": 42,
            "effect_type": "wecom.contact.tag.mark",
            "raw_status": "succeeded",
            "effective_status": "succeeded",
            "queue_state": "terminal",
            "delivery_state": "not_applicable",
            "section": "tags",
            "business_type": "contact_tag",
            "business_id": "tag-42",
            "created_at": "2026-07-17T10:00:00+08:00",
            "payload_summary_json": {"token": "private-token", "tag_id": "tag-42"},
            "attempt_count": 1,
        }
    )

    assert item["source_record_id"] == 42
    assert item["display_id"] == "#42"
    assert item["effect_type"] == "wecom.contact.tag.mark"
    assert item["status"] == "succeeded"
    assert item["status_label"] == "执行成功"
    assert item["delivery_state"] == "not_applicable"
    assert item["payload_summary_json"]["token"] == "[redacted]"
    assert item["linked_record_counts"]["external_effect_jobs"] == 1
    assert item["linked_record_counts"]["external_effect_attempts"] == 1


class _FakeBroadcastJobAdapter(BroadcastJobAdapter):
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def list_jobs(self, filters: dict | None = None, *, limit: int = 1000) -> list[dict]:
        return list(self.rows[:limit])

    def get_job(self, job_id: int) -> dict | None:
        return next((row for row in self.rows if int(row["id"]) == int(job_id)), None)


class _CountingExternalAdapter:
    def __init__(self) -> None:
        self.list_calls = 0
        self.attempt_batch_calls = 0

    def list_jobs(self, filters: dict | None = None, *, limit: int = 1000) -> list:
        self.list_calls += 1
        return []

    def get_job(self, job_id: int):
        return None

    def list_attempts(self, job_id: int) -> list:
        raise AssertionError("push center projection must batch attempt reads")

    def list_attempts_for_jobs(self, job_ids: list[int]) -> dict[int, list]:
        self.attempt_batch_calls += 1
        return {job_id: [] for job_id in job_ids}


class _CountingBroadcastAdapter(_FakeBroadcastJobAdapter):
    def __init__(self) -> None:
        super().__init__([])
        self.list_calls = 0

    def list_jobs(self, filters: dict | None = None, *, limit: int = 1000) -> list[dict]:
        self.list_calls += 1
        return super().list_jobs(filters, limit=limit)


def _projection_repo(*, broadcast_rows: list[dict]) -> PushCenterRepository:
    return PushCenterRepository(service=PushCenterProjectionService(broadcast_adapter=_FakeBroadcastJobAdapter(broadcast_rows)))


def _context(trace_id: str = "trace-push-center", source_route: str = "/pytest/push-center") -> CommandContext:
    return CommandContext(actor_id="pytest", actor_type="system", request_id=trace_id, trace_id=trace_id, source_route=source_route)


def _plan_job(
    *,
    effect_type: str,
    business_type: str,
    business_id: str,
    target_type: str = "external_user",
    target_id: str = "wm_fixture_a",
    status: str = "queued",
    execution_mode: str = "execute",
    payload: dict | None = None,
    payload_summary: dict | None = None,
    trace_id: str = "trace-push-center",
    idempotency_key: str = "",
    execution_id: str = "",
) -> dict:
    return ExternalEffectService().plan_effect(
        effect_type=effect_type,
        adapter_name="wecom_private_message" if effect_type == WECOM_MESSAGE_PRIVATE_SEND else "outbound_webhook",
        operation="send" if effect_type == WECOM_MESSAGE_PRIVATE_SEND else "post",
        target_type=target_type,
        target_id=target_id,
        business_type=business_type,
        business_id=business_id,
        payload=payload or {"owner_userid": "HuangYouCan", "external_userids": [target_id], "token": "secret-token"},
        payload_summary=payload_summary or {"owner_userid": "HuangYouCan", "external_userid": target_id, "token": "secret-token"},
        context=_context(trace_id=trace_id),
        source_module="pytest.push_center",
        source_event_id=business_id,
        source_command_id=idempotency_key or business_id,
        risk_level="medium",
        execution_mode=execution_mode,
        status=status,
        idempotency_key=idempotency_key or f"push-center:{effect_type}:{business_id}:{target_id}",
        execution_id=execution_id,
    )


def test_section_mapper_routes_effect_types_by_business_type() -> None:
    reset_external_effect_fixture_state()
    ai_job = _plan_job(effect_type=WECOM_MESSAGE_PRIVATE_SEND, business_type="ai_assist_campaign", business_id="camp_1")
    private_job = _plan_job(effect_type=WECOM_MESSAGE_PRIVATE_SEND, business_type="private_broadcast", business_id="broadcast_1", target_id="wm_fixture_b")
    group_job = _plan_job(
        effect_type=WECOM_MESSAGE_GROUP_SEND,
        business_type="group_ops_plan",
        business_id="12",
        target_type="group_ops_webhook_event",
        target_id="17",
        payload={"owner_userid": "HuangYouCan", "webhook_key": "测试运营计划-ce2519", "chat_ids": ["chat_1"]},
        payload_summary={"owner_userid": "HuangYouCan", "webhook_key": "测试运营计划-ce2519", "chat_count": 1},
    )

    assert section_for_job(ai_job) == "ai_assist"
    assert section_for_job(private_job) == "private_broadcast"
    assert section_for_job(group_job) == "group_ops"
    assert WECOM_MESSAGE_GROUP_SEND in effect_types_for_section("group_ops")
    assert label_for_section("questionnaire") == "问卷外推"


def test_push_center_jobs_filters_and_payload_redaction(next_client: TestClient) -> None:
    reset_external_effect_fixture_state()
    _plan_job(effect_type=WECOM_MESSAGE_PRIVATE_SEND, business_type="ai_assist_campaign", business_id="camp_1", trace_id="trace-ai")
    _plan_job(
        effect_type=WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
        business_type="questionnaire",
        business_id="q_1",
        target_type="questionnaire_submission",
        target_id="sub_1",
        trace_id="trace-questionnaire",
        status="planned",
        execution_mode="shadow",
    )

    response = next_client.get("/api/admin/push-center/jobs?section=ai_assist&external_userid=wm_fixture_a")
    body = response.json()

    assert response.status_code == 200
    assert body["ok"] is True
    assert body["total"] == 1
    assert body["items"][0]["section"] == "ai_assist"
    assert body["items"][0]["business_id"] == "camp_1"
    assert body["items"][0]["payload_summary"]["token"] == "[redacted]"
    assert "payload_json" not in body["items"][0]

    planned = next_client.get("/api/admin/push-center/jobs?section=questionnaire&status=pending").json()
    assert planned["total"] == 1
    assert planned["items"][0]["status"] == "pending"
    assert planned["items"][0]["status_label"] == "待执行"
    assert planned["items"][0]["raw_status"] == "planned"
    assert {item["key"] for item in planned["status_definitions"]} == {
        "pending",
        "running",
        "succeeded",
        "sent",
        "simulated",
        "unknown_after_dispatch",
        "failed",
        "sent_with_shadow_warning",
        "shadow_failed_not_business_failed",
    }


def test_push_center_group_ops_shadow_failed_with_sent_broadcast_is_warning_not_failed() -> None:
    reset_external_effect_fixture_state()
    job = _plan_job(
        effect_type=GROUP_OPS_MESSAGE_LOOPBACK,
        business_type="group_ops_plan",
        business_id="11",
        target_type="group_ops_webhook_event",
        target_id="23",
        status="failed_terminal",
        trace_id="group-ops-legacy-bundle:11:23:daily-lesson",
        idempotency_key="group-ops-legacy-bundle:11:23:daily-lesson",
        payload_summary={
            "execution_id": "exe_group_ops_11_23",
            "plan_id": 11,
            "trigger_event_id": "23",
            "chat_count": 8,
            "webhook_key": "正式群运营计划测试-584571",
        },
        execution_id="exe_group_ops_11_23",
    )
    repo = build_external_effect_repository()
    job_obj = repo.get_job(job["id"])
    assert job_obj is not None
    repo.record_attempt(
        job=job_obj,
        status="failed_terminal",
        adapter_mode="execute",
        request_summary={"effect_type": GROUP_OPS_MESSAGE_LOOPBACK, "target_id": "23"},
        response_summary={"blocked": True, "execution_gate": "group_ops_loopback_requires_test_receiver", "real_external_call_executed": False},
        error_code="group_ops_loopback_requires_test_receiver",
        error_message="Webhook adapter execution is blocked by external effect execution gates.",
    )
    broadcast_rows = [
        {
            "id": 3642,
            "source_type": "workflow",
            "source_id": "11:webhook:23",
            "source_table": "automation_group_ops_plans",
            "scheduled_for": "2026-06-18T00:57:15Z",
            "batch_key": "",
            "business_domain": "",
            "channel": "wecom_customer_group",
            "target_kind": "chat_id",
            "failure_type": "",
            "status": "sent",
            "target_count": 0,
            "target_summary": "8 customer groups",
            "content_type": "text",
            "content_summary": "6月18日思考",
            "attempt_count": 1,
            "last_error": "",
            "outbound_task_id": 3925,
            "sent_count": 8,
            "failed_count": 0,
            "trace_id": "group_ops:11:webhook:23:2026-06-18T08:57:15.390408+08:00",
            "idempotency_key": "group_ops:11:webhook:23:2026-06-18T08:57:15.390408+08:00",
            "execution_id": "exe_group_ops_11_23",
            "metadata_json": {"execution_id": "exe_group_ops_11_23"},
            "created_by": "group_ops_webhook",
            "created_at": "2026-06-18T00:57:10Z",
            "updated_at": "2026-06-18T00:58:04Z",
            "claimed_at": "2026-06-18T00:58:01Z",
            "sent_at": "2026-06-18T00:58:04Z",
            "outbound_task_status": "created",
            "outbound_task_type": "broadcast_job/group_ops",
            "outbound_task_wecom_task_id": "msgbNXyCwAAv7rCQ6fHkZwegoawyRNWqQ",
            "outbound_task_response_payload": '{"result":{"errcode":0,"errmsg":"ok","msgid":"msgbNXyCwAAv7rCQ6fHkZwegoawyRNWqQ"},"ok":true,"side_effect_executed":true,"exact_target_verified":true}',
            "outbound_task_trace_id": "group_ops:11:webhook:23:2026-06-18T08:57:15.390408+08:00",
            "outbound_task_created_at": "2026-06-18T00:58:04Z",
        }
    ]
    projection_repo = _projection_repo(broadcast_rows=broadcast_rows)

    body = build_jobs_payload({"section": "group_ops"}, repository=projection_repo)
    item = body["items"][0]
    stats = build_stats_payload({"section": "group_ops"}, repository=projection_repo)
    detail = build_job_detail_payload(item["projection_id"], repository=projection_repo)
    reconciliation = build_job_reconciliation_payload(item["projection_id"], repository=projection_repo)

    assert item["effective_status"] == "sent_with_shadow_warning"
    assert item["status"] == "sent_with_shadow_warning"
    assert item["status_label"] == "已发送 · 影子链路异常"
    assert "linked_records" not in item
    assert item["linked_record_counts"] == {
        "external_effect_jobs": 1,
        "external_effect_attempts": 1,
        "broadcast_jobs": 1,
        "outbound_tasks": 1,
    }
    assert stats["counts"]["sent"] == 1
    assert stats["counts"]["failed"] == 0
    assert detail is not None
    assert len(detail["linked_records"]["external_effect_jobs"]) == 1
    assert len(detail["linked_records"]["external_effect_attempts"]) == 1
    assert len(detail["linked_records"]["broadcast_jobs"]) == 1
    assert detail["linked_records"]["outbound_tasks"][0]["response_payload"]["result"]["errcode"] == 0
    assert reconciliation is not None
    assert reconciliation["reconciliation"]["effective_status"] == "sent_with_shadow_warning"
    assert reconciliation["reconciliation"]["retryable"] is False
    assert reconciliation["reconciliation"]["operator_action_required"] is True
    assert reconciliation["reconciliation"]["next_action_label"] == "检查影子链路"
    assert "不要把它误判为业务发送失败" in reconciliation["reconciliation"]["business_explanation"]


def test_push_center_group_ops_shadow_failed_without_primary_is_not_business_failed() -> None:
    reset_external_effect_fixture_state()
    _plan_job(
        effect_type=GROUP_OPS_MESSAGE_LOOPBACK,
        business_type="group_ops_plan",
        business_id="11",
        target_type="group_ops_webhook_event",
        target_id="24",
        status="failed_terminal",
        trace_id="group-ops-legacy-bundle:11:24:daily-lesson",
        idempotency_key="group-ops-legacy-bundle:11:24:daily-lesson",
        payload_summary={"plan_id": 11, "trigger_event_id": "24", "chat_count": 1},
    )
    projection_repo = _projection_repo(broadcast_rows=[])

    body = build_jobs_payload({"section": "group_ops"}, repository=projection_repo)
    failed = build_jobs_payload({"section": "group_ops", "status": "failed"}, repository=projection_repo)
    stats = build_stats_payload({"section": "group_ops"}, repository=projection_repo)

    assert body["items"][0]["effective_status"] == "shadow_failed_not_business_failed"
    assert body["items"][0]["status_label"] == "影子链路失败，未发现主发送记录"
    assert failed["total"] == 0
    assert stats["counts"]["failed"] == 0
    assert stats["counts"]["shadow_warning"] == 1


def test_push_center_does_not_infer_parentage_from_matching_trace_or_idempotency() -> None:
    reset_external_effect_fixture_state()
    _plan_job(
        effect_type=WECOM_MESSAGE_PRIVATE_SEND,
        business_type="campaign",
        business_id="external-only",
        status="succeeded",
        execution_mode="execute",
        trace_id="shared-but-not-a-parent-key",
        idempotency_key="shared-but-not-a-parent-key",
    )
    projection_repo = _projection_repo(
        broadcast_rows=[
            {
                "id": 9001,
                "source_type": "campaign",
                "source_id": "broadcast-only",
                "status": "sent",
                "trace_id": "shared-but-not-a-parent-key",
                "idempotency_key": "shared-but-not-a-parent-key",
                "created_at": "2026-07-17T10:00:00Z",
                "updated_at": "2026-07-17T10:00:01Z",
                "sent_at": "2026-07-17T10:00:01Z",
            }
        ]
    )

    body = build_jobs_payload({}, repository=projection_repo)
    succeeded = build_jobs_payload({"status": "succeeded"}, repository=projection_repo)
    sent = build_jobs_payload({"status": "sent"}, repository=projection_repo)

    assert body["total"] == 2
    assert {item["effective_status"] for item in body["items"]} == {"succeeded", "sent"}
    assert succeeded["total"] == 1
    assert succeeded["items"][0]["effective_status"] == "succeeded"
    assert sent["total"] == 1
    assert sent["items"][0]["effective_status"] == "sent"
    assert all(
        item["linked_record_counts"]["external_effect_jobs"]
        + item["linked_record_counts"]["broadcast_jobs"]
        == 1
        for item in body["items"]
    )


def test_push_center_detail_includes_attempts_without_full_payload(next_client: TestClient) -> None:
    reset_external_effect_fixture_state()
    job = _plan_job(
        effect_type=AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK, business_type="ai_assist_campaign", business_id="camp_loop", status="blocked", execution_mode="shadow"
    )
    repo = build_external_effect_repository()
    job_obj = repo.get_job(job["id"])
    assert job_obj is not None
    repo.record_attempt(
        job=job_obj,
        status="blocked",
        adapter_mode="shadow",
        request_summary={"Authorization": "Bearer secret", "effect_type": job_obj.effect_type},
        response_summary={"access_token": "secret", "blocked": True},
        error_code="shadow_only",
        error_message="blocked by test",
    )

    response = next_client.get(f"/api/admin/push-center/jobs/{job['id']}")
    body = response.json()

    assert response.status_code == 200
    assert body["job"]["projection_id"] == f"external_effect_job:{job['id']}"
    assert body["job"]["source_record_id"] == job["id"]
    assert "payload_json" not in body["job"]
    assert body["attempts"][0]["request_summary"]["Authorization"] == "[redacted]"
    assert body["attempts"][0]["response_summary"]["access_token"] == "[redacted]"


def test_push_center_sections_stats_retry_cancel_auth(next_client: TestClient, monkeypatch) -> None:
    reset_external_effect_fixture_state()
    failed = _plan_job(
        effect_type=WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
        business_type="questionnaire",
        business_id="q_failed",
        target_type="questionnaire_submission",
        target_id="sub_failed",
        status="failed_retryable",
        execution_mode="execute",
        trace_id="trace-failed",
    )
    queued = _plan_job(
        effect_type=WECOM_MESSAGE_GROUP_SEND,
        business_type="group_ops_plan",
        business_id="12",
        target_type="group_ops_webhook_event",
        target_id="17",
        status="queued",
        execution_mode="execute",
        trace_id="trace-group",
        payload={"owner_userid": "HuangYouCan", "webhook_key": "测试运营计划-ce2519", "chat_ids": ["chat_1"]},
        payload_summary={"owner_userid": "HuangYouCan", "webhook_key": "测试运营计划-ce2519", "chat_count": 1},
    )
    tokens = install_admin_action_tokens(
        next_client,
        ("POST", "/api/admin/push-center/jobs/{job_id}/retry"),
        ("POST", "/api/admin/push-center/jobs/{job_id}/cancel"),
    )

    sections = next_client.get("/api/admin/push-center/sections").json()
    stats = next_client.get("/api/admin/push-center/stats").json()
    reconciliation = next_client.get(f"/api/admin/push-center/jobs/{failed['id']}/reconciliation")
    rejected = next_client.post(f"/api/admin/push-center/jobs/{failed['id']}/retry", json={})
    retried = next_client.post(
        f"/api/admin/push-center/jobs/{failed['id']}/retry",
        headers={"X-Admin-Action-Token": tokens[("POST", "/api/admin/push-center/jobs/{job_id}/retry")]},
        json={"reason": "人工确认安全重试", "expected_version": 1},
    )
    cancelled = next_client.post(
        f"/api/admin/push-center/jobs/{queued['id']}/cancel",
        headers={"X-Admin-Action-Token": tokens[("POST", "/api/admin/push-center/jobs/{job_id}/cancel")]},
        json={
            "expected_version": queued["row_version"],
            "actor": "push-center-operator",
        },
    )

    assert any(item["key"] == "questionnaire" and item["count"] == 1 for item in sections["sections"])
    assert stats["counts"]["failed"] == 1
    assert reconciliation.status_code == 200
    assert reconciliation.json()["reconciliation"]["effective_status"] == "failed"
    assert reconciliation.json()["reconciliation"]["retryable"] is True
    assert reconciliation.json()["reconciliation"]["operator_action_required"] is True
    assert reconciliation.json()["reconciliation"]["next_action_label"] == "重试"
    assert reconciliation.json()["reconciliation"]["linked_record_counts"]["external_effect_jobs"] == 1
    assert rejected.status_code == 401
    assert retried.status_code == 422
    assert retried.json()["missing_fields"] == ["actor"]
    assert cancelled.status_code == 422
    assert cancelled.json()["missing_fields"] == ["reason"]


def test_push_center_page_smoke(next_client: TestClient) -> None:
    reset_external_effect_fixture_state()
    _plan_job(
        effect_type=WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
        business_type="questionnaire",
        business_id="q_page",
        target_type="questionnaire_submission",
        target_id="sub_page",
    )

    response = next_client.get("/admin/push-center")

    assert response.status_code == 200
    assert "推送中心" in response.text
    assert 'id="statsGrid"' in response.text
    assert 'id="sectionTabs"' in response.text
    assert 'id="filterForm"' in response.text
    assert 'id="pushCenterTable"' in response.text
    assert 'data-execution-page="push-list"' in response.text
    assert 'id="detailModal"' not in response.text
    assert "push-center-modal" not in response.text
    assert 'class="push-center-header"' not in response.text
    assert "push-center-title" not in response.text
    assert 'href="#refresh"' in response.text
    assert 'href="#export"' in response.text
    assert ">查询</button>" in response.text
    assert "admin_execution_ui.css" in response.text
    assert "admin_execution_ui.js" in response.text
    assert "已计划" not in response.text
    assert 'id="legacyDeprecationsPanel"' not in response.text
    assert 'id="legacyDeprecationsList"' not in response.text
    assert "/api/admin/push-center/legacy-deprecations" not in response.text
    assert "旧链路下线状态" not in response.text
    assert "下次删除" not in response.text
    assert "/api/admin/push-center/stats" not in response.text
    assert "/api/admin/push-center/jobs" not in response.text
    assert "外部动作队列" not in response.text
    assert "payload_json" not in response.text
    assert "token" not in response.text.lower()
    assert "secret" not in response.text.lower()
    assert "Authorization" not in response.text
    assert "access_token" not in response.text
    assert "secret-token" not in response.text


def test_push_center_page_shell_does_not_query_projection(monkeypatch, next_client: TestClient) -> None:
    def fail_if_constructed(*_args, **_kwargs):
        raise AssertionError("push center page shell must not query the projection")

    monkeypatch.setattr(push_center_api, "PushCenterRepository", fail_if_constructed)

    response = next_client.get("/admin/push-center")

    assert response.status_code == 200
    assert 'id="pushCenterTable"' in response.text
    assert "/api/admin/push-center/stats" not in response.text
    assert "admin_execution_ui.js" in response.text


def test_push_center_frontend_uses_one_jobs_request_without_input_refresh() -> None:
    source = (
        Path("aicrm_next/frontend_compat/static/admin_console/admin_execution_ui.js")
        .read_text(encoding="utf-8")
    )

    assert source.count("/api/admin/push-center/jobs?") == 1
    assert "/api/admin/push-center/stats" not in source
    assert 'addEventListener("input"' not in source
    assert "setInterval" not in source
    assert "window.prompt" not in source
    assert "requestJson" in source


def test_push_center_legacy_query_redirects_to_level_two_detail(next_client: TestClient) -> None:
    redirect = next_client.get(
        "/admin/push-center?job_id=external_effect_job:42",
        follow_redirects=False,
    )

    assert redirect.status_code == 303
    assert redirect.headers["location"] == "/admin/push-center/jobs/external_effect_job:42"

    detail = next_client.get("/admin/push-center/jobs/external_effect_job:42")
    assert detail.status_code == 200
    assert 'data-execution-page="push-detail"' in detail.text
    assert 'data-detail-id="external_effect_job:42"' in detail.text
    assert 'id="detailModal"' not in detail.text


def test_push_center_list_and_stats_reuse_one_projection_snapshot() -> None:
    external = _CountingExternalAdapter()
    broadcast = _CountingBroadcastAdapter()
    repository = PushCenterRepository(service=PushCenterProjectionService(external_adapter=external, broadcast_adapter=broadcast))

    jobs = build_jobs_payload({"limit": 1}, repository=repository)

    assert jobs["ok"] is True
    assert external.list_calls == 1
    assert external.attempt_batch_calls == 1
    assert broadcast.list_calls == 1

    stats = build_stats_payload({}, repository=repository)

    assert stats["ok"] is True
    assert external.list_calls == 2
    assert external.attempt_batch_calls == 2
    assert broadcast.list_calls == 2


def test_push_center_counts_cover_records_beyond_page_limit(monkeypatch) -> None:
    service = PushCenterProjectionService()
    records = [
        {
            "id": f"external_effect_job:{index}",
            "effective_status": "sent",
            "section": "other",
            "created_at": f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}+00:00",
        }
        for index in range(250)
    ]
    monkeypatch.setattr(service, "_matching_projections", lambda _filters: records)

    page, total = service.list_projections({}, limit=200)
    counts = service.counts({})

    assert len(page) == 200
    assert total == 250
    assert counts["total"] == 250
    assert counts["sent"] == 250
    assert counts["by_section"] == {"other": 250}


def test_questionnaire_default_external_push_is_queue_first(client: TestClient, monkeypatch) -> None:
    from aicrm_next.questionnaire.repo import build_questionnaire_repository

    authorize_wechat_client(client, {"external_userid": "wx_ext_001"})
    monkeypatch.delenv("AICRM_QUESTIONNAIRE_EXTERNAL_PUSH_MODE", raising=False)
    repo = build_questionnaire_repository()
    existing = repo.get_questionnaire_by_slug("hxc-activation-v1")
    questionnaire = repo.save_questionnaire(
        {
            "slug": "hxc-activation-v1",
            "name": "黄小璨激活问卷",
            "title": "黄小璨激活问卷",
            "enabled": True,
            "external_push_config": {"enabled": True, "webhook_url": "https://hooks.example.com/should-not-send"},
            "questions": [{"id": "q_mobile", "type": "mobile", "title": "手机号", "required": True, "options": []}],
        },
        questionnaire_id=int(existing["id"]) if existing else None,
    )
    phone_question_id = str(questionnaire["questions"][0].get("id") or "q_mobile")

    response = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={
            "answers": {phone_question_id: "13770938686"},
            "identity": {"external_userid": "wx_ext_001"},
        },
        headers={"Idempotency-Key": "push-center-questionnaire-default-queue"},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["external_push_mode"] == "queue"
    assert body["external_push"]["status"] == "queued"
    assert body["external_push"]["attempted"] is False
    assert body["real_external_call_executed"] is False
    assert body["durable_continuation_queued"] is True
    assert body["external_effect_job_status"] == "not_planned"
    assert body["external_effect_job"] is None


def test_group_ops_default_webhook_uses_external_effect_not_legacy_gateway(
    group_ops_api_client,  # noqa: F811
    monkeypatch,
) -> None:
    monkeypatch.delenv("AICRM_GROUP_OPS_OUTBOUND_MODE", raising=False)
    monkeypatch.delenv("AICRM_GROUP_OPS_EXTERNAL_EFFECT_SEND_MODE", raising=False)
    response = group_ops_api_client.post(
        "/api/automation/group-ops/webhooks/daily-lesson-8f3a",
        headers={"Authorization": "Bearer fixture-webhook-token"},
        json={
            "idempotency_key": "push-center-default-group-ops-external-effect",
            "send_mode": "queued",
            "content": {"text": "synthetic group ops default external effect", "attachments": []},
        },
    )
    body = response.json()

    assert response.status_code == 202
    assert body["outbound_mode"] == "external_effect"
    assert body["legacy_outbound_disabled"] is True
    assert body["external_effect_required"] is True
    assert body["broadcast_job_ids"] == []
    assert body["legacy_broadcast_job_ids"] == []
    assert body["external_effect_job_ids"]
