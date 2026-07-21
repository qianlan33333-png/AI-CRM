from __future__ import annotations

import ipaddress
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

from aicrm_next.platform_foundation.command_bus import CommandContext
from aicrm_next.platform_foundation.external_effects import ExternalEffectService, FEISHU_WEBHOOK_NOTIFY
from zoneinfo import ZoneInfo

from .domain import normalized_bool, normalized_text
from .repository import AdminJobsRepository, build_admin_jobs_repository

FEISHU_CHANNEL = "feishu"
FEISHU_VALIDATION_MESSAGE = "【群发队列监控验证】\n这是一条飞书 webhook 验证消息。收到此消息表示群发队列小时报配置成功。"
FEISHU_HOURLY_REPORT_TITLE = "【群发队列小时报】"
FEISHU_WEBHOOK_ERROR = "飞书 webhook 验证失败，请检查地址或机器人配置"
FEISHU_HOURLY_REPORT_ERROR = "飞书小时报发送失败，请检查 webhook 或机器人配置"
FEISHU_VALIDATION_STATUSES = {"unverified", "valid", "invalid"}
_ALLOWED_HOSTS = {"open.feishu.cn", "open.larksuite.com"}
_ALLOWED_PATH_PREFIX = "/open-apis/bot/v2/hook/"


class FeishuWebhookValidationError(ValueError):
    pass


HourlyReportSender = Callable[[str, str], dict[str, Any]]


def validate_feishu_webhook_url(webhook_url: str) -> None:
    value = normalized_text(webhook_url)
    if not value:
        raise FeishuWebhookValidationError("webhook 地址不能为空")
    try:
        parsed = urlsplit(value)
    except Exception as exc:
        raise FeishuWebhookValidationError("webhook 地址格式不正确") from exc
    if parsed.scheme != "https":
        raise FeishuWebhookValidationError("webhook 地址必须使用 https")
    hostname = normalized_text(parsed.hostname).lower()
    if not hostname:
        raise FeishuWebhookValidationError("webhook 地址域名不能为空")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise FeishuWebhookValidationError("webhook 地址域名不允许使用 IP")
    if hostname in {"localhost", "127.0.0.1", "::1"} or hostname not in _ALLOWED_HOSTS:
        raise FeishuWebhookValidationError("webhook 地址域名不在允许范围内")
    try:
        port = parsed.port
    except ValueError as exc:
        raise FeishuWebhookValidationError("webhook 地址端口不正确") from exc
    if port is not None:
        raise FeishuWebhookValidationError("webhook 地址不允许指定端口")
    if not parsed.path.startswith(_ALLOWED_PATH_PREFIX):
        raise FeishuWebhookValidationError("webhook 地址路径不正确")
    hook_token = parsed.path[len(_ALLOWED_PATH_PREFIX) :].strip("/")
    if not hook_token:
        raise FeishuWebhookValidationError("webhook hook token 不能为空")


def mask_webhook_url(webhook_url: str | None | Any) -> str | None:
    value = normalized_text(webhook_url)
    if not value:
        return None
    try:
        parsed = urlsplit(value)
    except Exception:
        return None
    hostname = normalized_text(parsed.hostname)
    if not parsed.scheme or not hostname or not parsed.path.startswith(_ALLOWED_PATH_PREFIX):
        return None
    token = parsed.path[len(_ALLOWED_PATH_PREFIX) :].strip("/")
    tail = token[-4:] if token else ""
    if not tail:
        return f"{parsed.scheme}://{hostname}{_ALLOWED_PATH_PREFIX}****"
    return f"{parsed.scheme}://{hostname}{_ALLOWED_PATH_PREFIX}****{tail}"


def public_feishu_notification_setting(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {
            "enabled": False,
            "channel": FEISHU_CHANNEL,
            "webhookMasked": None,
            "validationStatus": "unconfigured",
            "validatedAt": None,
            "lastValidationError": None,
        }
    validation_status = normalized_text(row.get("validation_status")) or "unverified"
    if validation_status not in FEISHU_VALIDATION_STATUSES:
        validation_status = "unverified"
    return {
        "enabled": normalized_bool(row.get("enabled")),
        "channel": FEISHU_CHANNEL,
        "webhookMasked": mask_webhook_url(row.get("webhook_url")),
        "validationStatus": validation_status,
        "validatedAt": _iso_or_none(row.get("validated_at")),
        "lastValidationError": _short_error(row.get("last_validation_error")),
    }


def get_feishu_notification_setting(repo: AdminJobsRepository | None = None) -> dict[str, Any]:
    repo = repo or build_admin_jobs_repository()
    return public_feishu_notification_setting(repo.get_broadcast_notification_setting(FEISHU_CHANNEL))


def upsert_feishu_notification_setting(
    *,
    enabled: bool,
    webhook_url: str,
    validation_status: str = "unverified",
    validated_at: datetime | str | None = None,
    last_validation_error: str | None = None,
    repo: AdminJobsRepository | None = None,
) -> dict[str, Any]:
    validate_feishu_webhook_url(webhook_url)
    repo = repo or build_admin_jobs_repository()
    status = normalized_text(validation_status) or "unverified"
    if status not in FEISHU_VALIDATION_STATUSES:
        status = "unverified"
    row = repo.upsert_broadcast_notification_setting(
        channel=FEISHU_CHANNEL,
        enabled=bool(enabled),
        webhook_url=normalized_text(webhook_url),
        validation_status=status,
        validated_at=validated_at,
        last_validation_error=_short_error(last_validation_error),
    )
    return public_feishu_notification_setting(row)


def validate_feishu_webhook(
    *,
    webhook_url: str,
    enabled: bool = True,
    repo: AdminJobsRepository | None = None,
    sender: Callable[[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    repo = repo or build_admin_jobs_repository()
    try:
        validate_feishu_webhook_url(webhook_url)
    except FeishuWebhookValidationError:
        return {"ok": False, "validationStatus": "invalid", "message": FEISHU_WEBHOOK_ERROR}
    send = sender or send_feishu_webhook_message
    try:
        result = send(normalized_text(webhook_url), FEISHU_VALIDATION_MESSAGE)
    except Exception:
        result = {"ok": False}
    if result.get("ok") is True:
        now = datetime.now(timezone.utc)
        row = repo.upsert_broadcast_notification_setting(
            channel=FEISHU_CHANNEL,
            enabled=bool(enabled),
            webhook_url=normalized_text(webhook_url),
            validation_status="valid",
            validated_at=now,
            last_validation_error=None,
        )
        public = public_feishu_notification_setting(row)
        return {"ok": True, "validationStatus": "valid", "validatedAt": public["validatedAt"], "webhookMasked": public["webhookMasked"]}
    row = repo.upsert_broadcast_notification_setting(
        channel=FEISHU_CHANNEL,
        enabled=bool(enabled),
        webhook_url=normalized_text(webhook_url),
        validation_status="invalid",
        validated_at=None,
        last_validation_error=FEISHU_WEBHOOK_ERROR,
    )
    public = public_feishu_notification_setting(row)
    return {
        "ok": False,
        "validationStatus": "invalid",
        "message": FEISHU_WEBHOOK_ERROR,
        "webhookMasked": public["webhookMasked"],
        "lastValidationError": public["lastValidationError"],
    }


def enqueue_feishu_webhook_validation(
    *,
    webhook_url: str,
    enabled: bool = True,
    actor_id: str = "",
    repo: AdminJobsRepository | None = None,
) -> dict[str, Any]:
    """Persist validation intent and return before any provider call starts."""

    validate_feishu_webhook_url(webhook_url)
    repo = repo or build_admin_jobs_repository()
    setting = upsert_feishu_notification_setting(
        enabled=enabled,
        webhook_url=webhook_url,
        validation_status="unverified",
        validated_at=None,
        last_validation_error=None,
        repo=repo,
    )
    validation_id = __import__("uuid").uuid4().hex
    job = ExternalEffectService().plan_effect(
        effect_type=FEISHU_WEBHOOK_NOTIFY,
        adapter_name="feishu_webhook",
        operation="validate",
        target_type="feishu_webhook_setting",
        target_id=FEISHU_CHANNEL,
        business_type="broadcast_notification_validation",
        business_id=validation_id,
        payload={
            "webhook_url": normalized_text(webhook_url),
            "body": {"msg_type": "text", "content": {"text": FEISHU_VALIDATION_MESSAGE}},
            "validation": {"channel": FEISHU_CHANNEL, "enabled": bool(enabled)},
        },
        payload_summary={
            "webhook_url_present": True,
            "validation": True,
            "channel": FEISHU_CHANNEL,
            "text_length": len(FEISHU_VALIDATION_MESSAGE),
        },
        context=CommandContext(
            actor_id=normalized_text(actor_id) or "authenticated_admin",
            actor_type="admin",
            trace_id=f"feishu-validation:{validation_id}",
            source_route="/api/admin/broadcast-jobs/notification-settings/feishu/validate",
        ),
        source_module="admin_jobs.notification_settings",
        source_command_id=validation_id,
        idempotency_key=f"feishu-webhook-validation:{validation_id}",
        risk_level="medium",
        execution_mode="execute",
        status="queued",
    )
    job_id = int(job.get("id") or 0)
    return {
        "ok": True,
        "accepted": True,
        "validationStatus": "unverified",
        "webhookMasked": setting.get("webhookMasked"),
        "externalEffectJobId": job_id,
        "statusUrl": f"/api/admin/push-center/jobs/external_effect_job:{job_id}",
        "real_external_call_executed": False,
    }


def send_feishu_webhook_message(webhook_url: str, text: str) -> dict[str, Any]:
    validate_feishu_webhook_url(webhook_url)
    job = ExternalEffectService().plan_effect(
        effect_type=FEISHU_WEBHOOK_NOTIFY,
        adapter_name="feishu_webhook",
        operation="notify",
        target_type="feishu_webhook",
        target_id="admin_jobs_notification",
        business_type="admin_notification",
        business_id="feishu_hourly_report",
        payload={
            "webhook_url": normalized_text(webhook_url),
            "body": {"msg_type": "text", "content": {"text": normalized_text(text)}},
        },
        payload_summary={
            "webhook_url_present": bool(normalized_text(webhook_url)),
            "text_length": len(normalized_text(text)),
        },
        context=CommandContext(
            actor_id="admin_jobs",
            actor_type="system",
            trace_id="feishu_hourly_report",
            source_route="admin_jobs.notification_settings.send_feishu_webhook_message",
        ),
        source_module="admin_jobs.notification_settings",
        idempotency_key=f"feishu-webhook-notify:{__import__('hashlib').sha1((normalized_text(webhook_url) + normalized_text(text)).encode('utf-8')).hexdigest()}",
    )
    return {
        "ok": True,
        "queued": True,
        "external_effect_job_id": job.get("id"),
        "real_external_call_executed": False,
    }


def get_previous_hour_window(time_zone: str = "Asia/Shanghai", now: datetime | None = None) -> dict[str, Any]:
    tz = ZoneInfo(time_zone)
    current = now or datetime.now(tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=tz)
    else:
        current = current.astimezone(tz)
    window_end = current.replace(minute=0, second=0, microsecond=0)
    window_start = window_end - timedelta(hours=1)
    return {
        "windowStart": window_start,
        "windowEnd": window_end,
        "label": f"{window_start:%Y-%m-%d %H:%M} - {window_end:%H:%M}",
    }


def get_broadcast_job_hourly_summary(*, window_start: datetime, window_end: datetime, repo: AdminJobsRepository | None = None) -> dict[str, int]:
    repo = repo or build_admin_jobs_repository()
    row = repo.broadcast_hourly_summary(window_start=window_start, window_end=window_end)
    return {
        "totalJobs": int(row.get("total_jobs") or 0),
        "successJobs": int(row.get("success_jobs") or 0),
        "failedJobs": int(row.get("failed_jobs") or 0),
        "pendingJobs": int(row.get("pending_jobs") or 0),
        "cancelledJobs": int(row.get("cancelled_jobs") or 0),
    }


def build_broadcast_job_hourly_report_message(
    *,
    window_start: datetime,
    window_end: datetime,
    total_jobs: int,
    success_jobs: int,
    failed_jobs: int,
) -> str:
    start = _as_shanghai(window_start)
    end = _as_shanghai(window_end)
    return (
        f"{FEISHU_HOURLY_REPORT_TITLE}\n"
        f"统计窗口：{start:%Y-%m-%d %H:%M} - {end:%H:%M}\n\n"
        f"任务总数：{int(total_jobs)}\n"
        f"成功：{int(success_jobs)}\n"
        f"失败：{int(failed_jobs)}"
    )


def build_hourly_report_key(*, channel: str = FEISHU_CHANNEL, window_start: datetime) -> str:
    start = _as_shanghai(window_start)
    return f"broadcast_jobs:{channel}:{start.isoformat(timespec='seconds')}"


def create_hourly_report_pending(
    *,
    report_key: str,
    window_start: datetime,
    window_end: datetime,
    channel: str = FEISHU_CHANNEL,
    repo: AdminJobsRepository | None = None,
) -> str:
    repo = repo or build_admin_jobs_repository()
    return repo.create_broadcast_hourly_report_pending(report_key=report_key, window_start=window_start, window_end=window_end, channel=channel)


def mark_hourly_report_sent(*, report_key: str, payload_json: dict[str, Any], repo: AdminJobsRepository | None = None) -> None:
    repo = repo or build_admin_jobs_repository()
    repo.mark_broadcast_hourly_report_sent(report_key=report_key, payload_json=payload_json)


def mark_hourly_report_failed(*, report_key: str, error_message: str, repo: AdminJobsRepository | None = None) -> None:
    repo = repo or build_admin_jobs_repository()
    repo.mark_broadcast_hourly_report_failed(report_key=report_key, error_message=_short_error(error_message) or FEISHU_HOURLY_REPORT_ERROR)


def send_broadcast_job_hourly_feishu_report(
    *,
    now: datetime | None = None,
    repo: AdminJobsRepository | None = None,
    sender: HourlyReportSender | None = None,
) -> dict[str, Any]:
    repo = repo or build_admin_jobs_repository()
    setting = repo.get_broadcast_notification_setting(FEISHU_CHANNEL)
    if not setting or not normalized_text(setting.get("webhook_url")):
        return {"status": "skipped_no_config"}
    if not normalized_bool(setting.get("enabled")):
        return {"status": "skipped_disabled"}
    if normalized_text(setting.get("validation_status")) != "valid":
        return {"status": "skipped_unverified"}

    window = get_previous_hour_window(now=now)
    window_start = window["windowStart"]
    window_end = window["windowEnd"]
    summary = get_broadcast_job_hourly_summary(window_start=window_start, window_end=window_end, repo=repo)
    public_summary = _public_summary(summary)
    if public_summary["totalJobs"] <= 0:
        return {"status": "skipped_no_jobs", "summary": public_summary}

    report_key = build_hourly_report_key(channel=FEISHU_CHANNEL, window_start=window_start)
    created = create_hourly_report_pending(report_key=report_key, window_start=window_start, window_end=window_end, channel=FEISHU_CHANNEL, repo=repo)
    if created == "duplicate":
        return {"status": "skipped_duplicate", "summary": public_summary}

    message = build_broadcast_job_hourly_report_message(
        window_start=window_start,
        window_end=window_end,
        total_jobs=public_summary["totalJobs"],
        success_jobs=public_summary["successJobs"],
        failed_jobs=public_summary["failedJobs"],
    )
    send = sender or send_feishu_webhook_message
    try:
        result = send(normalized_text(setting.get("webhook_url")), message)
    except Exception:
        result = {"ok": False}
    if result.get("ok") is True and result.get("queued") is True:
        # Queue acceptance is not provider delivery.  Keep the durable report
        # pending until the external-effect completion path records the actual
        # provider outcome.
        return {
            "status": "queued",
            "summary": public_summary,
            "externalEffectJobId": result.get("external_effect_job_id"),
        }
    if result.get("ok") is True:
        mark_hourly_report_sent(
            report_key=report_key,
            repo=repo,
            payload_json={
                "channel": FEISHU_CHANNEL,
                "message": message,
                "summary": public_summary,
                "windowStart": window_start.isoformat(),
                "windowEnd": window_end.isoformat(),
            },
        )
        return {"status": "sent", "summary": public_summary}
    mark_hourly_report_failed(report_key=report_key, error_message=FEISHU_HOURLY_REPORT_ERROR, repo=repo)
    return {"status": "failed", "summary": public_summary, "message": FEISHU_HOURLY_REPORT_ERROR}


def _short_error(value: Any) -> str | None:
    text = normalized_text(value)
    if not text:
        return None
    return text.replace("\n", " ")[:120]


def _as_shanghai(value: datetime) -> datetime:
    tz = ZoneInfo("Asia/Shanghai")
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def _public_summary(summary: dict[str, Any]) -> dict[str, int]:
    return {
        "totalJobs": int(summary.get("totalJobs") or 0),
        "successJobs": int(summary.get("successJobs") or 0),
        "failedJobs": int(summary.get("failedJobs") or 0),
    }


def _iso_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    text = normalized_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        try:
            parsed_json = json.loads(json.dumps(text, default=str))
            return normalized_text(parsed_json) or None
        except Exception:
            return text[:80]
