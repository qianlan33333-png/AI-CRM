#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from scripts.ops.check_callback_quick_ack_state import run as run_quick_ack_check
    from scripts.ops.check_wecom_callback_ingress_cutover import run as run_cutover_check
    from scripts.script_runtime import ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from check_callback_quick_ack_state import run as run_quick_ack_check
    from check_wecom_callback_ingress_cutover import run as run_cutover_check
    from script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()


DEFAULT_NGINX_CONFIG = "/etc/nginx/sites-enabled/youcangogogo.conf"
DEFAULT_ENV_FILE = "/home/ubuntu/.openclaw-wecom-pg.env"
DEFAULT_WEB_HEALTH_URL = "http://127.0.0.1:5001/health"
DEFAULT_INGRESS_HEALTH_URL = "http://127.0.0.1:5002/health"
DEFAULT_ADMIN_WEBHOOK_INBOX_METRICS_URL = "http://127.0.0.1:5001/api/admin/webhook-inbox/metrics?provider=wecom&event_family=external_contact"
DEFAULT_ADMIN_WEBHOOK_INBOX_ITEMS_URL = "http://127.0.0.1:5001/api/admin/webhook-inbox/items?provider=wecom&event_family=external_contact&status=pending_failed&limit=1"
DEFAULT_ADMIN_WEBHOOK_INBOX_RECONCILIATION_URL = "http://127.0.0.1:5001/api/admin/wecom/callback/reconciliation?limit=1"
DEFAULT_CALLBACK_INGRESS_SERVICE = "openclaw-wecom-callback-ingress.service"
DEFAULT_CALLBACK_WORKER_SERVICE = "openclaw-wecom-callback-inbox-worker.service"
MIN_PRESSURE_RATE_PER_MINUTE = 1200.0
MAX_CALLBACK_P95_MS = 200.0
MAX_CALLBACK_P99_MS = 500.0
MAX_HEALTH_P95_MS = 100.0
MAX_SIDEBAR_P95_MS = 300.0
MAX_ADMIN_P95_MS = 500.0
DEFAULT_MAX_WEBHOOK_DUE_COUNT = 100
DEFAULT_MAX_WEBHOOK_FAILED_RETRYABLE_COUNT = 0
DEFAULT_MAX_WEBHOOK_DEAD_LETTER_COUNT = 0
DEFAULT_MAX_WEBHOOK_OLDEST_AGE_SECONDS = 300


def _text(value: Any) -> str:
    return str(value or "").strip()


def _load_env_file(path: str) -> bool:
    env_path = Path(path)
    if not env_path.exists():
        return False
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")
    return True


def _psycopg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://") :]
    return url


def probe_health(url: str, timeout_seconds: float) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "aicrm-wecom-callback-readiness-check/1.0"}, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read(4096).decode("utf-8", errors="replace")
            return {"checked": True, "ok": 200 <= int(response.status) < 300, "status_code": int(response.status), "body": body, "error": ""}
    except HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        return {"checked": True, "ok": False, "status_code": int(exc.code), "body": body, "error": ""}
    except (OSError, URLError) as exc:
        return {"checked": False, "ok": False, "status_code": None, "body": "", "error": str(exc)}


def _payload_path(payload: dict[str, Any], path: str) -> Any:
    value: Any = payload
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def probe_json_ok(
    url: str,
    timeout_seconds: float,
    *,
    required_list_key: str = "",
    required_list_paths: tuple[str, ...] = (),
) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "aicrm-wecom-callback-readiness-check/1.0"}, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read(8192).decode("utf-8", errors="replace")
            status_code = int(response.status)
    except HTTPError as exc:
        body = exc.read(8192).decode("utf-8", errors="replace")
        return {"checked": True, "ok": False, "status_code": int(exc.code), "body": body, "json_ok": None, "error": ""}
    except (OSError, URLError) as exc:
        return {"checked": False, "ok": False, "status_code": None, "body": "", "json_ok": None, "error": str(exc)}
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        return {"checked": True, "ok": False, "status_code": status_code, "body": body, "json_ok": None, "error": f"invalid JSON: {exc}"}
    json_ok = isinstance(payload, dict) and payload.get("ok") is True
    required_list_checks: dict[str, bool] = {}
    if required_list_key:
        required_list_checks[required_list_key] = isinstance(payload, dict) and isinstance(payload.get(required_list_key), list)
    for path in required_list_paths:
        required_list_checks[path] = isinstance(payload, dict) and isinstance(_payload_path(payload, path), list)
    required_list_ok = all(required_list_checks.values()) if required_list_checks else True
    failed_paths = [path for path, ok in required_list_checks.items() if not ok]
    return {
        "checked": True,
        "ok": 200 <= status_code < 300 and json_ok and required_list_ok,
        "status_code": status_code,
        "body": body,
        "json_ok": json_ok,
        "required_list_key": required_list_key,
        "required_list_paths": list(required_list_paths),
        "required_list_checks": required_list_checks,
        "required_list_ok": required_list_ok,
        "error": "" if required_list_ok else f"{', '.join(failed_paths)} is not a list",
    }


def read_webhook_inbox_metrics(database_url: str | None = None) -> dict[str, Any]:
    url = _psycopg_url(_text(database_url or os.getenv("DATABASE_URL")))
    if not url:
        return {"checked": False, "ok": False, "schema_present": None, "error": "DATABASE_URL is empty"}
    try:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(url, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.webhook_inbox') AS table_name")
            table_name = _text((cur.fetchone() or {}).get("table_name"))
            if not table_name:
                return {"checked": True, "ok": False, "schema_present": False, "error": "webhook_inbox table missing"}
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total_count,
                    COUNT(*) FILTER (
                        WHERE status IN ('received', 'failed_retryable')
                          AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
                          AND (locked_at IS NULL OR locked_at <= CURRENT_TIMESTAMP - INTERVAL '5 minutes')
                    ) AS due_count,
                    COUNT(*) FILTER (WHERE status = 'processing') AS processing_count,
                    COUNT(*) FILTER (WHERE status = 'failed_retryable') AS failed_retryable_count,
                    COUNT(*) FILTER (WHERE status = 'dead_letter') AS dead_letter_count,
                    COALESCE(
                        EXTRACT(EPOCH FROM CURRENT_TIMESTAMP - MIN(received_at) FILTER (
                            WHERE status IN ('received', 'failed_retryable', 'processing')
                        )),
                        0
                    ) AS oldest_received_age_seconds
                FROM webhook_inbox
                WHERE provider = 'wecom'
                  AND event_family = 'external_contact'
                """
            )
            row = dict(cur.fetchone() or {})
        return {
            "checked": True,
            "ok": True,
            "schema_present": True,
            "total_count": int(row.get("total_count") or 0),
            "due_count": int(row.get("due_count") or 0),
            "processing_count": int(row.get("processing_count") or 0),
            "failed_retryable_count": int(row.get("failed_retryable_count") or 0),
            "dead_letter_count": int(row.get("dead_letter_count") or 0),
            "oldest_received_age_seconds": int(float(row.get("oldest_received_age_seconds") or 0)),
            "error": "",
        }
    except Exception as exc:  # pragma: no cover - depends on production DB
        return {"checked": False, "ok": False, "schema_present": None, "error": str(exc)}


def _int_metric(payload: dict[str, Any], key: str) -> int:
    try:
        return int(float(payload.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def _float_metric(payload: dict[str, Any], key: str) -> float:
    try:
        return float(payload.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def evaluate_webhook_inbox_health(
    metrics: dict[str, Any],
    *,
    max_due_count: int = DEFAULT_MAX_WEBHOOK_DUE_COUNT,
    max_failed_retryable_count: int = DEFAULT_MAX_WEBHOOK_FAILED_RETRYABLE_COUNT,
    max_dead_letter_count: int = DEFAULT_MAX_WEBHOOK_DEAD_LETTER_COUNT,
    max_oldest_age_seconds: int = DEFAULT_MAX_WEBHOOK_OLDEST_AGE_SECONDS,
) -> dict[str, Any]:
    thresholds = {
        "max_due_count": int(max_due_count),
        "max_failed_retryable_count": int(max_failed_retryable_count),
        "max_dead_letter_count": int(max_dead_letter_count),
        "max_oldest_age_seconds": int(max_oldest_age_seconds),
    }
    counts = {
        "due_count": _int_metric(metrics, "due_count"),
        "processing_count": _int_metric(metrics, "processing_count"),
        "failed_retryable_count": _int_metric(metrics, "failed_retryable_count"),
        "dead_letter_count": _int_metric(metrics, "dead_letter_count"),
        "oldest_received_age_seconds": _int_metric(metrics, "oldest_received_age_seconds"),
    }
    if metrics.get("checked") is False:
        return {
            "checked": False,
            "ok": None,
            "thresholds": thresholds,
            "counts": counts,
            "violations": [],
            "error": metrics.get("error") or "webhook_inbox metrics not checked",
        }
    if metrics.get("ok") is not True:
        return {
            "checked": bool(metrics.get("checked")),
            "ok": False,
            "thresholds": thresholds,
            "counts": counts,
            "violations": ["webhook_inbox metrics unavailable"],
            "error": metrics.get("error") or "webhook_inbox metrics unavailable",
        }

    violations: list[str] = []
    if counts["due_count"] > thresholds["max_due_count"]:
        violations.append(f"due_count {counts['due_count']} exceeds {thresholds['max_due_count']}")
    if counts["failed_retryable_count"] > thresholds["max_failed_retryable_count"]:
        violations.append(
            f"failed_retryable_count {counts['failed_retryable_count']} exceeds "
            f"{thresholds['max_failed_retryable_count']}"
        )
    if counts["dead_letter_count"] > thresholds["max_dead_letter_count"]:
        violations.append(f"dead_letter_count {counts['dead_letter_count']} exceeds {thresholds['max_dead_letter_count']}")
    if counts["oldest_received_age_seconds"] > thresholds["max_oldest_age_seconds"]:
        violations.append(
            f"oldest_received_age_seconds {counts['oldest_received_age_seconds']} exceeds "
            f"{thresholds['max_oldest_age_seconds']}"
        )
    return {
        "checked": True,
        "ok": not violations,
        "thresholds": thresholds,
        "counts": counts,
        "violations": violations,
        "error": "" if not violations else "; ".join(violations),
    }


def systemctl_is_active(unit: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:  # pragma: no cover - depends on host systemd
        return {"checked": False, "unit": unit, "active": False, "status": "", "error": str(exc)}
    status = _text(result.stdout) or _text(result.stderr)
    return {"checked": True, "unit": unit, "active": result.returncode == 0 and status == "active", "status": status, "error": ""}


def _service_state(unit: str, *, skip_systemctl: bool) -> dict[str, Any]:
    if skip_systemctl:
        return {"checked": False, "unit": unit, "active": None, "status": "", "error": "systemctl skipped"}
    return systemctl_is_active(unit)


def read_pressure_evidence(path: str) -> dict[str, Any]:
    evidence_path = _text(path)
    if not evidence_path:
        return {"checked": False, "ok": None, "path": "", "error": "pressure evidence not provided"}
    try:
        payload = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"checked": True, "ok": False, "path": evidence_path, "error": str(exc)}
    if not isinstance(payload, dict):
        return {"checked": True, "ok": False, "path": evidence_path, "error": "pressure evidence shape is invalid"}

    callback = payload.get("callback")
    page_samples = payload.get("page_samples")
    if not isinstance(callback, dict) or not isinstance(page_samples, dict):
        return {"checked": True, "ok": False, "path": evidence_path, "error": "pressure evidence shape is invalid"}

    page_targets = {
        str(label): bool(sample.get("meets_status_target") and sample.get("meets_p95_target"))
        for label, sample in page_samples.items()
        if isinstance(sample, dict)
    }
    sample_validation = payload.get("sample_validation") if isinstance(payload.get("sample_validation"), dict) else {}
    sample_validation_ok = sample_validation.get("ok") is True
    pressure = payload.get("pressure") if isinstance(payload.get("pressure"), dict) else {}
    try:
        requested_rate = float(pressure.get("requested_rate_per_minute") or 0)
    except (TypeError, ValueError):
        requested_rate = 0.0
    try:
        observed_rate = float(pressure.get("observed_rate_per_minute") or 0)
    except (TypeError, ValueError):
        observed_rate = 0.0
    required_pages = {"health", "sidebar_bind_mobile", "automation_conversion_admin"}
    missing_pages = sorted(required_pages - set(page_targets))
    rate_ok = requested_rate >= MIN_PRESSURE_RATE_PER_MINUTE and observed_rate >= MIN_PRESSURE_RATE_PER_MINUTE
    callback_latency = callback.get("latency_ms") if isinstance(callback.get("latency_ms"), dict) else {}
    callback_target_p95 = _float_metric(callback, "target_p95_ms")
    callback_target_p99 = _float_metric(callback, "target_p99_ms")
    callback_p95 = _float_metric(callback_latency, "p95")
    callback_p99 = _float_metric(callback_latency, "p99")
    callback_latency_target_ok = (
        0 < callback_target_p95 <= MAX_CALLBACK_P95_MS
        and 0 < callback_target_p99 <= MAX_CALLBACK_P99_MS
        and 0 < callback_p95 <= MAX_CALLBACK_P95_MS
        and 0 < callback_p99 <= MAX_CALLBACK_P99_MS
    )
    page_latency_targets = {
        "health": MAX_HEALTH_P95_MS,
        "sidebar_bind_mobile": MAX_SIDEBAR_P95_MS,
        "automation_conversion_admin": MAX_ADMIN_P95_MS,
    }
    page_latency_target_details: dict[str, dict[str, Any]] = {}
    for label, max_p95 in page_latency_targets.items():
        sample = page_samples.get(label) if isinstance(page_samples.get(label), dict) else {}
        latency = sample.get("latency_ms") if isinstance(sample.get("latency_ms"), dict) else {}
        target_p95 = _float_metric(sample, "target_p95_ms")
        observed_p95 = _float_metric(latency, "p95")
        page_latency_target_details[label] = {
            "target_p95_ms": target_p95,
            "max_allowed_p95_ms": max_p95,
            "observed_p95_ms": observed_p95,
            "ok": 0 < target_p95 <= max_p95 and 0 < observed_p95 <= max_p95,
        }
    page_latency_targets_ok = all(item["ok"] for item in page_latency_target_details.values())
    ok = bool(
        payload.get("ok") is True
        and payload.get("real_external_call_executed") is False
        and callback.get("meets_status_target") is True
        and callback.get("meets_p95_target") is True
        and callback.get("meets_p99_target") is True
        and callback_latency_target_ok
        and sample_validation_ok
        and rate_ok
        and not missing_pages
        and all(page_targets.values())
        and page_latency_targets_ok
    )
    return {
        "checked": True,
        "ok": ok,
        "path": evidence_path,
        "min_required_rate_per_minute": MIN_PRESSURE_RATE_PER_MINUTE,
        "requested_rate_per_minute": requested_rate,
        "observed_rate_per_minute": observed_rate,
        "rate_target_met": rate_ok,
        "callback_latency_ms": callback_latency,
        "callback_latency_target": {
            "target_p95_ms": callback_target_p95,
            "max_allowed_p95_ms": MAX_CALLBACK_P95_MS,
            "target_p99_ms": callback_target_p99,
            "max_allowed_p99_ms": MAX_CALLBACK_P99_MS,
            "observed_p95_ms": callback_p95,
            "observed_p99_ms": callback_p99,
            "ok": callback_latency_target_ok,
        },
        "callback_targets": {
            "status": bool(callback.get("meets_status_target")),
            "p95": bool(callback.get("meets_p95_target")),
            "p99": bool(callback.get("meets_p99_target")),
        },
        "page_targets": page_targets,
        "page_latency_targets": page_latency_target_details,
        "missing_page_samples": missing_pages,
        "sample_validation": sample_validation,
        "sample_validation_target_met": sample_validation_ok,
        "real_external_call_executed": payload.get("real_external_call_executed"),
        "error": "" if ok else "pressure evidence does not meet readiness targets",
    }


def read_worker_isolation_evidence(path: str) -> dict[str, Any]:
    evidence_path = _text(path)
    if not evidence_path:
        return {"checked": False, "ok": None, "path": "", "error": "worker isolation evidence not provided"}
    try:
        payload = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"checked": True, "ok": False, "path": evidence_path, "error": str(exc)}
    if not isinstance(payload, dict):
        return {"checked": True, "ok": False, "path": evidence_path, "error": "worker isolation evidence shape is invalid"}

    callback = payload.get("callback")
    pressure = payload.get("pressure") if isinstance(payload.get("pressure"), dict) else {}
    sample_validation = payload.get("sample_validation") if isinstance(payload.get("sample_validation"), dict) else {}
    if not isinstance(callback, dict):
        return {"checked": True, "ok": False, "path": evidence_path, "error": "worker isolation evidence shape is invalid"}
    try:
        total_requests = int(pressure.get("total_requests") or 0)
    except (TypeError, ValueError):
        total_requests = 0
    try:
        callback_request_count = int(callback.get("request_count") or 0)
    except (TypeError, ValueError):
        callback_request_count = 0
    callback_targets = {
        "status": bool(callback.get("meets_status_target")),
        "p95": bool(callback.get("meets_p95_target")),
        "p99": bool(callback.get("meets_p99_target")),
    }
    ok = bool(
        payload.get("ok") is True
        and payload.get("real_external_call_executed") is False
        and sample_validation.get("ok") is True
        and total_requests == 1
        and callback_request_count == 1
        and all(callback_targets.values())
    )
    return {
        "checked": True,
        "ok": ok,
        "path": evidence_path,
        "total_requests": total_requests,
        "callback_request_count": callback_request_count,
        "callback_targets": callback_targets,
        "sample_validation": sample_validation,
        "sample_validation_target_met": sample_validation.get("ok") is True,
        "real_external_call_executed": payload.get("real_external_call_executed"),
        "error": "" if ok else "worker isolation evidence does not meet readiness targets",
    }


def read_downstream_worker_isolation_evidence(path: str) -> dict[str, Any]:
    evidence_path = _text(path)
    if not evidence_path:
        return {"checked": False, "ok": None, "path": "", "error": "downstream worker isolation evidence not provided"}
    try:
        payload = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"checked": True, "ok": False, "path": evidence_path, "error": str(exc)}
    if not isinstance(payload, dict):
        return {"checked": True, "ok": False, "path": evidence_path, "error": "downstream worker isolation evidence shape is invalid"}

    callback = payload.get("callback")
    page_samples = payload.get("page_samples")
    pressure = payload.get("pressure") if isinstance(payload.get("pressure"), dict) else {}
    sample_validation = payload.get("sample_validation") if isinstance(payload.get("sample_validation"), dict) else {}
    if not isinstance(callback, dict) or not isinstance(page_samples, dict):
        return {"checked": True, "ok": False, "path": evidence_path, "error": "downstream worker isolation evidence shape is invalid"}
    try:
        total_requests = int(pressure.get("total_requests") or 0)
    except (TypeError, ValueError):
        total_requests = 0
    try:
        callback_request_count = int(callback.get("request_count") or 0)
    except (TypeError, ValueError):
        callback_request_count = 0
    required_pages = {"health", "sidebar_bind_mobile", "automation_conversion_admin"}
    page_targets = {
        str(label): bool(sample.get("meets_status_target") and sample.get("meets_p95_target"))
        for label, sample in page_samples.items()
        if isinstance(sample, dict)
    }
    missing_pages = sorted(required_pages - set(page_targets))
    callback_targets = {
        "status": bool(callback.get("meets_status_target")),
        "p95": bool(callback.get("meets_p95_target")),
        "p99": bool(callback.get("meets_p99_target")),
    }
    ok = bool(
        payload.get("ok") is True
        and payload.get("real_external_call_executed") is False
        and sample_validation.get("ok") is True
        and total_requests == 1
        and callback_request_count == 1
        and all(callback_targets.values())
        and not missing_pages
        and all(page_targets.values())
    )
    return {
        "checked": True,
        "ok": ok,
        "path": evidence_path,
        "total_requests": total_requests,
        "callback_request_count": callback_request_count,
        "callback_targets": callback_targets,
        "page_targets": page_targets,
        "missing_page_samples": missing_pages,
        "sample_validation": sample_validation,
        "sample_validation_target_met": sample_validation.get("ok") is True,
        "real_external_call_executed": payload.get("real_external_call_executed"),
        "error": "" if ok else "downstream worker isolation evidence does not meet readiness targets",
    }


def read_internal_event_worker_isolation_evidence(path: str) -> dict[str, Any]:
    evidence_path = _text(path)
    if not evidence_path:
        return {"checked": False, "ok": None, "path": "", "error": "internal event worker isolation evidence not provided"}
    try:
        payload = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"checked": True, "ok": False, "path": evidence_path, "error": str(exc)}
    if not isinstance(payload, dict):
        return {"checked": True, "ok": False, "path": evidence_path, "error": "internal event worker isolation evidence shape is invalid"}

    callback = payload.get("callback")
    page_samples = payload.get("page_samples")
    pressure = payload.get("pressure") if isinstance(payload.get("pressure"), dict) else {}
    sample_validation = payload.get("sample_validation") if isinstance(payload.get("sample_validation"), dict) else {}
    if not isinstance(callback, dict) or not isinstance(page_samples, dict):
        return {"checked": True, "ok": False, "path": evidence_path, "error": "internal event worker isolation evidence shape is invalid"}
    try:
        total_requests = int(pressure.get("total_requests") or 0)
    except (TypeError, ValueError):
        total_requests = 0
    try:
        callback_request_count = int(callback.get("request_count") or 0)
    except (TypeError, ValueError):
        callback_request_count = 0
    required_pages = {"health", "sidebar_bind_mobile", "automation_conversion_admin"}
    page_targets = {
        str(label): bool(sample.get("meets_status_target") and sample.get("meets_p95_target"))
        for label, sample in page_samples.items()
        if isinstance(sample, dict)
    }
    missing_pages = sorted(required_pages - set(page_targets))
    callback_targets = {
        "status": bool(callback.get("meets_status_target")),
        "p95": bool(callback.get("meets_p95_target")),
        "p99": bool(callback.get("meets_p99_target")),
    }
    ok = bool(
        payload.get("ok") is True
        and payload.get("real_external_call_executed") is False
        and sample_validation.get("ok") is True
        and total_requests == 1
        and callback_request_count == 1
        and all(callback_targets.values())
        and not missing_pages
        and all(page_targets.values())
    )
    return {
        "checked": True,
        "ok": ok,
        "path": evidence_path,
        "total_requests": total_requests,
        "callback_request_count": callback_request_count,
        "callback_targets": callback_targets,
        "page_targets": page_targets,
        "missing_page_samples": missing_pages,
        "sample_validation": sample_validation,
        "sample_validation_target_met": sample_validation.get("ok") is True,
        "real_external_call_executed": payload.get("real_external_call_executed"),
        "error": "" if ok else "internal event worker isolation evidence does not meet readiness targets",
    }


def read_webhook_ingestion_evidence(path: str) -> dict[str, Any]:
    evidence_path = _text(path)
    if not evidence_path:
        return {"checked": False, "ok": None, "path": "", "error": "webhook_inbox ingestion evidence not provided"}
    try:
        payload = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"checked": True, "ok": False, "path": evidence_path, "error": str(exc)}
    if not isinstance(payload, dict):
        return {"checked": True, "ok": False, "path": evidence_path, "error": "webhook_inbox ingestion evidence shape is invalid"}

    row = payload.get("webhook_inbox_row") if isinstance(payload.get("webhook_inbox_row"), dict) else {}
    requirements = payload.get("requirements") if isinstance(payload.get("requirements"), dict) else {}
    ok = bool(
        payload.get("ok") is True
        and bool(_text(payload.get("idempotency_key")))
        and row.get("found") is True
        and row.get("provider") == "wecom"
        and row.get("event_family") == "external_contact"
        and _text(row.get("idempotency_key")) == _text(payload.get("idempotency_key"))
    )
    return {
        "checked": True,
        "ok": ok,
        "path": evidence_path,
        "idempotency_key": _text(payload.get("idempotency_key")),
        "requirements": requirements,
        "webhook_inbox_row": row,
        "violations": payload.get("violations") if isinstance(payload.get("violations"), list) else [],
        "error": "" if ok else payload.get("error") or "webhook_inbox ingestion evidence does not meet readiness targets",
    }


def read_webhook_processing_evidence(path: str) -> dict[str, Any]:
    evidence_path = _text(path)
    if not evidence_path:
        return {"checked": False, "ok": None, "path": "", "error": "webhook_inbox processing evidence not provided"}
    try:
        payload = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"checked": True, "ok": False, "path": evidence_path, "error": str(exc)}
    if not isinstance(payload, dict):
        return {"checked": True, "ok": False, "path": evidence_path, "error": "webhook_inbox processing evidence shape is invalid"}

    row = payload.get("webhook_inbox_row") if isinstance(payload.get("webhook_inbox_row"), dict) else {}
    summary = row.get("processing_summary_json") if isinstance(row.get("processing_summary_json"), dict) else {}
    requirements = payload.get("requirements") if isinstance(payload.get("requirements"), dict) else {}
    ok = bool(
        payload.get("ok") is True
        and bool(_text(payload.get("idempotency_key")))
        and row.get("found") is True
        and row.get("provider") == "wecom"
        and row.get("event_family") == "external_contact"
        and _text(row.get("idempotency_key")) == _text(payload.get("idempotency_key"))
        and row.get("status") == "succeeded"
        and bool(_text(row.get("started_at")))
        and bool(_text(row.get("finished_at")))
        and summary.get("handled") is False
        and _text(summary.get("identity_sync_status")) == "skipped"
        and summary.get("external_effect_job_ids") in ([], None)
    )
    return {
        "checked": True,
        "ok": ok,
        "path": evidence_path,
        "idempotency_key": _text(payload.get("idempotency_key")),
        "requirements": requirements,
        "webhook_inbox_row": row,
        "violations": payload.get("violations") if isinstance(payload.get("violations"), list) else [],
        "error": "" if ok else payload.get("error") or "webhook_inbox processing evidence does not meet readiness targets",
    }


def evaluate_same_sample_evidence(
    *,
    pressure_evidence: dict[str, Any],
    webhook_ingestion_evidence: dict[str, Any],
    webhook_processing_evidence: dict[str, Any],
) -> dict[str, Any]:
    keys = {
        "pressure": _text((pressure_evidence.get("sample_validation") or {}).get("idempotency_key")),
        "ingestion": _text(webhook_ingestion_evidence.get("idempotency_key")),
        "processing": _text(webhook_processing_evidence.get("idempotency_key")),
    }
    checked = all(
        evidence.get("checked") is True
        for evidence in (pressure_evidence, webhook_ingestion_evidence, webhook_processing_evidence)
    )
    if not checked:
        return {
            "checked": False,
            "ok": None,
            "idempotency_keys": keys,
            "error": "pressure, ingestion, and processing evidence are not all checked",
        }
    if not all(
        evidence.get("ok") is True
        for evidence in (pressure_evidence, webhook_ingestion_evidence, webhook_processing_evidence)
    ):
        return {
            "checked": True,
            "ok": False,
            "idempotency_keys": keys,
            "error": "pressure, ingestion, and processing evidence must all pass before same-sample validation",
        }
    unique_keys = {key for key in keys.values() if key}
    ok = len(unique_keys) == 1 and all(keys.values())
    return {
        "checked": True,
        "ok": ok,
        "idempotency_keys": keys,
        "error": "" if ok else "pressure, ingestion, and processing evidence do not share the same idempotency_key",
    }


def read_public_state_evidence(path: str) -> dict[str, Any]:
    evidence_path = _text(path)
    if not evidence_path:
        return {"checked": False, "ok": None, "path": "", "error": "public state evidence not provided"}
    try:
        payload = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"checked": True, "ok": False, "path": evidence_path, "error": str(exc)}
    if not isinstance(payload, dict):
        return {"checked": True, "ok": False, "path": evidence_path, "error": "public state evidence shape is invalid"}

    callback_route_signals = payload.get("callback_route_signals") if isinstance(payload.get("callback_route_signals"), list) else []
    callback_route_checks: dict[str, bool] = {}
    for index, item in enumerate(callback_route_signals):
        if not isinstance(item, dict):
            callback_route_checks[f"route_{index + 1}"] = False
            continue
        callback_route_checks[_text(item.get("path")) or f"route_{index + 1}"] = bool(
            item.get("checked") is True
            and item.get("plain_success") is False
            and item.get("app_level_callback_signal") is True
        )
    dual_callback_route_signal = bool(
        len(callback_route_signals) >= 2
        and all(callback_route_checks.values())
        and any(_text(item.get("path")).startswith("/wecom/external-contact/callback") for item in callback_route_signals if isinstance(item, dict))
        and any(_text(item.get("path")).startswith("/api/wecom/events") for item in callback_route_signals if isinstance(item, dict))
    )
    checks = {
        "ok": payload.get("ok") is True,
        "permanent_fix_public_signals_ready": payload.get("permanent_fix_public_signals_ready") is True,
        "user_facing_available": payload.get("user_facing_available") is True,
        "admin_webhook_inbox_api_deployed": payload.get("admin_webhook_inbox_api_deployed") is True,
        "admin_webhook_inbox_detail_route_deployed": payload.get("admin_webhook_inbox_detail_route_deployed") is True,
        "invalid_callback_plain_success": payload.get("invalid_callback_plain_success") is False,
        "app_level_callback_signal": payload.get("app_level_callback_signal") is True,
        "dual_callback_route_signal": dual_callback_route_signal,
    }
    ok = all(checks.values())
    return {
        "checked": True,
        "ok": ok,
        "path": evidence_path,
        "checks": checks,
        "base_url": payload.get("base_url"),
        "callback_route_checks": callback_route_checks,
        "warnings": payload.get("warnings") if isinstance(payload.get("warnings"), list) else [],
        "error": "" if ok else "public state evidence does not prove deployed webhook inbox and app-level callback rejection for both callback routes",
    }


def read_deploy_smoke_evidence(path: str) -> dict[str, Any]:
    evidence_path = _text(path)
    if not evidence_path:
        return {"checked": False, "ok": None, "path": "", "error": "deploy smoke evidence not provided"}
    try:
        payload = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"checked": True, "ok": False, "path": evidence_path, "error": str(exc)}
    if not isinstance(payload, dict):
        return {"checked": True, "ok": False, "path": evidence_path, "error": "deploy smoke evidence shape is invalid"}

    ingress_callback_route_signals = (
        payload.get("ingress_callback_route_signals")
        if isinstance(payload.get("ingress_callback_route_signals"), list)
        else []
    )
    ingress_callback_route_checks: dict[str, bool] = {}
    for index, item in enumerate(ingress_callback_route_signals):
        if not isinstance(item, dict):
            ingress_callback_route_checks[f"route_{index + 1}"] = False
            continue
        ingress_callback_route_checks[_text(item.get("path")) or f"route_{index + 1}"] = bool(
            item.get("checked") is True
            and item.get("plain_success") is False
            and item.get("app_level_callback_signal") is True
        )
    dual_ingress_callback_route_signal = bool(
        payload.get("ingress_callback_routes_ready") is True
        and len(ingress_callback_route_signals) >= 2
        and all(ingress_callback_route_checks.values())
        and any(_text(item.get("path")).startswith("/wecom/external-contact/callback") for item in ingress_callback_route_signals if isinstance(item, dict))
        and any(_text(item.get("path")).startswith("/api/wecom/events") for item in ingress_callback_route_signals if isinstance(item, dict))
    )
    checks = {
        "ok": payload.get("ok") is True,
        "base_urls_distinct": payload.get("base_urls_distinct") is True,
        "web_health_ok": payload.get("web_health_ok") is True,
        "ingress_health_ok": payload.get("ingress_health_ok") is True,
        "ingress_durable_ack_ready": payload.get("ingress_durable_ack_ready") is True,
        "admin_api_deployed": payload.get("admin_api_deployed") is True,
        "admin_detail_route_deployed": payload.get("admin_detail_route_deployed") is True,
        "dual_ingress_callback_route_signal": dual_ingress_callback_route_signal,
    }
    ok = all(checks.values())
    return {
        "checked": True,
        "ok": ok,
        "path": evidence_path,
        "checks": checks,
        "web_base_url": payload.get("web_base_url"),
        "ingress_base_url": payload.get("ingress_base_url"),
        "ingress_callback_route_checks": ingress_callback_route_checks,
        "warnings": payload.get("warnings") if isinstance(payload.get("warnings"), list) else [],
        "error": "" if ok else "deploy smoke evidence does not prove distinct web/ingress runtimes, durable-only ACK, admin API, detail routes, and ingress callback routes are deployed",
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate readiness checks for the WeCom callback permanent fix.")
    parser.add_argument("--nginx-config", default=DEFAULT_NGINX_CONFIG)
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--web-health-url", default=DEFAULT_WEB_HEALTH_URL)
    parser.add_argument("--ingress-health-url", default=DEFAULT_INGRESS_HEALTH_URL)
    parser.add_argument("--admin-webhook-inbox-metrics-url", default=DEFAULT_ADMIN_WEBHOOK_INBOX_METRICS_URL)
    parser.add_argument("--admin-webhook-inbox-items-url", default=DEFAULT_ADMIN_WEBHOOK_INBOX_ITEMS_URL)
    parser.add_argument("--admin-webhook-inbox-reconciliation-url", default=DEFAULT_ADMIN_WEBHOOK_INBOX_RECONCILIATION_URL)
    parser.add_argument("--probe-timeout", type=float, default=2.0)
    parser.add_argument("--callback-ingress-service", default=DEFAULT_CALLBACK_INGRESS_SERVICE)
    parser.add_argument("--callback-worker-service", default=DEFAULT_CALLBACK_WORKER_SERVICE)
    parser.add_argument("--pressure-evidence-file", default="")
    parser.add_argument("--ingestion-evidence-file", default="")
    parser.add_argument("--processing-evidence-file", default="")
    parser.add_argument("--worker-isolation-evidence-file", default="")
    parser.add_argument("--internal-event-worker-isolation-evidence-file", default="")
    parser.add_argument("--downstream-worker-isolation-evidence-file", default="")
    parser.add_argument("--rollback-evidence-file", default="")
    parser.add_argument("--public-state-evidence-file", default="")
    parser.add_argument("--deploy-smoke-evidence-file", default="")
    parser.add_argument("--max-webhook-due-count", type=int, default=DEFAULT_MAX_WEBHOOK_DUE_COUNT)
    parser.add_argument("--max-webhook-failed-retryable-count", type=int, default=DEFAULT_MAX_WEBHOOK_FAILED_RETRYABLE_COUNT)
    parser.add_argument("--max-webhook-dead-letter-count", type=int, default=DEFAULT_MAX_WEBHOOK_DEAD_LETTER_COUNT)
    parser.add_argument("--max-webhook-oldest-age-seconds", type=int, default=DEFAULT_MAX_WEBHOOK_OLDEST_AGE_SECONDS)
    parser.add_argument("--skip-systemctl", action="store_true", default=False)
    parser.add_argument("--skip-db", action="store_true", default=False)
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> dict[str, Any]:
    args = _parse_args(argv)
    env_loaded = _load_env_file(str(args.env_file))
    quick_ack = run_quick_ack_check(
        [
            "--nginx-config",
            str(args.nginx_config),
            "--env-file",
            str(args.env_file),
            "--skip-probe",
        ]
    )
    cutover = run_cutover_check(
        [
            "--nginx-config",
            str(args.nginx_config),
            "--ingress-health-url",
            str(args.ingress_health_url),
            "--probe-timeout",
            str(args.probe_timeout),
        ]
    )
    web_health = probe_health(str(args.web_health_url), float(args.probe_timeout))
    ingress_health = probe_health(str(args.ingress_health_url), float(args.probe_timeout))
    admin_webhook_inbox_metrics = probe_json_ok(
        str(args.admin_webhook_inbox_metrics_url),
        float(args.probe_timeout),
        required_list_paths=(
            "queue_metrics.provider_distribution",
            "queue_metrics.route_distribution",
            "queue_metrics.recent_errors",
        ),
    )
    admin_webhook_inbox_items = probe_json_ok(str(args.admin_webhook_inbox_items_url), float(args.probe_timeout), required_list_key="items")
    admin_webhook_inbox_reconciliation = probe_json_ok(
        str(args.admin_webhook_inbox_reconciliation_url),
        float(args.probe_timeout),
        required_list_key="recent_items",
    )
    inbox_metrics = (
        {"checked": False, "ok": None, "schema_present": None, "error": "database check skipped"}
        if args.skip_db
        else read_webhook_inbox_metrics()
    )
    inbox_health = evaluate_webhook_inbox_health(
        inbox_metrics,
        max_due_count=int(args.max_webhook_due_count),
        max_failed_retryable_count=int(args.max_webhook_failed_retryable_count),
        max_dead_letter_count=int(args.max_webhook_dead_letter_count),
        max_oldest_age_seconds=int(args.max_webhook_oldest_age_seconds),
    )
    services = {
        "callback_ingress": _service_state(str(args.callback_ingress_service), skip_systemctl=bool(args.skip_systemctl)),
        "callback_worker_service": _service_state(str(args.callback_worker_service), skip_systemctl=bool(args.skip_systemctl)),
    }
    pressure_evidence = read_pressure_evidence(str(args.pressure_evidence_file))
    webhook_ingestion_evidence = read_webhook_ingestion_evidence(str(args.ingestion_evidence_file))
    webhook_processing_evidence = read_webhook_processing_evidence(str(args.processing_evidence_file))
    worker_isolation_evidence = read_worker_isolation_evidence(str(args.worker_isolation_evidence_file))
    internal_event_worker_isolation_evidence = read_internal_event_worker_isolation_evidence(str(args.internal_event_worker_isolation_evidence_file))
    downstream_worker_isolation_evidence = read_downstream_worker_isolation_evidence(str(args.downstream_worker_isolation_evidence_file))
    same_sample_evidence = evaluate_same_sample_evidence(
        pressure_evidence=pressure_evidence,
        webhook_ingestion_evidence=webhook_ingestion_evidence,
        webhook_processing_evidence=webhook_processing_evidence,
    )
    public_state_evidence = read_public_state_evidence(str(args.public_state_evidence_file))
    deploy_smoke_evidence = read_deploy_smoke_evidence(str(args.deploy_smoke_evidence_file))
    try:
        from scripts.ops.check_wecom_callback_rollback_evidence import read_rollback_evidence
    except ModuleNotFoundError:  # pragma: no cover - direct script execution
        from check_wecom_callback_rollback_evidence import read_rollback_evidence

    rollback_evidence = read_rollback_evidence(str(args.rollback_evidence_file))
    service_ok = all(item.get("active") is True for item in services.values()) if not args.skip_systemctl else True
    db_ok = bool(inbox_metrics.get("ok")) if not args.skip_db else True
    inbox_health_ok = inbox_health.get("ok") is True
    pressure_ok = pressure_evidence.get("ok") is True
    webhook_ingestion_ok = webhook_ingestion_evidence.get("ok") is True
    webhook_processing_ok = webhook_processing_evidence.get("ok") is True
    same_sample_ok = same_sample_evidence.get("ok") is True
    worker_isolation_ok = worker_isolation_evidence.get("ok") is True
    internal_event_worker_isolation_ok = internal_event_worker_isolation_evidence.get("ok") is True
    downstream_worker_isolation_ok = downstream_worker_isolation_evidence.get("ok") is True
    rollback_ok = rollback_evidence.get("ok") is True
    public_state_ok = public_state_evidence.get("ok") is True
    deploy_smoke_ok = deploy_smoke_evidence.get("ok") is True
    admin_webhook_inbox_metrics_ok = admin_webhook_inbox_metrics.get("ok") is True
    admin_webhook_inbox_items_ok = admin_webhook_inbox_items.get("ok") is True
    admin_webhook_inbox_reconciliation_ok = admin_webhook_inbox_reconciliation.get("ok") is True
    cutover_ready = bool(
        web_health.get("ok")
        and ingress_health.get("ok")
        and admin_webhook_inbox_metrics_ok
        and admin_webhook_inbox_items_ok
        and admin_webhook_inbox_reconciliation_ok
        and db_ok
        and service_ok
        and quick_ack.get("ok") is True
        and quick_ack.get("emergency_quick_ack_enabled") is False
        and cutover.get("ready_for_cutover") is True
    )
    completion_ready = bool(
        cutover_ready
        and pressure_ok
        and webhook_ingestion_ok
        and webhook_processing_ok
        and same_sample_ok
        and inbox_health_ok
        and worker_isolation_ok
        and internal_event_worker_isolation_ok
        and downstream_worker_isolation_ok
        and rollback_ok
        and public_state_ok
        and deploy_smoke_ok
    )
    warnings: list[str] = []
    if quick_ack.get("emergency_quick_ack_enabled"):
        warnings.append("emergency quick ACK is still enabled")
    if quick_ack.get("ok") is not True:
        warnings.append(f"quick ACK state check failed: {quick_ack.get('nginx_error') or quick_ack.get('database_error') or quick_ack.get('callback_post_error') or 'unknown error'}")
    if not cutover.get("ready_for_cutover"):
        warnings.append("nginx cutover to 5002 is not ready")
    if not web_health.get("ok"):
        warnings.append("5001 web health is not healthy")
    if not ingress_health.get("ok"):
        warnings.append("5002 ingress health is not healthy")
    if not admin_webhook_inbox_metrics_ok:
        warnings.append(f"admin webhook inbox metrics API is not available: {admin_webhook_inbox_metrics.get('error') or admin_webhook_inbox_metrics.get('status_code')}")
    if not admin_webhook_inbox_items_ok:
        warnings.append(f"admin webhook inbox items API is not available: {admin_webhook_inbox_items.get('error') or admin_webhook_inbox_items.get('status_code')}")
    if not admin_webhook_inbox_reconciliation_ok:
        warnings.append(
            "admin webhook inbox reconciliation API is not available: "
            f"{admin_webhook_inbox_reconciliation.get('error') or admin_webhook_inbox_reconciliation.get('status_code')}"
        )
    if not db_ok:
        warnings.append(f"webhook_inbox metrics unavailable: {inbox_metrics.get('error')}")
    if args.skip_db:
        warnings.append("webhook_inbox health not checked; production completion requires live DB metrics")
    elif inbox_health.get("ok") is False:
        warnings.append(f"webhook_inbox health failed readiness targets: {inbox_health.get('error')}")
    if not service_ok:
        warnings.append("callback ingress service or callback worker timer is not active")
    if pressure_evidence.get("ok") is False:
        warnings.append(f"pressure evidence failed readiness targets: {pressure_evidence.get('error')}")
    if pressure_evidence.get("checked") is False:
        warnings.append("pressure evidence not provided; production completion still requires 1200/min pressure evidence")
    if webhook_ingestion_evidence.get("ok") is False:
        warnings.append(f"webhook_inbox ingestion evidence failed readiness targets: {webhook_ingestion_evidence.get('error')}")
    if webhook_ingestion_evidence.get("checked") is False:
        warnings.append("webhook_inbox ingestion evidence not provided; production completion still requires valid callback DB ingestion proof")
    if webhook_processing_evidence.get("ok") is False:
        warnings.append(f"webhook_inbox processing evidence failed readiness targets: {webhook_processing_evidence.get('error')}")
    if webhook_processing_evidence.get("checked") is False:
        warnings.append("webhook_inbox processing evidence not provided; production completion still requires worker consumption proof")
    if same_sample_evidence.get("ok") is False:
        warnings.append(f"same-sample pressure/ingestion/processing evidence failed: {same_sample_evidence.get('error')}")
    if worker_isolation_evidence.get("ok") is False:
        warnings.append(f"worker isolation evidence failed readiness targets: {worker_isolation_evidence.get('error')}")
    if worker_isolation_evidence.get("checked") is False:
        warnings.append("worker isolation evidence not provided; production completion still requires callback ACK proof while worker is stopped")
    if internal_event_worker_isolation_evidence.get("ok") is False:
        warnings.append(f"internal event worker isolation evidence failed readiness targets: {internal_event_worker_isolation_evidence.get('error')}")
    if internal_event_worker_isolation_evidence.get("checked") is False:
        warnings.append("internal event worker isolation evidence not provided; production completion still requires page and ACK proof while internal event workers are stopped")
    if downstream_worker_isolation_evidence.get("ok") is False:
        warnings.append(f"downstream worker isolation evidence failed readiness targets: {downstream_worker_isolation_evidence.get('error')}")
    if downstream_worker_isolation_evidence.get("checked") is False:
        warnings.append("downstream worker isolation evidence not provided; production completion still requires page and ACK proof while downstream workers are stopped")
    if rollback_evidence.get("ok") is False:
        warnings.append(f"rollback evidence failed readiness targets: {rollback_evidence.get('error')}")
    if rollback_evidence.get("checked") is False:
        warnings.append("rollback evidence not provided; production completion still requires rollback drill proof")
    if public_state_evidence.get("ok") is False:
        warnings.append(f"public state evidence failed readiness targets: {public_state_evidence.get('error')}")
    if public_state_evidence.get("checked") is False:
        warnings.append("public state evidence not provided; production completion still requires public HTTP proof")
    if deploy_smoke_evidence.get("ok") is False:
        warnings.append(f"deploy smoke evidence failed readiness targets: {deploy_smoke_evidence.get('error')}")
    if deploy_smoke_evidence.get("checked") is False:
        warnings.append("deploy smoke evidence not provided; production completion still requires post-deploy web/ingress/admin smoke proof")
    return {
        "ok": completion_ready,
        "ready_for_production_cutover": cutover_ready,
        "ready_for_production_completion": completion_ready,
        "env_file": str(args.env_file),
        "env_file_loaded": env_loaded,
        "web_health": web_health,
        "ingress_health": ingress_health,
        "admin_webhook_inbox_metrics": admin_webhook_inbox_metrics,
        "admin_webhook_inbox_items": admin_webhook_inbox_items,
        "admin_webhook_inbox_reconciliation": admin_webhook_inbox_reconciliation,
        "quick_ack": quick_ack,
        "cutover": cutover,
        "webhook_inbox": inbox_metrics,
        "webhook_inbox_health": inbox_health,
        "webhook_ingestion_evidence": webhook_ingestion_evidence,
        "webhook_processing_evidence": webhook_processing_evidence,
        "same_sample_evidence": same_sample_evidence,
        "services": services,
        "pressure_evidence": pressure_evidence,
        "worker_isolation_evidence": worker_isolation_evidence,
        "internal_event_worker_isolation_evidence": internal_event_worker_isolation_evidence,
        "downstream_worker_isolation_evidence": downstream_worker_isolation_evidence,
        "rollback_evidence": rollback_evidence,
        "public_state_evidence": public_state_evidence,
        "deploy_smoke_evidence": deploy_smoke_evidence,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    payload = run(argv)
    print_json(payload, indent=2)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
