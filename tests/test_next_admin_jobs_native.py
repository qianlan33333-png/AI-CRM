from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from aicrm_next.admin_jobs.notification_settings import (
    FeishuWebhookValidationError,
    build_broadcast_job_hourly_report_message,
    build_hourly_report_key,
    get_broadcast_job_hourly_summary,
    get_previous_hour_window,
    mask_webhook_url,
    send_broadcast_job_hourly_feishu_report,
    validate_feishu_webhook_url,
)
from aicrm_next.admin_jobs.repository import build_admin_jobs_repository
from aicrm_next.main import create_app
from tests.admin_auth_test_helpers import install_admin_action_tokens, install_admin_session


def _client(monkeypatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    monkeypatch.setenv("SECRET_KEY", "next-admin-jobs-test")
    client = TestClient(create_app(), raise_server_exceptions=False)
    install_admin_session(client, "super_admin")
    return client


def _jobs_action_token(client: TestClient, method: str, path: str) -> str:
    return install_admin_action_tokens(client, (method, path))[(method.upper(), path)]


def test_admin_jobs_page_is_native_jobs_console(monkeypatch):
    client = _client(monkeypatch)

    response = client.get("/admin/jobs")
    html = response.text

    assert response.status_code == 200
    assert "同步与任务总览" in html
    for text in ["聊天同步", "回调状态", "消息批次", "待处理作业", "Webhook 投递", "群发队列"]:
        assert text in html
    assert "数据读取状态" not in html
    assert "production_unavailable" not in html
    assert "degraded" not in html


def test_admin_jobs_deferred_runner_is_retired(monkeypatch):
    client = _client(monkeypatch)

    page = client.get("/admin/jobs?tab=deferred")
    html = page.text
    token = _jobs_action_token(client, "POST", "/api/admin/jobs/deferred-jobs/run")

    assert page.status_code == 200
    assert "待处理作业执行已退场" in html
    assert "手动执行待处理作业" not in html
    assert 'name="action" value="run-deferred-jobs"' not in html

    response = client.post(
        "/api/admin/jobs/deferred-jobs/run",
        json={"confirm": True, "admin_action_token": token, "operator": "tester-deferred"},
    )

    assert response.status_code == 409
    assert response.json()["ok"] is False
    assert response.json()["legacy_outbound_disabled"] is True
    assert response.json()["error"] == "legacy_deferred_jobs_runner_disabled"
    assert response.json()["real_external_call_executed"] is False


def test_admin_jobs_legacy_actions_return_disabled_payload(monkeypatch):
    _client(monkeypatch)

    from aicrm_next.admin_jobs.application import execute_jobs_action

    deferred = execute_jobs_action(action="run-deferred-jobs", form={"confirm": True}, operator="pytest")
    retry_one = execute_jobs_action(action="retry-webhook-delivery", form={"confirm": True, "delivery_id": 1}, operator="pytest")
    retry_due = execute_jobs_action(action="run-webhook-retries", form={"confirm": True}, operator="pytest")

    assert deferred["ok"] is False
    assert deferred["error"] == "legacy_deferred_jobs_runner_disabled"
    assert retry_one["ok"] is False
    assert retry_one["error"] == "legacy_webhook_retry_disabled"
    assert retry_due["ok"] is False
    assert retry_due["error"] == "legacy_webhook_retry_disabled"


def test_admin_jobs_webhooks_tab_is_read_only_for_legacy_retries(monkeypatch):
    client = _client(monkeypatch)

    response = client.get("/admin/jobs?tab=webhooks&webhook_status=retry_scheduled")
    html = response.text
    tokens = install_admin_action_tokens(
        client,
        ("POST", "/api/admin/jobs/webhook-deliveries/run"),
        ("POST", "/api/admin/jobs/webhook-deliveries/{delivery_id}/retry"),
    )
    run_token = tokens[("POST", "/api/admin/jobs/webhook-deliveries/run")]
    retry_token = tokens[("POST", "/api/admin/jobs/webhook-deliveries/{delivery_id}/retry")]

    assert response.status_code == 200
    assert "Webhook 投递状态" in html
    assert "Webhook 重试执行已退场" in html
    assert "执行到期重试" not in html
    assert 'name="action" value="run-webhook-retries"' not in html
    assert 'name="action" value="retry-webhook-delivery"' not in html
    assert "重试已退场" in html
    assert "ext-3" in html
    assert "Payload 摘要" in html

    run_due = client.post(
        "/api/admin/jobs/webhook-deliveries/run",
        json={"confirm": True, "admin_action_token": run_token, "operator": "tester-webhook"},
    )
    assert run_due.status_code == 409
    assert run_due.json()["ok"] is False
    assert run_due.json()["legacy_outbound_disabled"] is True
    assert run_due.json()["error"] == "legacy_webhook_retry_disabled"
    assert run_due.json()["real_external_call_executed"] is False

    retry = client.post(
        "/api/admin/jobs/webhook-deliveries/2/retry",
        json={"confirm": True, "admin_action_token": retry_token, "operator": "tester-webhook"},
    )
    assert retry.status_code == 409
    assert retry.json()["ok"] is False
    assert retry.json()["legacy_outbound_disabled"] is True
    assert retry.json()["external_effect_required"] is True
    assert retry.json()["migration_target"] == "external_effect_queue"
    assert retry.json()["push_center_url"] == "/admin/push-center"
    assert retry.json()["real_external_call_executed"] is False


def test_admin_broadcast_jobs_page_filters_and_actions(monkeypatch):
    client = _client(monkeypatch)

    page = client.get("/admin/broadcast-jobs?status=waiting_approval&source_type=campaign")
    html = page.text
    tokens = install_admin_action_tokens(
        client,
        ("POST", "/api/admin/broadcast-jobs/{job_id}/approve"),
        ("POST", "/api/admin/broadcast-jobs/{job_id}/cancel"),
    )
    approve_token = tokens[("POST", "/api/admin/broadcast-jobs/{job_id}/approve")]
    cancel_token = tokens[("POST", "/api/admin/broadcast-jobs/{job_id}/cancel")]

    assert page.status_code == 200
    assert "群发任务队列" in html
    assert "审批通过" in html
    assert "取消" in html
    assert "campaign" in html
    assert "排队中内容" not in html
    for label in ["发送中", "模拟执行", "失败可重试", "失败不可重试", "执行受阻", "结果待核对"]:
        assert label in html
    assert "admin_execution_ui.js" in html
    assert "broadcastJobAction" not in html

    filtered = client.get(
        "/api/admin/broadcast-jobs?status=waiting_approval&source_type=campaign"
    ).json()
    assert filtered["total"] == 1
    assert filtered["counts"]["total_count"] == 1
    assert filtered["counts"]["waiting_approval_count"] == 1
    assert filtered["counts"]["queued_count"] == 0

    approve = client.post(
        "/api/admin/broadcast-jobs/1/approve",
        json={
            "admin_action_token": approve_token,
            "operator": "spoofed-browser-actor",
            "reason": "审批内容与白名单已核对",
            "expected_version": 1,
        },
    )
    assert approve.status_code == 200
    assert approve.json()["job"]["status"] == "queued"
    assert approve.json()["job"]["approved_by"] == "admin-user:test"

    cancel = client.post(
        "/api/admin/broadcast-jobs/2/cancel",
        json={
            "admin_action_token": cancel_token,
            "operator": "spoofed-browser-actor",
            "reason": "manual stop",
            "expected_version": 1,
        },
    )
    assert cancel.status_code == 200
    assert cancel.json()["job"]["status"] == "cancelled"
    assert cancel.json()["job"]["cancelled_by"] == "admin-user:test"
    assert cancel.json()["job"]["cancel_reason"] == "manual stop"

    sent_cancel = client.post(
        "/api/admin/broadcast-jobs/3/cancel",
        json={
            "admin_action_token": cancel_token,
            "reason": "验证终态不可取消",
            "expected_version": 1,
        },
    )
    assert sent_cancel.status_code == 409
    assert "not cancelable" in sent_cancel.json()["error"]


def test_broadcast_job_level_two_detail_is_redacted_and_query_compatible(monkeypatch):
    client = _client(monkeypatch)
    repo = build_admin_jobs_repository()
    job = repo.broadcast_jobs[0]
    job.update(
        {
            "source_id": "private-source-secret",
            "target_unionids_json": ["unionid-secret"],
            "content_payload": {"phone": "13800138000", "token": "secret-token"},
            "metadata_json": {"webhook_url": "https://example.test/hook/secret-token"},
            "idempotency_key": "idempotency-secret",
            "content_summary": "发送给 13800138000",
        }
    )
    repo.messages[1] = [
        {
            "message_id": 99 + index,
            "send_time": "2026-04-02 12:41:00",
            "chat_type": "private",
            "external_userid": "wm-secret-user",
            "chat_id": "chat-secret",
            "content": "private message secret-token",
            "msgtype": "broadcast.dispatched",
        }
        for index in range(150)
    ]

    redirect = client.get("/admin/broadcast-jobs?job_id=1", follow_redirects=False)
    assert redirect.status_code == 303
    assert redirect.headers["location"] == "/admin/broadcast-jobs/1"

    page = client.get("/admin/broadcast-jobs/1")
    assert page.status_code == 200
    assert 'data-execution-page="broadcast-detail"' in page.text
    assert 'data-detail-id="1"' in page.text

    response = client.get("/api/admin/broadcast-jobs/1")
    assert response.status_code == 200
    body = response.json()
    serialized = str(body)
    assert body["ok"] is True
    assert body["route_owner"] == "ai_crm_next"
    assert body["real_external_call_executed"] is False
    assert body["push_center_url"] == "/admin/push-center/jobs/broadcast_job:1"
    assert body["linked_record_counts"]["broadcast_job_events"] == 150
    assert len(body["events"]) == 100
    assert body["events_truncated"] is True
    assert body["events"][0]["has_external_target"] is True
    assert body["events"][0]["has_content"] is True
    for forbidden in [
        "private-source-secret",
        "unionid-secret",
        "13800138000",
        "secret-token",
        "wm-secret-user",
        "chat-secret",
        "private message",
        "idempotency-secret",
        "target_unionids_json",
        "content_payload",
        "metadata_json",
        "webhook_url",
    ]:
        assert forbidden not in serialized

    listing = client.get("/api/admin/broadcast-jobs").json()
    assert listing["total"] == 3
    assert listing["route_owner"] == "ai_crm_next"
    assert {item["key"] for item in listing["status_options"]} >= {
        "dispatching",
        "simulated",
        "failed_retryable",
        "failed_terminal",
        "blocked",
        "unknown_after_dispatch",
    }


def test_broadcast_queue_feishu_settings_page_module(monkeypatch):
    client = _client(monkeypatch)

    response = client.get("/admin/broadcast-jobs")
    html = response.text

    assert response.status_code == 200
    assert "飞书监控配置" in html
    assert "配置后，系统会每小时统计上一小时的群发任务" in html
    assert "data-feishu-open" in html
    assert "data-feishu-overlay" in html
    assert "data-feishu-webhook-input" in html
    assert "data-feishu-save" in html
    assert "data-feishu-validate" in html
    assert "Outbound Task" not in html
    assert "Trace" not in html
    assert "Campaign" not in html
    assert "Workflow" not in html
    assert "+08:00" not in html


def test_mask_webhook_url_safely_masks_feishu_and_lark_tokens():
    feishu = "https://open.feishu.cn/open-apis/bot/v2/hook/secret-token-abcd"
    lark = "https://open.larksuite.com/open-apis/bot/v2/hook/lark-secret-7890"

    assert mask_webhook_url(feishu) == "https://open.feishu.cn/open-apis/bot/v2/hook/****abcd"
    assert mask_webhook_url(lark) == "https://open.larksuite.com/open-apis/bot/v2/hook/****7890"
    assert mask_webhook_url("") is None
    assert mask_webhook_url("not a url") is None
    assert "secret-token" not in (mask_webhook_url(feishu) or "")
    assert "lark-secret" not in (mask_webhook_url(lark) or "")


def test_validate_feishu_webhook_url_accepts_only_official_https_hooks():
    for url in (
        "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
        "https://open.larksuite.com/open-apis/bot/v2/hook/xxx",
    ):
        validate_feishu_webhook_url(url)

    rejected = (
        "http://open.feishu.cn/open-apis/bot/v2/hook/xxx",
        "https://example.com/open-apis/bot/v2/hook/xxx",
        "https://localhost/open-apis/bot/v2/hook/xxx",
        "https://127.0.0.1/open-apis/bot/v2/hook/xxx",
        "https://open.feishu.cn/open-apis/bot/v2/hook/",
        "",
        "not a url",
    )
    for url in rejected:
        with pytest.raises(FeishuWebhookValidationError):
            validate_feishu_webhook_url(url)


def test_broadcast_queue_feishu_settings_api_masks_saves_and_validates(monkeypatch):
    client = _client(monkeypatch)
    tokens = install_admin_action_tokens(
        client,
        ("PUT", "/api/admin/broadcast-jobs/notification-settings/feishu"),
        ("POST", "/api/admin/broadcast-jobs/notification-settings/feishu/validate"),
    )
    save_token = tokens[("PUT", "/api/admin/broadcast-jobs/notification-settings/feishu")]
    validate_token = tokens[("POST", "/api/admin/broadcast-jobs/notification-settings/feishu/validate")]
    webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/secret-token-abcd"

    no_token = client.put(
        "/api/admin/broadcast-jobs/notification-settings/feishu",
        json={"enabled": True, "webhookUrl": webhook},
    )
    assert no_token.status_code == 401

    unconfigured = client.get(
        "/api/admin/broadcast-jobs/notification-settings/feishu",
    )
    assert unconfigured.status_code == 200
    assert unconfigured.json()["validationStatus"] == "unconfigured"
    assert "secret-token" not in unconfigured.text

    saved = client.put(
        "/api/admin/broadcast-jobs/notification-settings/feishu",
        headers={"X-Admin-Action-Token": save_token},
        json={"enabled": True, "webhookUrl": webhook, "admin_action_token": save_token},
    )
    saved_payload = saved.json()
    assert saved.status_code == 200
    assert saved_payload["validationStatus"] == "unverified"
    assert saved_payload["webhookMasked"] == "https://open.feishu.cn/open-apis/bot/v2/hook/****abcd"
    assert "secret-token" not in saved.text

    captured: dict[str, str] = {}

    def fake_send(url: str, text: str) -> dict[str, object]:
        captured["url"] = url
        captured["text"] = text
        return {"ok": True}

    monkeypatch.setattr("aicrm_next.admin_jobs.notification_settings.send_feishu_webhook_message", fake_send)
    validated = client.post(
        "/api/admin/broadcast-jobs/notification-settings/feishu/validate",
        headers={"X-Admin-Action-Token": validate_token},
        json={"enabled": True, "webhookUrl": webhook, "admin_action_token": validate_token},
    )
    assert validated.status_code == 202
    assert validated.json()["ok"] is True
    assert validated.json()["validationStatus"] == "unverified"
    assert validated.json()["webhookMasked"].endswith("****abcd")
    assert validated.json()["accepted"] is True
    assert validated.json()["externalEffectJobId"]
    assert validated.json()["statusUrl"].endswith(
        f"external_effect_job:{validated.json()['externalEffectJobId']}"
    )
    assert captured == {}
    assert "secret-token" not in validated.text

    get_after_validate = client.get(
        "/api/admin/broadcast-jobs/notification-settings/feishu",
    )
    assert get_after_validate.json()["validationStatus"] == "unverified"
    assert "secret-token" not in get_after_validate.text


def test_broadcast_queue_feishu_validate_failure_does_not_leak_webhook(monkeypatch):
    client = _client(monkeypatch)
    token = _jobs_action_token(client, "POST", "/api/admin/broadcast-jobs/notification-settings/feishu/validate")
    webhook = "https://open.larksuite.com/open-apis/bot/v2/hook/top-secret-7890"

    monkeypatch.setattr(
        "aicrm_next.admin_jobs.notification_settings.send_feishu_webhook_message",
        lambda url, text: {"ok": False, "raw": {"url": url, "body": "large external body"}},
    )
    response = client.post(
        "/api/admin/broadcast-jobs/notification-settings/feishu/validate",
        headers={"X-Admin-Action-Token": token},
        json={"enabled": False, "webhookUrl": webhook, "admin_action_token": token},
    )

    assert response.status_code == 202
    assert response.json()["ok"] is True
    assert response.json()["validationStatus"] == "unverified"
    assert response.json()["accepted"] is True
    assert response.json()["externalEffectJobId"]
    assert "top-secret" not in response.text
    assert "large external body" not in response.text

    setting = client.get(
        "/api/admin/broadcast-jobs/notification-settings/feishu",
    ).json()
    assert setting["validationStatus"] == "unverified"
    assert setting["enabled"] is False
    assert "top-secret" not in str(setting)


def test_broadcast_queue_hourly_window_uses_previous_full_shanghai_hour():
    tz = ZoneInfo("Asia/Shanghai")
    window = get_previous_hour_window(now=datetime(2026, 5, 27, 14, 5, 12, 345000, tzinfo=tz))

    assert window["windowStart"] == datetime(2026, 5, 27, 13, 0, 0, 0, tzinfo=tz)
    assert window["windowEnd"] == datetime(2026, 5, 27, 14, 0, 0, 0, tzinfo=tz)
    assert window["label"] == "2026-05-27 13:00 - 14:00"
    assert window["windowStart"].minute == 0
    assert window["windowStart"].second == 0
    assert window["windowStart"].microsecond == 0


def test_broadcast_queue_hourly_summary_counts_window_by_scheduled_for(monkeypatch):
    _client(monkeypatch)
    repo = build_admin_jobs_repository()
    tz = ZoneInfo("Asia/Shanghai")
    start = datetime(2026, 5, 27, 13, 0, tzinfo=tz)
    end = datetime(2026, 5, 27, 14, 0, tzinfo=tz)
    repo.broadcast_jobs = [
        {"id": 101, "scheduled_for": start, "status": "sent"},
        {"id": 102, "scheduled_for": start + timedelta(minutes=15), "status": "failed"},
        {"id": 103, "scheduled_for": end - timedelta(seconds=1), "status": "queued"},
        {"id": 104, "scheduled_for": start - timedelta(seconds=1), "status": "sent"},
        {"id": 105, "scheduled_for": end, "status": "failed"},
    ]

    summary = get_broadcast_job_hourly_summary(window_start=start, window_end=end, repo=repo)

    assert summary["totalJobs"] == 3
    assert summary["successJobs"] == 1
    assert summary["failedJobs"] == 1
    assert summary["pendingJobs"] == 1


def test_broadcast_queue_hourly_report_message_and_key_are_count_only():
    tz = ZoneInfo("Asia/Shanghai")
    start = datetime(2026, 5, 27, 13, 0, tzinfo=tz)
    end = datetime(2026, 5, 27, 14, 0, tzinfo=tz)

    message = build_broadcast_job_hourly_report_message(
        window_start=start,
        window_end=end,
        total_jobs=18,
        success_jobs=16,
        failed_jobs=2,
    )

    assert message == "【群发队列小时报】\n统计窗口：2026-05-27 13:00 - 14:00\n\n任务总数：18\n成功：16\n失败：2"
    assert "trace" not in message.lower()
    assert "webhook" not in message.lower()
    assert build_hourly_report_key(channel="feishu", window_start=start) == "broadcast_jobs:feishu:2026-05-27T13:00:00+08:00"


def test_broadcast_queue_hourly_report_skips_when_config_missing_disabled_or_unverified(monkeypatch):
    _client(monkeypatch)
    repo = build_admin_jobs_repository()
    webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/hourly-secret-abcd"

    assert send_broadcast_job_hourly_feishu_report(repo=repo)["status"] == "skipped_no_config"

    repo.upsert_broadcast_notification_setting(
        channel="feishu",
        enabled=False,
        webhook_url=webhook,
        validation_status="valid",
        validated_at=datetime.now(tz=ZoneInfo("Asia/Shanghai")),
        last_validation_error=None,
    )
    assert send_broadcast_job_hourly_feishu_report(repo=repo)["status"] == "skipped_disabled"

    repo.upsert_broadcast_notification_setting(
        channel="feishu",
        enabled=True,
        webhook_url=webhook,
        validation_status="unverified",
        validated_at=None,
        last_validation_error=None,
    )
    assert send_broadcast_job_hourly_feishu_report(repo=repo)["status"] == "skipped_unverified"


def test_broadcast_queue_hourly_report_skips_no_jobs_sends_once_and_records_failure(monkeypatch):
    _client(monkeypatch)
    repo = build_admin_jobs_repository()
    tz = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 5, 27, 14, 5, tzinfo=tz)
    window = get_previous_hour_window(now=now)
    webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/hourly-secret-abcd"
    repo.upsert_broadcast_notification_setting(
        channel="feishu",
        enabled=True,
        webhook_url=webhook,
        validation_status="valid",
        validated_at=now,
        last_validation_error=None,
    )
    repo.broadcast_jobs = []
    sent_messages: list[dict[str, str]] = []

    def fake_send(url: str, text: str) -> dict[str, object]:
        sent_messages.append({"url": url, "text": text})
        return {"ok": True}

    no_jobs = send_broadcast_job_hourly_feishu_report(now=now, repo=repo, sender=fake_send)
    assert no_jobs == {"status": "skipped_no_jobs", "summary": {"totalJobs": 0, "successJobs": 0, "failedJobs": 0}}
    assert sent_messages == []

    repo.broadcast_jobs = [
        {"id": 201, "scheduled_for": window["windowStart"], "status": "sent"},
        {"id": 202, "scheduled_for": window["windowStart"] + timedelta(minutes=10), "status": "failed"},
    ]
    first = send_broadcast_job_hourly_feishu_report(now=now, repo=repo, sender=fake_send)
    duplicate = send_broadcast_job_hourly_feishu_report(now=now, repo=repo, sender=fake_send)
    assert first == {"status": "sent", "summary": {"totalJobs": 2, "successJobs": 1, "failedJobs": 1}}
    assert duplicate == {"status": "skipped_duplicate", "summary": {"totalJobs": 2, "successJobs": 1, "failedJobs": 1}}
    assert len(sent_messages) == 1
    assert sent_messages[0]["url"] == webhook
    assert "任务总数：2" in sent_messages[0]["text"]

    next_now = now + timedelta(hours=1)
    next_window = get_previous_hour_window(now=next_now)
    repo.broadcast_jobs = [{"id": 203, "scheduled_for": next_window["windowStart"], "status": "failed"}]

    def failing_send(url: str, text: str) -> dict[str, object]:
        return {"ok": False, "raw": {"webhook": url, "detail": "external failure body"}}

    failed = send_broadcast_job_hourly_feishu_report(now=next_now, repo=repo, sender=failing_send)
    report_key = build_hourly_report_key(channel="feishu", window_start=next_window["windowStart"])
    assert failed["status"] == "failed"
    assert failed["summary"] == {"totalJobs": 1, "successJobs": 0, "failedJobs": 1}
    assert "hourly-secret" not in str(failed)
    assert "external failure body" not in str(failed)
    assert repo.broadcast_hourly_reports[report_key]["status"] == "failed"
    assert "hourly-secret" not in str(repo.broadcast_hourly_reports[report_key]["error_message"])


def test_broadcast_queue_hourly_report_does_not_mark_queue_acceptance_as_sent(monkeypatch):
    _client(monkeypatch)
    repo = build_admin_jobs_repository()
    now = datetime(2026, 5, 27, 14, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
    window = get_previous_hour_window(now=now)
    repo.upsert_broadcast_notification_setting(
        channel="feishu",
        enabled=True,
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/hourly-secret-abcd",
        validation_status="valid",
        validated_at=now,
        last_validation_error=None,
    )
    repo.broadcast_jobs = [
        {"id": 204, "scheduled_for": window["windowStart"], "status": "sent"}
    ]

    result = send_broadcast_job_hourly_feishu_report(
        now=now,
        repo=repo,
        sender=lambda _url, _text: {
            "ok": True,
            "queued": True,
            "external_effect_job_id": 77,
            "real_external_call_executed": False,
        },
    )
    report_key = build_hourly_report_key(
        channel="feishu",
        window_start=window["windowStart"],
    )

    assert result == {
        "status": "queued",
        "summary": {"totalJobs": 1, "successJobs": 1, "failedJobs": 0},
        "externalEffectJobId": 77,
    }
    assert repo.broadcast_hourly_reports[report_key]["status"] == "pending"


def test_broadcast_queue_hourly_report_run_api_requires_route_bound_action_grant(monkeypatch):
    client = _client(monkeypatch)

    denied = client.post("/api/admin/broadcast-jobs/feishu-hourly-report/run")
    assert denied.status_code == 401

    monkeypatch.setattr(
        "aicrm_next.admin_jobs.routes.send_broadcast_job_hourly_feishu_report",
        lambda: {"status": "sent", "summary": {"totalJobs": 1, "successJobs": 1, "failedJobs": 0}},
    )
    token = _jobs_action_token(client, "POST", "/api/admin/broadcast-jobs/feishu-hourly-report/run")
    authorized = client.post(
        "/api/admin/broadcast-jobs/feishu-hourly-report/run",
        headers={"X-Admin-Action-Token": token},
    )
    assert authorized.status_code == 200
    assert authorized.json() == {"ok": True, "status": "sent", "summary": {"totalJobs": 1, "successJobs": 1, "failedJobs": 0}}


def test_broadcast_queue_hourly_report_run_api_real_no_jobs_duplicate_and_no_webhook_leak(monkeypatch):
    client = _client(monkeypatch)
    token = _jobs_action_token(client, "POST", "/api/admin/broadcast-jobs/feishu-hourly-report/run")
    repo = build_admin_jobs_repository()
    webhook = "https://open.larksuite.com/open-apis/bot/v2/hook/api-secret-7890"
    repo.upsert_broadcast_notification_setting(
        channel="feishu",
        enabled=True,
        webhook_url=webhook,
        validation_status="valid",
        validated_at=datetime.now(tz=ZoneInfo("Asia/Shanghai")),
        last_validation_error=None,
    )
    repo.broadcast_jobs = []

    no_jobs = client.post(
        "/api/admin/broadcast-jobs/feishu-hourly-report/run",
        headers={"X-Admin-Action-Token": token},
        json={"admin_action_token": token},
    )
    assert no_jobs.status_code == 200
    assert no_jobs.json()["status"] == "skipped_no_jobs"
    assert "api-secret" not in no_jobs.text

    now_window = get_previous_hour_window()
    repo.broadcast_jobs = [{"id": 301, "scheduled_for": now_window["windowStart"], "status": "sent"}]
    calls: list[str] = []
    monkeypatch.setattr(
        "aicrm_next.admin_jobs.notification_settings.send_feishu_webhook_message",
        lambda url, text: calls.append(url) or {"ok": True},
    )
    sent = client.post(
        "/api/admin/broadcast-jobs/feishu-hourly-report/run",
        headers={"X-Admin-Action-Token": token},
        json={"admin_action_token": token},
    )
    duplicate = client.post(
        "/api/admin/broadcast-jobs/feishu-hourly-report/run",
        headers={"X-Admin-Action-Token": token},
        json={"admin_action_token": token},
    )
    assert sent.json()["status"] == "sent"
    assert duplicate.json()["status"] == "skipped_duplicate"
    assert calls == [webhook]
    assert "api-secret" not in sent.text
    assert "api-secret" not in duplicate.text


def test_admin_read_model_count_uses_identifier_not_percent_i(monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    from aicrm_next.admin_read_model.repo import PostgresAdminReadRepository

    executed: list[object] = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, query, params=()):
            executed.append(query)
            assert "%I" not in str(query)
            self._row = {"table_oid": "sync_runs"} if "to_regclass" in str(query) else {"count": 7}

        def fetchone(self):
            return self._row

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: Connection())

    assert PostgresAdminReadRepository().count("sync_runs") == 7
    assert PostgresAdminReadRepository().count("outbound_webhook_deliveries") == 7
    assert PostgresAdminReadRepository().count("broadcast_jobs") == 7
