from __future__ import annotations

import hashlib
import json
import logging
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from aicrm_next.platform.shared.outbound_https.security import (
    Resolver,
    WebhookUrlValidationError,
    resolve_and_validate_public_https_target,
)
from aicrm_next.platform.shared.outbound_https.transport import (
    HttpsTransport,
    HttpsTransportError,
    HttpsTransportTimeout,
    PinnedHttpsTransport,
)
from aicrm_next.platform.shared.release import current_release_sha
from aicrm_next.platform.shared.runtime_settings import startup_environment_setting
from aicrm_next.platform.shared.secret_store import FileSecretStore, SecretStoreError, parse_secret_reference
from aicrm_next.platform.shared.sensitive_data import redact_sensitive_data, redact_sensitive_text


ERROR_REPORT_EXECUTE_KEY = "AICRM_ERROR_REPORTING_EXECUTE"
ERROR_REPORT_WEBHOOK_SECRET_REF_KEY = "AICRM_ERROR_REPORTING_FEISHU_WEBHOOK_SECRET_REF"
RUNTIME_ENVIRONMENT_KEYS = frozenset({ERROR_REPORT_EXECUTE_KEY, ERROR_REPORT_WEBHOOK_SECRET_REF_KEY})

_TRUE_VALUES = {"1", "true", "yes", "on"}
_ALLOWED_FEISHU_HOSTS = {"open.feishu.cn", "open.larksuite.com"}
_FEISHU_HOOK_PATH_PREFIX = "/open-apis/bot/v2/hook/"
_DEFAULT_TIMEOUT_SECONDS = 2.0
_DEFAULT_DEDUPE_SECONDS = 300.0
_DEFAULT_MAX_PER_MINUTE = 20
_MAX_SUMMARY_CHARS = 600
_MAX_COMPONENT_CHARS = 160
_REPORTER_LOGGER_NAME = "aicrm.error_reporting.delivery"
_REPORTER_LOGGER = logging.getLogger(_REPORTER_LOGGER_NAME)


@dataclass(frozen=True)
class ErrorReportingConfig:
    enabled: bool
    webhook_url: str
    environment: str
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    dedupe_seconds: float = _DEFAULT_DEDUPE_SECONDS
    max_per_minute: int = _DEFAULT_MAX_PER_MINUTE


@dataclass(frozen=True)
class ErrorReportEvent:
    category: str
    component: str
    summary: str
    severity: str = "ERROR"
    status_code: int | None = None
    error_code: str = ""
    error_type: str = ""
    method: str = ""
    route: str = ""
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class ErrorReportResult:
    status: str
    fingerprint: str = ""
    provider_status_code: int | None = None
    suppressed_count: int = 0

    @property
    def sent(self) -> bool:
        return self.status == "sent"


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in _TRUE_VALUES


def _load_webhook_secret(reference: str) -> str:
    normalized = str(reference or "").strip()
    if not normalized:
        return ""
    try:
        parsed = parse_secret_reference(normalized)
        if parsed.key != ERROR_REPORT_WEBHOOK_SECRET_REF_KEY.removesuffix("_REF"):
            return ""
        return FileSecretStore.from_environment().read(normalized).strip()
    except SecretStoreError:
        return ""


def load_error_reporting_config(
    *,
    environment: Mapping[str, str] | None = None,
    secret_loader: Callable[[str], str] | None = None,
) -> ErrorReportingConfig:
    execute = startup_environment_setting(ERROR_REPORT_EXECUTE_KEY, environment=environment)
    reference = startup_environment_setting(ERROR_REPORT_WEBHOOK_SECRET_REF_KEY, environment=environment)
    webhook_url = (secret_loader or _load_webhook_secret)(reference) if reference else ""
    environment_name = startup_environment_setting("AICRM_NEXT_ENV", "unknown", environment=environment).strip() or "unknown"
    return ErrorReportingConfig(
        enabled=_truthy(execute) and bool(webhook_url),
        webhook_url=webhook_url,
        environment=environment_name,
    )


def _validate_feishu_webhook_url(webhook_url: str) -> str:
    value = str(webhook_url or "").strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise WebhookUrlValidationError("Feishu error-report webhook URL is invalid") from exc
    hostname = str(parsed.hostname or "").strip().rstrip(".").lower()
    if parsed.scheme.lower() != "https" or hostname not in _ALLOWED_FEISHU_HOSTS or port not in {None, 443}:
        raise WebhookUrlValidationError("Feishu error-report webhook target is not allowed")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise WebhookUrlValidationError("Feishu error-report webhook URL contains unsupported fields")
    if not parsed.path.startswith(_FEISHU_HOOK_PATH_PREFIX):
        raise WebhookUrlValidationError("Feishu error-report webhook path is invalid")
    hook_token = parsed.path[len(_FEISHU_HOOK_PATH_PREFIX) :].strip("/")
    if not hook_token or "/" in hook_token:
        raise WebhookUrlValidationError("Feishu error-report webhook token is invalid")
    return value


class FeishuErrorReporter:
    """Best-effort, database-independent error notification channel.

    The reporter is deliberately isolated from External Effect Queue so a
    database or queue outage cannot silence the notification path. Delivery
    failures are contained and never change the calling business outcome.
    """

    def __init__(
        self,
        config: ErrorReportingConfig,
        *,
        transport: HttpsTransport | None = None,
        resolver: Resolver | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or PinnedHttpsTransport()
        self._resolver = resolver
        self._monotonic = monotonic
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._recent: dict[str, tuple[float, int]] = {}
        self._minute_sends: deque[float] = deque()

    @property
    def enabled(self) -> bool:
        return bool(self._config.enabled and self._config.webhook_url)

    def report(self, event: ErrorReportEvent) -> ErrorReportResult:
        normalized = self._normalized_event(event)
        fingerprint = self._fingerprint(normalized)
        if not self.enabled:
            return ErrorReportResult(status="disabled", fingerprint=fingerprint)

        reserved_at, suppressed_count, skip_status = self._reserve(fingerprint)
        if skip_status:
            return ErrorReportResult(
                status=skip_status,
                fingerprint=fingerprint,
                suppressed_count=suppressed_count,
            )

        try:
            webhook_url = _validate_feishu_webhook_url(self._config.webhook_url)
            target = resolve_and_validate_public_https_target(webhook_url, resolver=self._resolver)
            body = json.dumps(
                {
                    "msg_type": "text",
                    "content": {
                        "text": self._render_text(
                            normalized,
                            fingerprint=fingerprint,
                            suppressed_count=suppressed_count,
                        )
                    },
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            response = self._transport.post(
                target,
                body=body,
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=float(self._config.timeout_seconds),
            )
            if not self._provider_succeeded(response.status_code, response.text):
                self._release_failed_reservation(fingerprint, reserved_at)
                return ErrorReportResult(
                    status="provider_rejected",
                    fingerprint=fingerprint,
                    provider_status_code=int(response.status_code),
                    suppressed_count=suppressed_count,
                )
            return ErrorReportResult(
                status="sent",
                fingerprint=fingerprint,
                provider_status_code=int(response.status_code),
                suppressed_count=suppressed_count,
            )
        except (WebhookUrlValidationError, HttpsTransportTimeout, HttpsTransportError, OSError, ValueError):
            self._release_failed_reservation(fingerprint, reserved_at)
            _REPORTER_LOGGER.warning("Feishu error report delivery failed", extra={"error_report_fingerprint": fingerprint})
            return ErrorReportResult(
                status="delivery_failed",
                fingerprint=fingerprint,
                suppressed_count=suppressed_count,
            )
        except Exception:
            self._release_failed_reservation(fingerprint, reserved_at)
            _REPORTER_LOGGER.warning("Feishu error report delivery failed unexpectedly", extra={"error_report_fingerprint": fingerprint})
            return ErrorReportResult(
                status="delivery_failed",
                fingerprint=fingerprint,
                suppressed_count=suppressed_count,
            )

    def _reserve(self, fingerprint: str) -> tuple[float, int, str]:
        now = float(self._monotonic())
        with self._lock:
            last_sent_at, previous_suppressed = self._recent.get(fingerprint, (float("-inf"), 0))
            if now - last_sent_at < float(self._config.dedupe_seconds):
                suppressed = previous_suppressed + 1
                self._recent[fingerprint] = (last_sent_at, suppressed)
                return last_sent_at, suppressed, "deduplicated"
            while self._minute_sends and now - self._minute_sends[0] >= 60.0:
                self._minute_sends.popleft()
            if len(self._minute_sends) >= int(self._config.max_per_minute):
                return now, previous_suppressed, "rate_limited"
            self._minute_sends.append(now)
            self._recent[fingerprint] = (now, 0)
            return now, previous_suppressed, ""

    def _release_failed_reservation(self, fingerprint: str, reserved_at: float) -> None:
        with self._lock:
            current = self._recent.get(fingerprint)
            if current is not None and current[0] == reserved_at:
                self._recent.pop(fingerprint, None)
            try:
                self._minute_sends.remove(reserved_at)
            except ValueError:
                pass

    def _normalized_event(self, event: ErrorReportEvent) -> ErrorReportEvent:
        return ErrorReportEvent(
            category=redact_sensitive_text(event.category).strip()[:80] or "system_error",
            component=redact_sensitive_text(event.component).strip()[:_MAX_COMPONENT_CHARS] or "unknown",
            summary=redact_sensitive_text(event.summary).strip()[:_MAX_SUMMARY_CHARS] or "未提供错误摘要",
            severity=redact_sensitive_text(event.severity).strip().upper()[:20] or "ERROR",
            status_code=int(event.status_code) if event.status_code is not None else None,
            error_code=redact_sensitive_text(event.error_code).strip()[:160],
            error_type=redact_sensitive_text(event.error_type).strip()[:160],
            method=redact_sensitive_text(event.method).strip().upper()[:16],
            route=redact_sensitive_text(event.route).strip()[:300],
            occurred_at=event.occurred_at,
        )

    @staticmethod
    def _fingerprint(event: ErrorReportEvent) -> str:
        material = "\0".join(
            (
                event.category,
                event.component,
                str(event.status_code or ""),
                event.error_code,
                event.error_type,
                event.method,
                event.route,
                event.summary,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    def _render_text(self, event: ErrorReportEvent, *, fingerprint: str, suppressed_count: int) -> str:
        occurred_at = event.occurred_at or self._now()
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        local_time = occurred_at.astimezone(ZoneInfo("Asia/Shanghai"))
        category_label = {
            "business_error": "业务问题",
            "repository_error": "数据/仓储异常",
            "http_error": "HTTP 服务异常",
            "worker_error": "后台任务异常",
            "system_error": "系统结构异常",
        }.get(event.category, event.category)
        lines = [
            "【AI-CRM 全局报错】",
            f"时间：{local_time:%Y-%m-%d %H:%M:%S}（北京时间）",
            f"环境：{redact_sensitive_text(self._config.environment)[:80] or 'unknown'}",
            f"级别：{event.severity}",
            f"类别：{category_label}",
            f"组件：{event.component}",
        ]
        if event.method or event.route:
            lines.append(f"请求：{' '.join(item for item in (event.method, event.route) if item)}")
        if event.status_code is not None:
            lines.append(f"HTTP 状态：{event.status_code}")
        if event.error_code:
            lines.append(f"错误码：{event.error_code}")
        if event.error_type:
            lines.append(f"异常类型：{event.error_type}")
        lines.extend(
            (
                f"问题点：{event.summary}",
                f"发布版本：{current_release_sha()}",
                f"事件指纹：{fingerprint}",
            )
        )
        if suppressed_count:
            lines.append(f"此前同类错误已抑制：{suppressed_count} 次")
        return "\n".join(lines)

    @staticmethod
    def _provider_succeeded(status_code: int, text: str) -> bool:
        if not 200 <= int(status_code) < 300:
            return False
        try:
            payload = json.loads(str(text or "{}"))
        except json.JSONDecodeError:
            return False
        if not isinstance(payload, dict):
            return False
        provider_code = payload.get("code", payload.get("StatusCode"))
        try:
            return int(provider_code) == 0
        except (TypeError, ValueError):
            return False


class ErrorReportingLogHandler(logging.Handler):
    def __init__(self, reporter: FeishuErrorReporter) -> None:
        super().__init__(level=logging.ERROR)
        self.reporter = reporter
        self._aicrm_error_reporting_handler = True

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("aicrm.error_reporting") or record.name == "aicrm_next.main":
            return
        try:
            error_type = str(getattr(record, "error_type", "") or "")
            if not error_type and record.exc_info and record.exc_info[0] is not None:
                error_type = record.exc_info[0].__name__
            self.reporter.report(
                ErrorReportEvent(
                    category="system_error",
                    component=record.name,
                    summary=record.getMessage(),
                    severity=record.levelname,
                    error_code=str(getattr(record, "error_code", "") or ""),
                    error_type=error_type,
                )
            )
        except Exception:
            self.handleError(record)


_DEFAULT_REPORTER: FeishuErrorReporter | None = None
_DEFAULT_REPORTER_LOCK = threading.Lock()
_PROCESS_HOOK_LOCK = threading.Lock()
_PROCESS_HOOK_INSTALLED = False


def get_default_error_reporter() -> FeishuErrorReporter:
    global _DEFAULT_REPORTER
    with _DEFAULT_REPORTER_LOCK:
        if _DEFAULT_REPORTER is None:
            _DEFAULT_REPORTER = FeishuErrorReporter(load_error_reporting_config())
        return _DEFAULT_REPORTER


def install_logging_error_reporting(*, reporter: FeishuErrorReporter | None = None) -> FeishuErrorReporter:
    selected = reporter or get_default_error_reporter()
    root = logging.getLogger()
    if not any(getattr(handler, "_aicrm_error_reporting_handler", False) for handler in root.handlers):
        root.addHandler(ErrorReportingLogHandler(selected))
    return selected


def install_process_error_reporting(*, component: str = "python_process") -> FeishuErrorReporter:
    global _PROCESS_HOOK_INSTALLED
    reporter = install_logging_error_reporting()
    with _PROCESS_HOOK_LOCK:
        if _PROCESS_HOOK_INSTALLED:
            return reporter
        previous_excepthook = sys.excepthook

        def report_unhandled(exc_type, exc, traceback) -> None:
            reporter.report(
                ErrorReportEvent(
                    category="system_error",
                    component=component,
                    summary=str(exc) or exc_type.__name__,
                    error_type=exc_type.__name__,
                )
            )
            previous_excepthook(exc_type, exc, traceback)

        sys.excepthook = report_unhandled
        if hasattr(threading, "excepthook"):
            previous_threading_hook = threading.excepthook

            def report_thread_unhandled(args) -> None:
                reporter.report(
                    ErrorReportEvent(
                        category="system_error",
                        component=f"{component}:thread",
                        summary=str(args.exc_value) or args.exc_type.__name__,
                        error_type=args.exc_type.__name__,
                    )
                )
                previous_threading_hook(args)

            threading.excepthook = report_thread_unhandled
        _PROCESS_HOOK_INSTALLED = True
    return reporter


def build_http_error_event(
    *,
    request: Any,
    category: str,
    status_code: int,
    error_code: str,
    exc: BaseException,
) -> ErrorReportEvent:
    route = getattr(request.scope.get("route"), "path", "") if hasattr(request, "scope") else ""
    if not route:
        route = getattr(getattr(request, "url", None), "path", "")
    policy = getattr(getattr(request, "state", None), "route_policy", None)
    component = str(getattr(policy, "capability", "") or "http")
    return ErrorReportEvent(
        category=category,
        component=component,
        summary=str(exc),
        status_code=int(status_code),
        error_code=error_code,
        error_type=type(exc).__name__,
        method=str(getattr(request, "method", "") or ""),
        route=str(route or ""),
    )


def report_failed_result(
    payload: Any,
    *,
    component: str = "",
    reporter: FeishuErrorReporter | None = None,
) -> ErrorReportResult | None:
    if not isinstance(payload, Mapping):
        return None
    status = str(payload.get("status") or "").strip().lower()
    failed = payload.get("ok") is False or status in {"error", "failed", "failed_terminal", "failed_retryable"}
    exit_code = payload.get("exit_code")
    if not failed and exit_code not in (None, "", 0, "0"):
        failed = True
    if not failed:
        return None
    error_code = str(payload.get("error_code") or payload.get("error") or payload.get("reason") or status or "process_failed")
    summary_value = payload.get("message") or payload.get("detail") or payload.get("reason") or error_code
    safe_payload = redact_sensitive_data(
        {
            "error_code": error_code,
            "summary": summary_value,
            "error_type": payload.get("error_type") or payload.get("error"),
        }
    )
    return (reporter or get_default_error_reporter()).report(
        ErrorReportEvent(
            category="worker_error",
            component=str(component or Path(sys.argv[0] or "python_process").name),
            summary=str(safe_payload.get("summary") or error_code),
            error_code=str(safe_payload.get("error_code") or error_code),
            error_type=str(safe_payload.get("error_type") or ""),
        )
    )
