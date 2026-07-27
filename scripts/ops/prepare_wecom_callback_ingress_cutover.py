#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

try:
    from scripts.script_runtime import REPO_ROOT, ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from script_runtime import REPO_ROOT, ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()


DEFAULT_REMOTE_REPO = "/home/ubuntu/极简 crm"
DEFAULT_ENV_FILE = "/home/ubuntu/.openclaw-wecom-pg.env"
DEFAULT_VENV_ACTIVATE = "/home/ubuntu/venvs/openclaw/bin/activate"
DEFAULT_NGINX_CONFIG = "/etc/nginx/sites-enabled/youcangogogo.conf"
DEFAULT_HEALTH_URL = "http://127.0.0.1:5001/health"
DEFAULT_INGRESS_HEALTH_URL = "http://127.0.0.1:5002/health"
DEFAULT_BACKUP_PATH_FILE = "/tmp/wecom-callback-cutover-backup-path"

REQUIRED_ASSETS = [
    "migrations/versions/0054_webhook_inbox.py",
    "aicrm_next/channels/channel_entry/callback_ingress.py",
    "aicrm_next/channels/channel_entry/ingress_app.py",
    "aicrm_next/channels/channel_entry/inbox.py",
    "aicrm_next/platform/platform_foundation/webhook_inbox/models.py",
    "aicrm_next/platform/platform_foundation/webhook_inbox/repository.py",
    "aicrm_next/platform/platform_foundation/webhook_inbox/service.py",
    "scripts/run_wecom_callback_ingress.py",
    "scripts/run_wecom_callback_inbox_worker.py",
    "scripts/ops/check_callback_quick_ack_state.py",
    "scripts/ops/check_wecom_callback_ingress_cutover.py",
    "scripts/ops/check_wecom_callback_ingestion_evidence.py",
    "scripts/ops/check_wecom_callback_processing_evidence.py",
    "scripts/ops/check_wecom_callback_rollback_evidence.py",
    "scripts/ops/check_wecom_callback_permanent_fix_readiness.py",
    "scripts/ops/check_wecom_callback_public_state.py",
    "scripts/ops/check_wecom_callback_deploy_smoke.py",
    "scripts/ops/generate_wecom_callback_sample.py",
    "scripts/ops/probe_wecom_callback_pressure.py",
    "deploy/openclaw-wecom-callback-ingress.service",
    "deploy/openclaw-wecom-callback-inbox-worker.service",
    "deploy/nginx-wecom-callback-ingress.conf.example",
    "docs/runbooks/wecom_callback_storm.md",
    "docs/reports/production_wecom_callback_storm_20260627.md",
    "docs/reports/wecom_callback_permanent_fix_acceptance_audit_20260627.md",
]


def _asset_state() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for relative in REQUIRED_ASSETS:
        path = REPO_ROOT / relative
        rows.append({"path": relative, "present": path.exists(), "size_bytes": path.stat().st_size if path.exists() else 0})
    return rows


def _source_env(env_file: str) -> str:
    return f"set -a && source {env_file} && set +a"


def _source_venv(venv_activate: str) -> str:
    return f"source {venv_activate}"


def _base_url_from_health_url(health_url: str) -> str:
    value = str(health_url or "").strip().rstrip("/")
    return value[: -len("/health")] if value.endswith("/health") else value


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    remote_repo = str(args.remote_repo)
    env_file = str(args.env_file)
    venv_activate = str(args.venv_activate)
    nginx_config = str(args.nginx_config)
    backup_path_file = str(args.backup_path_file)
    backup_template = f"{nginx_config}.bak-codex-callback-cutover-$(date +%Y%m%dT%H%M%S)"
    source_env = _source_env(env_file)
    source_venv = _source_venv(venv_activate)
    web_base_url = _base_url_from_health_url(str(args.web_health_url))
    ingress_base_url = _base_url_from_health_url(str(args.ingress_health_url))
    assets = _asset_state()
    missing_assets = [item["path"] for item in assets if not item["present"]]
    preflight = [
        f"cd {remote_repo}",
        f"test -f {venv_activate}",
        source_venv,
        f"test -f {env_file}",
        source_env,
        'test -n "${DATABASE_URL:-}"',
        "python -m alembic heads",
        "python -m alembic current",
        "python -m alembic upgrade head",
        "python scripts/ops/check_callback_quick_ack_state.py --skip-probe",
    ]
    install_and_start = [
        f"cd {remote_repo}",
        source_venv,
        source_env,
        "sudo cp deploy/openclaw-wecom-callback-ingress.service /etc/systemd/system/",
        "sudo cp deploy/openclaw-wecom-callback-inbox-worker.service /etc/systemd/system/",
        "sudo systemctl daemon-reload",
        "sudo systemctl disable --now openclaw-wecom-callback-inbox-worker.timer || true",
        "sudo systemctl enable openclaw-wecom-callback-ingress.service",
        "sudo systemctl restart openclaw-wecom-callback-ingress.service",
        "sudo systemctl enable openclaw-wecom-callback-inbox-worker.service",
        "sudo systemctl restart openclaw-wecom-callback-inbox-worker.service",
        f"curl -sSf {args.ingress_health_url}",
        "python scripts/run_wecom_callback_inbox_worker.py --limit 20",
        "set -o pipefail; "
        f"python scripts/ops/check_wecom_callback_deploy_smoke.py --web-base-url {web_base_url} --ingress-base-url {ingress_base_url} "
        "| tee /tmp/wecom-callback-deploy-smoke.json",
    ]
    cutover = [
        f"cd {remote_repo}",
        source_venv,
        source_env,
        f"export AICRM_CALLBACK_CUTOVER_BACKUP=\"{backup_template}\"",
        f"printf '%s\\n' \"$AICRM_CALLBACK_CUTOVER_BACKUP\" > {backup_path_file}",
        f"sudo cp {nginx_config} \"$AICRM_CALLBACK_CUTOVER_BACKUP\"",
        "sudoedit " + nginx_config,
        "sudo nginx -t",
        "sudo systemctl reload nginx",
        f"python scripts/ops/check_wecom_callback_ingress_cutover.py --nginx-config {nginx_config}",
        f"python scripts/ops/check_wecom_callback_permanent_fix_readiness.py --nginx-config {nginx_config}",
    ]
    callback_sample = [
        f"cd {remote_repo}",
        source_venv,
        source_env,
        "python scripts/ops/generate_wecom_callback_sample.py "
        f"--env-file {env_file} "
        "--callback-base-url http://127.0.0.1:5002/wecom/external-contact/callback "
        "--body-file /tmp/wecom-callback-sample.xml "
        "--url-file /tmp/wecom-callback-sample.url "
        "--metadata-file /tmp/wecom-callback-sample.json",
        "test -s /tmp/wecom-callback-sample.xml",
        "test -s /tmp/wecom-callback-sample.url",
    ]
    pressure = [
        f"cd {remote_repo}",
        source_venv,
        source_env,
        "set -o pipefail; python scripts/ops/probe_wecom_callback_pressure.py "
        "--callback-url \"$(cat /tmp/wecom-callback-sample.url)\" "
        "--callback-body-file /tmp/wecom-callback-sample.xml "
        "--require-valid-callback-sample "
        "--rate-per-minute 1200 "
        "--duration-seconds 60 "
        "| tee /tmp/wecom-callback-pressure.json",
        "python scripts/ops/check_wecom_callback_ingestion_evidence.py "
        "--pressure-evidence-file /tmp/wecom-callback-pressure.json "
        "| tee /tmp/wecom-callback-ingestion.json",
        "python scripts/run_wecom_callback_inbox_worker.py --limit 20",
        "AICRM_WECOM_CALLBACK_INBOX_WORKER_EXECUTE=1 "
        "python scripts/run_wecom_callback_inbox_worker.py --execute --limit 20",
        "python scripts/ops/check_wecom_callback_processing_evidence.py "
        "--pressure-evidence-file /tmp/wecom-callback-pressure.json "
        "| tee /tmp/wecom-callback-processing.json",
        f"python scripts/ops/check_wecom_callback_public_state.py --base-url {web_base_url} "
        "| tee /tmp/wecom-callback-public-state.json",
        "set -o pipefail; "
        f"python scripts/ops/check_wecom_callback_deploy_smoke.py --web-base-url {web_base_url} --ingress-base-url {ingress_base_url} "
        "| tee /tmp/wecom-callback-deploy-smoke.json",
    ]
    worker_isolation_canary = [
        f"cd {remote_repo}",
        source_venv,
        source_env,
        "sudo systemctl stop openclaw-wecom-callback-inbox-worker.service || true",
        "systemctl is-active openclaw-wecom-callback-inbox-worker.service || true",
        "set -o pipefail; python scripts/ops/probe_wecom_callback_pressure.py "
        "--callback-url \"$(cat /tmp/wecom-callback-sample.url)\" "
        "--callback-body-file /tmp/wecom-callback-sample.xml "
        "--require-valid-callback-sample "
        "--total-requests 1 "
        "--rate-per-minute 60000 "
        "--duration-seconds 1 "
        "--no-default-samples "
        "--callback-target-p95-ms 200 "
        "--callback-target-p99-ms 500 "
        "| tee /tmp/wecom-callback-worker-isolation.json",
        "sudo systemctl start openclaw-wecom-callback-inbox-worker.service || true",
        "sudo systemctl is-active openclaw-wecom-callback-inbox-worker.service",
    ]
    downstream_worker_isolation_canary = [
        f"cd {remote_repo}",
        source_venv,
        source_env,
        "sudo systemctl disable --now openclaw-external-push-worker.timer || true",
        "sudo systemctl disable --now openclaw-external-push-worker.service || true",
        "systemctl is-active openclaw-external-push-worker.timer || true",
        "systemctl is-active openclaw-external-push-worker.service || true",
        "set -o pipefail; python scripts/ops/probe_wecom_callback_pressure.py "
        "--callback-url \"$(cat /tmp/wecom-callback-sample.url)\" "
        "--callback-body-file /tmp/wecom-callback-sample.xml "
        "--require-valid-callback-sample "
        "--total-requests 1 "
        "--rate-per-minute 60000 "
        "--duration-seconds 1 "
        "--callback-target-p95-ms 200 "
        "--callback-target-p99-ms 500 "
        "| tee /tmp/wecom-callback-downstream-worker-isolation.json",
    ]
    internal_event_worker_isolation_canary = [
        f"cd {remote_repo}",
        source_venv,
        source_env,
        "sudo systemctl stop openclaw-internal-event-worker.timer",
        "sudo systemctl stop openclaw-internal-event-worker.service || true",
        "systemctl is-active openclaw-internal-event-worker.timer || true",
        "set -o pipefail; python scripts/ops/probe_wecom_callback_pressure.py "
        "--callback-url \"$(cat /tmp/wecom-callback-sample.url)\" "
        "--callback-body-file /tmp/wecom-callback-sample.xml "
        "--require-valid-callback-sample "
        "--total-requests 1 "
        "--rate-per-minute 60000 "
        "--duration-seconds 1 "
        "--callback-target-p95-ms 200 "
        "--callback-target-p99-ms 500 "
        "| tee /tmp/wecom-callback-internal-event-worker-isolation.json",
        "sudo systemctl start openclaw-internal-event-worker.timer",
        "sudo systemctl start openclaw-internal-event-worker.service || true",
        "sudo systemctl is-active openclaw-internal-event-worker.timer",
    ]
    rollback = [
        f"cd {remote_repo}",
        f"test -s {backup_path_file}",
        f"export AICRM_CALLBACK_CUTOVER_BACKUP=\"$(cat {backup_path_file})\"",
        "test -n \"${AICRM_CALLBACK_CUTOVER_BACKUP:-}\"",
        "test -f \"$AICRM_CALLBACK_CUTOVER_BACKUP\"",
        f"sudo cp \"$AICRM_CALLBACK_CUTOVER_BACKUP\" {nginx_config}",
        "sudo nginx -t",
        "sudo systemctl reload nginx",
        "sudo systemctl stop openclaw-wecom-callback-inbox-worker.service || true",
        "sudo systemctl stop openclaw-wecom-callback-ingress.service || true",
        f"curl -sSf {args.web_health_url}",
    ]
    reapply_cutover_after_rollback = [
        f"cd {remote_repo}",
        source_venv,
        source_env,
        "sudo systemctl start openclaw-wecom-callback-ingress.service",
        "sudo systemctl start openclaw-wecom-callback-inbox-worker.service",
        f"curl -sSf {args.ingress_health_url}",
        "sudoedit " + nginx_config,
        "sudo nginx -t",
        "sudo systemctl reload nginx",
        f"python scripts/ops/check_wecom_callback_ingress_cutover.py --nginx-config {nginx_config}",
        f"python scripts/ops/check_wecom_callback_public_state.py --base-url {web_base_url} "
        "| tee /tmp/wecom-callback-public-state.json",
        "set -o pipefail; "
        f"python scripts/ops/check_wecom_callback_deploy_smoke.py --web-base-url {web_base_url} --ingress-base-url {ingress_base_url} "
        "| tee /tmp/wecom-callback-deploy-smoke.json",
    ]
    rollback_drill_evidence = [
        f"cd {remote_repo}",
        source_venv,
        source_env,
        "python scripts/ops/check_wecom_callback_rollback_evidence.py --print-template > /tmp/wecom-callback-rollback.template.json",
        "printf '%s\\n' 'After an approved rollback drill and reapply_cutover_after_rollback, replace template values with captured production evidence and save /tmp/wecom-callback-rollback.json'",
        "python scripts/ops/check_wecom_callback_rollback_evidence.py --evidence-file /tmp/wecom-callback-rollback.json",
    ]
    final_readiness = [
        f"cd {remote_repo}",
        source_venv,
        source_env,
        f"python scripts/ops/check_wecom_callback_permanent_fix_readiness.py --nginx-config {nginx_config} --pressure-evidence-file /tmp/wecom-callback-pressure.json --ingestion-evidence-file /tmp/wecom-callback-ingestion.json --processing-evidence-file /tmp/wecom-callback-processing.json --worker-isolation-evidence-file /tmp/wecom-callback-worker-isolation.json --downstream-worker-isolation-evidence-file /tmp/wecom-callback-downstream-worker-isolation.json --internal-event-worker-isolation-evidence-file /tmp/wecom-callback-internal-event-worker-isolation.json --rollback-evidence-file /tmp/wecom-callback-rollback.json --public-state-evidence-file /tmp/wecom-callback-public-state.json --deploy-smoke-evidence-file /tmp/wecom-callback-deploy-smoke.json",
    ]
    completion_evidence = [
        "alembic current shows 0054_webhook_inbox",
        "openclaw-wecom-callback-ingress.service is active on 127.0.0.1:5002",
        "openclaw-wecom-callback-inbox-worker.service is active and the retired timer is disabled",
        "nginx callback routes proxy to 127.0.0.1:5002 and no longer contain return 200 success",
        "nginx callback routes include limit_req, limit_conn, and 429 overload status",
        "valid callbacks enqueue into webhook_inbox and /tmp/wecom-callback-ingestion.json proves the sampled idempotency_key row",
        "/tmp/wecom-callback-processing.json proves the worker consumed the sampled row without business side effects",
        "readiness same_sample_evidence.ok proves pressure, ingestion, and processing JSON share the same idempotency_key",
        "invalid callback POST returns 400 from app-level ingress rather than nginx-level plain success",
        "single valid callback ACK succeeds while callback worker service is stopped, then the durable row is recovered after restart",
        "single valid callback ACK and page samples succeed while downstream external push worker is stopped, then worker is restored",
        "single valid callback ACK and page samples succeed while internal event worker timer/service is stopped, then worker is restored",
        "/tmp/wecom-callback-rollback.json proves emergency quick ACK can be restored and the permanent cutover was re-applied after the drill",
        "/tmp/wecom-callback-public-state.json proves public webhook inbox routes are deployed and invalid callback returns app-level 4xx",
        "/tmp/wecom-callback-deploy-smoke.json proves web, ingress, admin API, and detail routes are deployed after cutover",
        "callback pressure probe at 1200/min meets P95/P99 targets and page samples stay non-5xx",
        "webhook inbox JSON APIs expose queue metrics and replayable dead-letter rows",
    ]
    warnings = []
    if missing_assets:
        warnings.append("missing local cutover assets; do not use this plan until assets are present")
    warnings.append("this script does not execute production changes; run commands only inside an approved deployment window")
    warnings.append("nginx edit is intentionally manual because the production server block must be merged, not overwritten")
    warnings.append("pressure probe requires callback env that can generate a valid encrypted WeCom callback sample")
    return {
        "ok": not missing_assets,
        "dry_run_only": True,
        "remote_repo": remote_repo,
        "venv_activate": venv_activate,
        "nginx_config": nginx_config,
        "backup_path_file": backup_path_file,
        "nginx_backup_path_template": backup_template,
        "assets": assets,
        "missing_assets": missing_assets,
        "commands": {
            "preflight": preflight,
            "install_and_start": install_and_start,
            "cutover": cutover,
            "callback_sample": callback_sample,
            "worker_isolation_canary": worker_isolation_canary,
            "downstream_worker_isolation_canary": downstream_worker_isolation_canary,
            "internal_event_worker_isolation_canary": internal_event_worker_isolation_canary,
            "pressure_probe": pressure,
            "reapply_cutover_after_rollback": reapply_cutover_after_rollback,
            "rollback_drill_evidence": rollback_drill_evidence,
            "final_readiness": final_readiness,
            "rollback": rollback,
        },
        "manual_nginx_merge_required": True,
        "completion_evidence": completion_evidence,
        "warnings": warnings,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the approved-window command plan for WeCom callback ingress cutover.")
    parser.add_argument("--remote-repo", default=DEFAULT_REMOTE_REPO)
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--venv-activate", default=DEFAULT_VENV_ACTIVATE)
    parser.add_argument("--nginx-config", default=DEFAULT_NGINX_CONFIG)
    parser.add_argument("--backup-path-file", default=DEFAULT_BACKUP_PATH_FILE)
    parser.add_argument("--web-health-url", default=DEFAULT_HEALTH_URL)
    parser.add_argument("--ingress-health-url", default=DEFAULT_INGRESS_HEALTH_URL)
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> dict[str, Any]:
    return build_plan(_parse_args(argv))


def main(argv: list[str] | None = None) -> int:
    payload = run(argv)
    print_json(payload, indent=2)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
