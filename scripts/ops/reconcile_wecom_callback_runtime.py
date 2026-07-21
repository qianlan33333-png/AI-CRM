#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

try:
    from scripts.script_runtime import REPO_ROOT, ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from script_runtime import REPO_ROOT, ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()

from aicrm_next.integration_gateway.wecom_runtime import load_wecom_execution_config
from aicrm_next.shared.release import current_release_sha
from aicrm_next.shared.runtime import raw_database_url


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def read_count_only_inbox_state(database_url: str = "") -> dict[str, Any]:
    url = _psycopg_url(str(database_url or raw_database_url() or "").strip())
    if not url.startswith(("postgresql://", "postgres://")):
        return {"checked": False, "error": "database_url_missing"}
    try:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(url, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  COUNT(*) FILTER (WHERE status = 'received') AS received_count,
                  COUNT(*) FILTER (WHERE status = 'processing') AS processing_count,
                  COUNT(*) FILTER (WHERE status = 'failed_retryable') AS failed_retryable_count,
                  COUNT(*) FILTER (WHERE status = 'failed_terminal') AS failed_terminal_count,
                  COUNT(*) FILTER (WHERE status = 'dead_letter') AS dead_letter_count,
                  COUNT(*) FILTER (
                    WHERE status IN ('received', 'failed_retryable')
                      AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
                  ) AS due_count,
                  COALESCE(SUM(duplicate_count), 0) AS duplicate_collapsed_count,
                  COALESCE(
                    EXTRACT(EPOCH FROM (
                      CURRENT_TIMESTAMP - MIN(received_at)
                        FILTER (WHERE status IN ('received', 'processing', 'failed_retryable'))
                    )),
                    0
                  )::BIGINT AS oldest_pending_age_seconds
                FROM webhook_inbox
                WHERE provider = 'wecom'
                """
            )
            row = dict(cur.fetchone() or {})
    except Exception as exc:
        return {"checked": False, "error": exc.__class__.__name__}
    return {"checked": True, **{key: int(value or 0) for key, value in row.items()}}


def static_boundary_state(root: Path = REPO_ROOT) -> dict[str, int]:
    callback_source = (root / "aicrm_next/channel_entry/inbox.py").read_text(encoding="utf-8")
    ingress_source = (root / "aicrm_next/channel_entry/callback_ingress.py").read_text(encoding="utf-8")
    realtime_source = (root / "aicrm_next/platform_foundation/external_effects/realtime.py").read_text(encoding="utf-8")
    application_source = (root / "aicrm_next/channel_entry/application.py").read_text(encoding="utf-8")
    manifest = json.loads((root / "deploy/production_runtime_units.json").read_text(encoding="utf-8"))
    retired = set(manifest.get("retired_forbidden") or [])
    active_services = {str(item.get("service") or "") for item in manifest.get("active_services") or []}
    cutover_managed = manifest.get("cutover_managed_legacy") or {}
    managed_legacy_services = {
        str(item.get("service") or "")
        for item in cutover_managed.get("persistent_services") or []
    }
    return {
        "inline_dispatch_reference_count": callback_source.count("process_time_sensitive") + callback_source.count("ingress-inline") + ingress_source.count("process_time_sensitive"),
        "process_local_executor_reference_count": realtime_source.count("ThreadPoolExecutor") + realtime_source.count("_EXECUTOR.submit"),
        "welcome_fallback_cancel_reference_count": application_source.count("welcome_realtime_not_scheduled") + application_source.count("channel_entry_welcome_fallback"),
        "retired_timer_manifest_count": int("openclaw-wecom-callback-inbox-worker.timer" in retired),
        "durable_inbox_runtime_manifest_count": int("aicrm-inbox-queue-runtime.service" in active_services),
        "persistent_worker_manifest_count": int("aicrm-inbox-queue-runtime.service" in active_services),
        "legacy_persistent_worker_active_count": int("openclaw-wecom-callback-inbox-worker.service" in active_services),
        "legacy_persistent_worker_managed_count": int("openclaw-wecom-callback-inbox-worker.service" in managed_legacy_services),
    }


def retired_timer_state(*, skip_systemctl: bool) -> dict[str, Any]:
    unit = "openclaw-wecom-callback-inbox-worker.timer"
    if skip_systemctl:
        return {"checked": False, "unit": unit, "active": None}
    proc = subprocess.run(["systemctl", "is-active", unit], text=True, capture_output=True, check=False)
    return {"checked": True, "unit": unit, "active": proc.returncode == 0, "status": proc.stdout.strip()}


def run(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="Count-only reconciliation for the durable WeCom callback runtime.")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--skip-systemctl", action="store_true", default=False)
    args = parser.parse_args(argv)

    inbox = read_count_only_inbox_state(str(args.database_url))
    static = static_boundary_state()
    retired_timer = retired_timer_state(skip_systemctl=bool(args.skip_systemctl))
    config = load_wecom_execution_config().diagnostics()
    unsafe_count = (
        int(static["inline_dispatch_reference_count"])
        + int(static["process_local_executor_reference_count"])
        + int(static["welcome_fallback_cancel_reference_count"])
        + int(static["retired_timer_manifest_count"] != 1)
        + int(static["durable_inbox_runtime_manifest_count"] != 1)
        + int(static["legacy_persistent_worker_active_count"] != 0)
        + int(static["legacy_persistent_worker_managed_count"] != 1)
        + int(retired_timer.get("active") is True)
        + int(config.get("conflict") is True)
    )
    count_payload = {
        "inbox": inbox,
        "static_boundary": static,
        "retired_timer": retired_timer,
        "wecom_execution_config": config,
        "unsafe_count": unsafe_count,
        "release_sha": current_release_sha(),
        "pii_included": False,
    }
    digest = hashlib.sha256(json.dumps(count_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return {
        "ok": bool(inbox.get("checked")) and unsafe_count == 0,
        **count_payload,
        "count_digest": digest,
    }


def main(argv: list[str] | None = None) -> int:
    payload = run(argv)
    print_json(payload, indent=2, sort_keys=True)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
