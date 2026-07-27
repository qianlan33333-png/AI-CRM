from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone
from typing import Any

from .admin_refunds import list_refunds
from .admin_unified_orders import ROUTE_OWNER, list_orders, list_payments

_EXPORT_JOBS: dict[str, dict[str, Any]] = {}
_SUPPORTED_RESOURCES = {"orders", "payments", "refunds", "customer_business_profile"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_csv(rows: list[dict[str, Any]], columns: list[str]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue()


def _orders_csv(filters: dict[str, Any]) -> str:
    payload = list_orders(provider=_text(filters.get("provider") or "all"), filters=filters, limit=100, offset=0)
    return _write_csv(
        list(payload.get("items") or []),
        ["provider", "order_no", "transaction_id", "mobile", "product_code", "product_name", "amount_total", "amount_yuan", "currency", "status", "created_at", "paid_at"],
    )


def _payments_csv(filters: dict[str, Any]) -> str:
    payload = list_payments(provider=_text(filters.get("provider") or "all"), filters=filters, limit=100, offset=0)
    return _write_csv(
        list(payload.get("payments") or []),
        ["provider", "order_no", "transaction_id", "mobile", "amount_total", "currency", "payment_status", "paid_at", "raw_status", "provider_status"],
    )


def _refunds_csv(filters: dict[str, Any]) -> str:
    payload = list_refunds(provider=_text(filters.get("provider") or "all"), filters=filters, limit=100, offset=0)
    return _write_csv(
        list(payload.get("refunds") or []),
        ["provider", "order_no", "transaction_id", "out_refund_no", "refund_id", "refund_amount_total", "currency", "status", "reason", "created_at"],
    )


def _customer_business_profile_csv(filters: dict[str, Any]) -> str:
    external_userid = _text(filters.get("external_userid"))
    return _write_csv([{"external_userid": external_userid, "note": "Use GET /api/admin/customers/{external_userid}/business-profile for live JSON payload"}], ["external_userid", "note"])


def create_export_job(payload: dict[str, Any]) -> dict[str, Any]:
    resource = _text(payload.get("resource") or "orders")
    export_format = _text(payload.get("format") or "csv").lower()
    filters = dict(payload.get("filters") or {})
    if resource not in _SUPPORTED_RESOURCES:
        raise ValueError("unsupported export resource")
    if export_format != "csv":
        raise ValueError("only csv export is supported in this slice")
    content = {
        "orders": _orders_csv,
        "payments": _payments_csv,
        "refunds": _refunds_csv,
        "customer_business_profile": _customer_business_profile_csv,
    }[resource](filters)
    job_id = f"exp_{uuid.uuid4().hex[:12]}"
    job = {
        "job_id": job_id,
        "resource": resource,
        "format": export_format,
        "status": "completed",
        "created_at": _now(),
        "operator": _text(payload.get("operator")),
        "download_url": f"/api/admin/exports/{job_id}",
        "source_status": "next_export_in_memory",
    }
    _EXPORT_JOBS[job_id] = {
        "job": job,
        "content_type": "text/csv; charset=utf-8",
        "file_name": f"{resource}-{job_id}.csv",
        "content_text": content,
    }
    return {
        "ok": True,
        "job": job,
        "route_owner": ROUTE_OWNER,
        "source_status": "next_admin_exports",
        "fallback_used": False,
    }


def get_export_job(job_id: str) -> dict[str, Any]:
    record = _EXPORT_JOBS.get(_text(job_id))
    if not record:
        raise LookupError("export job not found")
    return {
        "ok": True,
        **record,
        "route_owner": ROUTE_OWNER,
        "source_status": "next_admin_export_result",
        "fallback_used": False,
    }


def reset_export_jobs_for_tests() -> None:
    _EXPORT_JOBS.clear()
