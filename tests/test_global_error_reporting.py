from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from aicrm_next.platform.platform_foundation.error_reporting.reporter import (
    ErrorReportEvent,
    ErrorReportingConfig,
    ErrorReportingLogHandler,
    FeishuErrorReporter,
    load_error_reporting_config,
    report_failed_result,
)
from aicrm_next.platform.shared.errors import ContractError
from aicrm_next.platform.shared.outbound_https.transport import HttpsTransportResponse


@dataclass
class _FakeTransport:
    response: HttpsTransportResponse = HttpsTransportResponse(
        status_code=200,
        text='{"code":0,"msg":"success"}',
    )

    def __post_init__(self) -> None:
        self.calls: list[dict] = []

    def post(self, target, *, body, headers, timeout):
        self.calls.append(
            {
                "target": target,
                "body": bytes(body),
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        return self.response


class _CollectingReporter:
    def __init__(self) -> None:
        self.events: list[ErrorReportEvent] = []

    def report(self, event: ErrorReportEvent):
        self.events.append(event)
        return None


def _config(**overrides) -> ErrorReportingConfig:
    values = {
        "enabled": True,
        "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/test-token",
        "environment": "test",
        "timeout_seconds": 1.25,
        "dedupe_seconds": 300.0,
        "max_per_minute": 20,
    }
    values.update(overrides)
    return ErrorReportingConfig(**values)


def _resolver(_hostname: str, _port: int) -> list[str]:
    return ["8.8.8.8"]


def test_reporter_sends_clear_redacted_feishu_message_without_webhook_leak() -> None:
    transport = _FakeTransport()
    reporter = FeishuErrorReporter(
        _config(),
        transport=transport,
        resolver=_resolver,
        now=lambda: datetime(2026, 7, 31, 2, 3, 4, tzinfo=timezone.utc),
    )

    result = reporter.report(
        ErrorReportEvent(
            category="repository_error",
            component="core.crm",
            summary="token=super-secret mobile=13987654321 external_userid=wmRuntimeSentinel001 database unavailable",
            status_code=503,
            error_code="production_repository_unavailable",
            error_type="RepositoryProviderError",
            method="GET",
            route="/api/customers/{customer_id}",
        )
    )

    assert result.sent is True
    assert len(transport.calls) == 1
    payload = json.loads(transport.calls[0]["body"])
    text = payload["content"]["text"]
    assert "【AI-CRM 全局报错】" in text
    assert "2026-07-31 10:03:04" in text
    assert "数据/仓储异常" in text
    assert "GET /api/customers/{customer_id}" in text
    assert "production_repository_unavailable" in text
    assert "[redacted]" in text
    assert "[pii]" in text
    assert "super-secret" not in text
    assert "13987654321" not in text
    assert "wmRuntimeSentinel001" not in text
    assert "test-token" not in text


def test_reporter_deduplicates_and_rate_limits_without_blocking_callers() -> None:
    clock = [100.0]
    transport = _FakeTransport()
    reporter = FeishuErrorReporter(
        _config(dedupe_seconds=10.0, max_per_minute=1),
        transport=transport,
        resolver=_resolver,
        monotonic=lambda: clock[0],
    )
    first = reporter.report(ErrorReportEvent(category="system_error", component="worker", summary="boom"))
    duplicate = reporter.report(ErrorReportEvent(category="system_error", component="worker", summary="boom"))
    limited = reporter.report(ErrorReportEvent(category="system_error", component="worker", summary="different"))

    assert first.status == "sent"
    assert duplicate.status == "deduplicated"
    assert duplicate.suppressed_count == 1
    assert limited.status == "rate_limited"
    assert len(transport.calls) == 1


def test_delivery_failure_is_contained_and_can_retry_immediately() -> None:
    transport = _FakeTransport(response=HttpsTransportResponse(status_code=503, text="unavailable"))
    reporter = FeishuErrorReporter(_config(), transport=transport, resolver=_resolver)
    event = ErrorReportEvent(category="system_error", component="worker", summary="provider down")

    first = reporter.report(event)
    transport.response = HttpsTransportResponse(status_code=200, text='{"StatusCode":0}')
    retry = reporter.report(event)

    assert first.status == "provider_rejected"
    assert retry.status == "sent"
    assert len(transport.calls) == 2


def test_startup_config_requires_both_execute_gate_and_secret_reference() -> None:
    reference = "secretref:file:AICRM_ERROR_REPORTING_FEISHU_WEBHOOK_SECRET:v1_1111111111111111_2222222222222222"
    seen: list[str] = []

    def load_secret(value: str) -> str:
        seen.append(value)
        return "https://open.feishu.cn/open-apis/bot/v2/hook/from-secret-store"

    disabled = load_error_reporting_config(
        environment={"AICRM_ERROR_REPORTING_FEISHU_WEBHOOK_SECRET_REF": reference},
        secret_loader=load_secret,
    )
    enabled = load_error_reporting_config(
        environment={
            "AICRM_ERROR_REPORTING_EXECUTE": "1",
            "AICRM_ERROR_REPORTING_FEISHU_WEBHOOK_SECRET_REF": reference,
            "AICRM_NEXT_ENV": "production",
        },
        secret_loader=load_secret,
    )

    assert disabled.enabled is False
    assert enabled.enabled is True
    assert enabled.environment == "production"
    assert seen == [reference, reference]


def test_error_log_and_failed_worker_result_share_the_reporter() -> None:
    collector = _CollectingReporter()
    handler = ErrorReportingLogHandler(collector)  # type: ignore[arg-type]
    record = logging.LogRecord(
        name="aicrm_next.worker",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="worker token=hidden failed",
        args=(),
        exc_info=None,
    )
    handler.emit(record)
    result = report_failed_result(
        {
            "ok": False,
            "error_code": "batch_failed",
            "message": "mobile=13987654321",
            "error_type": "RuntimeError",
        },
        component="run_worker.py",
        reporter=collector,  # type: ignore[arg-type]
    )

    assert result is None
    assert [event.category for event in collector.events] == ["system_error", "worker_error"]
    assert collector.events[0].component == "aicrm_next.worker"
    assert collector.events[1].error_code == "batch_failed"
    assert collector.events[1].summary == "mobile=[pii]"


def test_worker_category_is_rendered_as_background_failure_instead_of_pii() -> None:
    transport = _FakeTransport()
    reporter = FeishuErrorReporter(_config(), transport=transport, resolver=_resolver)

    reporter.report(
        ErrorReportEvent(
            category="worker_error",
            component="run_execution_runtime.py",
            summary="process_failed",
            error_code="process_failed",
        )
    )

    text = json.loads(transport.calls[0]["body"])["content"]["text"]
    assert "类别：后台任务异常" in text
    assert "类别：[pii]" not in text


def test_error_log_handler_includes_redacted_safe_exception_detail() -> None:
    collector = _CollectingReporter()
    handler = ErrorReportingLogHandler(collector)  # type: ignore[arg-type]
    record = logging.LogRecord(
        name="aicrm_next.worker",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="continuation failed",
        args=(),
        exc_info=None,
    )
    record.error_type = "OperationalError"
    record.error_detail = "connection failed mobile=[pii]"

    handler.emit(record)

    assert collector.events[0].error_type == "OperationalError"
    assert collector.events[0].summary == "continuation failed: connection failed mobile=[pii]"


def test_fastapi_handlers_report_business_system_and_http_5xx_only(monkeypatch) -> None:
    from aicrm_next.main import create_app

    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    collector = _CollectingReporter()
    app = create_app(error_reporter=collector)  # type: ignore[arg-type]

    @app.get("/__test__/business/{item_id}")
    def business_error(item_id: str):
        raise ContractError(f"mobile=13987654321 item failed: {item_id}")

    @app.get("/__test__/system")
    def system_error():
        raise RuntimeError("token=should-not-leak")

    @app.get("/__test__/upstream")
    def upstream_error():
        raise HTTPException(status_code=503, detail="upstream unavailable")

    @app.get("/__test__/missing")
    def missing():
        raise HTTPException(status_code=404, detail="missing")

    @app.get("/__test__/direct-5xx")
    def direct_5xx():
        return JSONResponse(status_code=502, content={"ok": False, "error_code": "direct_failure"})

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/__test__/business/123").status_code == 400
    assert client.get("/__test__/system").status_code == 500
    assert client.get("/__test__/upstream").status_code == 503
    assert client.get("/__test__/missing").status_code == 404
    assert client.get("/__test__/direct-5xx").status_code == 502

    assert [event.category for event in collector.events] == [
        "business_error",
        "system_error",
        "http_error",
        "http_error",
    ]
    assert collector.events[0].route == "/__test__/business/{item_id}"
    assert collector.events[0].summary == "mobile=13987654321 item failed: 123"
    assert collector.events[1].error_code == "internal_server_error"
    assert collector.events[2].status_code == 503
    assert collector.events[3].status_code == 502
    assert collector.events[3].error_code == "http_error_response"
