from __future__ import annotations

from scripts.ops import prepare_wecom_callback_ingress_cutover as cutover_plan


def test_wecom_callback_cutover_plan_requires_all_local_assets() -> None:
    payload = cutover_plan.run([])

    assert payload["ok"] is True
    assert payload["missing_assets"] == []
    assert payload["dry_run_only"] is True
    assert payload["manual_nginx_merge_required"] is True
    assert payload["venv_activate"] == "/home/ubuntu/venvs/openclaw/bin/activate"
    assert payload["backup_path_file"] == "/tmp/wecom-callback-cutover-backup-path"
    assert any(item["path"] == "aicrm_next/platform/platform_foundation/webhook_inbox/models.py" for item in payload["assets"])
    assert any(item["path"] == "aicrm_next/platform/platform_foundation/webhook_inbox/service.py" for item in payload["assets"])
    assert any(item["path"] == "aicrm_next/channels/channel_entry/callback_ingress.py" for item in payload["assets"])
    assert any(item["path"] == "scripts/ops/check_wecom_callback_deploy_smoke.py" for item in payload["assets"])


def test_wecom_callback_cutover_plan_covers_install_cutover_pressure_and_rollback() -> None:
    payload = cutover_plan.run(
        [
            "--remote-repo",
            "/srv/aicrm",
            "--nginx-config",
            "/etc/nginx/app.conf",
            "--venv-activate",
            "/srv/venv/bin/activate",
            "--env-file",
            "/srv/app.env",
            "--backup-path-file",
            "/tmp/app-callback-backup-path",
        ]
    )
    commands = payload["commands"]
    source_env = "set -a && source /srv/app.env && set +a"

    assert "test -f /srv/venv/bin/activate" in commands["preflight"]
    assert "source /srv/venv/bin/activate" in commands["preflight"]
    assert "test -f /srv/app.env" in commands["preflight"]
    assert source_env in commands["preflight"]
    assert "python -m alembic upgrade head" in commands["preflight"]
    assert "python3 -m alembic upgrade head" not in commands["preflight"]
    assert "source /srv/venv/bin/activate" in commands["install_and_start"]
    assert "source /srv/venv/bin/activate" in commands["cutover"]
    assert "source /srv/venv/bin/activate" in commands["callback_sample"]
    assert "source /srv/venv/bin/activate" in commands["pressure_probe"]
    assert "source /srv/venv/bin/activate" in commands["worker_isolation_canary"]
    assert "source /srv/venv/bin/activate" in commands["downstream_worker_isolation_canary"]
    assert "source /srv/venv/bin/activate" in commands["internal_event_worker_isolation_canary"]
    assert "source /srv/venv/bin/activate" in commands["reapply_cutover_after_rollback"]
    assert "source /srv/venv/bin/activate" in commands["rollback_drill_evidence"]
    assert "source /srv/venv/bin/activate" in commands["final_readiness"]
    for group in (
        "install_and_start",
        "cutover",
        "callback_sample",
        "pressure_probe",
        "worker_isolation_canary",
        "downstream_worker_isolation_canary",
        "internal_event_worker_isolation_canary",
        "reapply_cutover_after_rollback",
        "rollback_drill_evidence",
        "final_readiness",
    ):
        assert source_env in commands[group]
    assert "sudo cp deploy/openclaw-wecom-callback-ingress.service /etc/systemd/system/" in commands["install_and_start"]
    assert "sudo cp deploy/openclaw-wecom-callback-inbox-worker.service /etc/systemd/system/" in commands["install_and_start"]
    assert "sudo systemctl restart openclaw-wecom-callback-ingress.service" in commands["install_and_start"]
    assert "sudo systemctl restart openclaw-wecom-callback-inbox-worker.service" in commands["install_and_start"]
    assert "sudo systemctl disable --now openclaw-wecom-callback-inbox-worker.timer || true" in commands["install_and_start"]
    assert (
        "set -o pipefail; "
        "python scripts/ops/check_wecom_callback_deploy_smoke.py "
        "--web-base-url http://127.0.0.1:5001 --ingress-base-url http://127.0.0.1:5002 "
        "| tee /tmp/wecom-callback-deploy-smoke.json"
    ) in commands["install_and_start"]
    assert commands["install_and_start"].index("sudo systemctl restart openclaw-wecom-callback-inbox-worker.service") < commands[
        "install_and_start"
    ].index(
        "set -o pipefail; "
        "python scripts/ops/check_wecom_callback_deploy_smoke.py "
        "--web-base-url http://127.0.0.1:5001 --ingress-base-url http://127.0.0.1:5002 "
        "| tee /tmp/wecom-callback-deploy-smoke.json"
    )
    assert "sudoedit /etc/nginx/app.conf" in commands["cutover"]
    assert any("AICRM_CALLBACK_CUTOVER_BACKUP" in item for item in commands["cutover"])
    assert 'printf \'%s\\n\' "$AICRM_CALLBACK_CUTOVER_BACKUP" > /tmp/app-callback-backup-path' in commands["cutover"]
    assert "cd /srv/aicrm" in commands["rollback"]
    assert "test -s /tmp/app-callback-backup-path" in commands["rollback"]
    assert 'export AICRM_CALLBACK_CUTOVER_BACKUP="$(cat /tmp/app-callback-backup-path)"' in commands["rollback"]
    assert 'test -f "$AICRM_CALLBACK_CUTOVER_BACKUP"' in commands["rollback"]
    assert 'sudo cp "$AICRM_CALLBACK_CUTOVER_BACKUP" /etc/nginx/app.conf' in commands["rollback"]
    assert any("check_wecom_callback_ingress_cutover.py --nginx-config /etc/nginx/app.conf" in item for item in commands["cutover"])
    assert "callback_sample" in commands
    assert any("generate_wecom_callback_sample.py" in item for item in commands["callback_sample"])
    assert any("--body-file /tmp/wecom-callback-sample.xml" in item for item in commands["callback_sample"])
    assert any("--url-file /tmp/wecom-callback-sample.url" in item for item in commands["callback_sample"])
    assert any("probe_wecom_callback_pressure.py" in item and "--rate-per-minute 1200" in item for item in commands["pressure_probe"])
    assert any("--require-valid-callback-sample" in item for item in commands["pressure_probe"])
    assert any("--callback-url \"$(cat /tmp/wecom-callback-sample.url)\"" in item for item in commands["pressure_probe"])
    assert any("--callback-body-file /tmp/wecom-callback-sample.xml" in item for item in commands["pressure_probe"])
    assert "worker_isolation_canary" in commands
    assert "sudo systemctl stop openclaw-wecom-callback-inbox-worker.service || true" in commands["worker_isolation_canary"]
    assert any("--total-requests 1" in item for item in commands["worker_isolation_canary"])
    assert any("--require-valid-callback-sample" in item for item in commands["worker_isolation_canary"])
    assert any("--callback-url \"$(cat /tmp/wecom-callback-sample.url)\"" in item for item in commands["worker_isolation_canary"])
    assert any("--no-default-samples" in item for item in commands["worker_isolation_canary"])
    assert any("tee /tmp/wecom-callback-worker-isolation.json" in item for item in commands["worker_isolation_canary"])
    assert "sudo systemctl start openclaw-wecom-callback-inbox-worker.service || true" in commands["worker_isolation_canary"]
    assert "downstream_worker_isolation_canary" in commands
    assert "sudo systemctl disable --now openclaw-external-push-worker.timer || true" in commands["downstream_worker_isolation_canary"]
    assert "sudo systemctl disable --now openclaw-external-push-worker.service || true" in commands["downstream_worker_isolation_canary"]
    assert any("--total-requests 1" in item for item in commands["downstream_worker_isolation_canary"])
    assert any("--require-valid-callback-sample" in item for item in commands["downstream_worker_isolation_canary"])
    assert any("--callback-url \"$(cat /tmp/wecom-callback-sample.url)\"" in item for item in commands["downstream_worker_isolation_canary"])
    assert any("tee /tmp/wecom-callback-downstream-worker-isolation.json" in item for item in commands["downstream_worker_isolation_canary"])
    assert not any("systemctl start openclaw-external-push-worker" in item for item in commands["downstream_worker_isolation_canary"])
    assert "internal_event_worker_isolation_canary" in commands
    assert "sudo systemctl stop openclaw-internal-event-worker.timer" in commands["internal_event_worker_isolation_canary"]
    assert "sudo systemctl stop openclaw-internal-event-worker.service || true" in commands["internal_event_worker_isolation_canary"]
    assert any("--total-requests 1" in item for item in commands["internal_event_worker_isolation_canary"])
    assert any("--require-valid-callback-sample" in item for item in commands["internal_event_worker_isolation_canary"])
    assert any("--callback-url \"$(cat /tmp/wecom-callback-sample.url)\"" in item for item in commands["internal_event_worker_isolation_canary"])
    assert any("--callback-body-file /tmp/wecom-callback-sample.xml" in item for item in commands["internal_event_worker_isolation_canary"])
    assert any("tee /tmp/wecom-callback-internal-event-worker-isolation.json" in item for item in commands["internal_event_worker_isolation_canary"])
    assert "sudo systemctl start openclaw-internal-event-worker.timer" in commands["internal_event_worker_isolation_canary"]
    assert "sudo systemctl start openclaw-internal-event-worker.service || true" in commands["internal_event_worker_isolation_canary"]
    assert any("set -o pipefail;" in item for item in commands["pressure_probe"])
    assert any("tee /tmp/wecom-callback-pressure.json" in item for item in commands["pressure_probe"])
    assert any("check_wecom_callback_ingestion_evidence.py" in item for item in commands["pressure_probe"])
    assert any("tee /tmp/wecom-callback-ingestion.json" in item for item in commands["pressure_probe"])
    assert any("run_wecom_callback_inbox_worker.py --limit 20" in item for item in commands["pressure_probe"])
    assert any(
        "AICRM_WECOM_CALLBACK_INBOX_WORKER_EXECUTE=1 "
        "python scripts/run_wecom_callback_inbox_worker.py --execute --limit 20" in item
        for item in commands["pressure_probe"]
    )
    assert commands["pressure_probe"].index("python scripts/run_wecom_callback_inbox_worker.py --limit 20") < commands[
        "pressure_probe"
    ].index(
        "AICRM_WECOM_CALLBACK_INBOX_WORKER_EXECUTE=1 "
        "python scripts/run_wecom_callback_inbox_worker.py --execute --limit 20"
    )
    assert any("check_wecom_callback_processing_evidence.py" in item for item in commands["pressure_probe"])
    assert any("tee /tmp/wecom-callback-processing.json" in item for item in commands["pressure_probe"])
    assert any("check_wecom_callback_public_state.py --base-url http://127.0.0.1:5001" in item for item in commands["pressure_probe"])
    assert any("tee /tmp/wecom-callback-public-state.json" in item for item in commands["pressure_probe"])
    assert any("check_wecom_callback_deploy_smoke.py" in item for item in commands["pressure_probe"])
    assert any("tee /tmp/wecom-callback-deploy-smoke.json" in item for item in commands["pressure_probe"])
    assert not any("check_wecom_callback_permanent_fix_readiness.py" in item for item in commands["pressure_probe"])
    assert "rollback_drill_evidence" in commands
    assert any("check_wecom_callback_rollback_evidence.py --print-template" in item for item in commands["rollback_drill_evidence"])
    assert any("check_wecom_callback_rollback_evidence.py --evidence-file /tmp/wecom-callback-rollback.json" in item for item in commands["rollback_drill_evidence"])
    assert "final_readiness" in commands
    assert any("check_wecom_callback_permanent_fix_readiness.py" in item for item in commands["final_readiness"])
    assert any("--pressure-evidence-file /tmp/wecom-callback-pressure.json" in item for item in commands["final_readiness"])
    assert any("--ingestion-evidence-file /tmp/wecom-callback-ingestion.json" in item for item in commands["final_readiness"])
    assert any("--processing-evidence-file /tmp/wecom-callback-processing.json" in item for item in commands["final_readiness"])
    assert any("--worker-isolation-evidence-file /tmp/wecom-callback-worker-isolation.json" in item for item in commands["final_readiness"])
    assert any("--downstream-worker-isolation-evidence-file /tmp/wecom-callback-downstream-worker-isolation.json" in item for item in commands["final_readiness"])
    assert any("--internal-event-worker-isolation-evidence-file /tmp/wecom-callback-internal-event-worker-isolation.json" in item for item in commands["final_readiness"])
    assert any("--rollback-evidence-file /tmp/wecom-callback-rollback.json" in item for item in commands["final_readiness"])
    assert any("--public-state-evidence-file /tmp/wecom-callback-public-state.json" in item for item in commands["final_readiness"])
    assert any("--deploy-smoke-evidence-file /tmp/wecom-callback-deploy-smoke.json" in item for item in commands["final_readiness"])
    assert "sudo systemctl reload nginx" in commands["rollback"]
    assert "reapply_cutover_after_rollback" in commands
    assert "source /srv/venv/bin/activate" in commands["reapply_cutover_after_rollback"]
    assert source_env in commands["reapply_cutover_after_rollback"]
    assert "sudo systemctl start openclaw-wecom-callback-ingress.service" in commands["reapply_cutover_after_rollback"]
    assert "sudo systemctl start openclaw-wecom-callback-inbox-worker.service" in commands["reapply_cutover_after_rollback"]
    assert "curl -sSf http://127.0.0.1:5002/health" in commands["reapply_cutover_after_rollback"]
    assert "sudoedit /etc/nginx/app.conf" in commands["reapply_cutover_after_rollback"]
    assert "sudo nginx -t" in commands["reapply_cutover_after_rollback"]
    assert "sudo systemctl reload nginx" in commands["reapply_cutover_after_rollback"]
    assert any(
        "check_wecom_callback_ingress_cutover.py --nginx-config /etc/nginx/app.conf" in item
        for item in commands["reapply_cutover_after_rollback"]
    )
    assert any("check_wecom_callback_public_state.py --base-url http://127.0.0.1:5001" in item for item in commands["reapply_cutover_after_rollback"])
    assert any("tee /tmp/wecom-callback-public-state.json" in item for item in commands["reapply_cutover_after_rollback"])
    assert any("check_wecom_callback_deploy_smoke.py" in item for item in commands["reapply_cutover_after_rollback"])
    assert any("tee /tmp/wecom-callback-deploy-smoke.json" in item for item in commands["reapply_cutover_after_rollback"])
    assert any("limit_req" in item and "limit_conn" in item for item in payload["completion_evidence"])
    assert any("worker service is stopped" in item for item in payload["completion_evidence"])
    assert any("downstream external push worker is stopped" in item for item in payload["completion_evidence"])
    assert any("internal event worker timer/service is stopped" in item for item in payload["completion_evidence"])
    assert any("processing.json" in item for item in payload["completion_evidence"])
    assert any("same_sample_evidence.ok" in item for item in payload["completion_evidence"])
    assert any("rollback.json" in item for item in payload["completion_evidence"])
    assert any("public-state.json" in item for item in payload["completion_evidence"])
    assert any("deploy-smoke.json" in item for item in payload["completion_evidence"])
    assert any("callback pressure probe at 1200/min" in item for item in payload["completion_evidence"])
