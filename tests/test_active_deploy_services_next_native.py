from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTIVE_SCRIPTS = {
    "automation_ops_scheduler": Path("scripts/run_automation_ops_scheduler.py"),
    "broadcast_queue_worker": Path("scripts/run_broadcast_queue_worker.py"),
    "external_contact_sync": Path("scripts/run_external_contact_sync.py"),
    "external_effect_queue_worker": Path("scripts/run_external_effect_queue_worker.py"),
}
SERVICE_COMMANDS = {
    "deploy/aicrm-next-group-ops-planning.service": "python scripts/run_automation_ops_scheduler.py",
    "deploy/aicrm-next-broadcast-delegation.service": "python scripts/run_broadcast_queue_worker.py",
    "deploy/openclaw-automation-ops-scheduler.service": "python scripts/run_automation_ops_scheduler.py",
    "deploy/openclaw-broadcast-queue-worker.service": "python scripts/run_broadcast_queue_worker.py",
    "deploy/openclaw-external-contact-sync.service": "python scripts/run_external_contact_sync.py",
    "deploy/openclaw-external-contact-full-sync.service": "python scripts/run_external_contact_sync.py --full",
    "deploy/openclaw-external-effect-worker.service": "python scripts/run_external_effect_queue_worker.py --execute",
}


def _run_cli(args: list[str]) -> dict:
    result = subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def test_active_deploy_scripts_exist_and_do_not_import_legacy() -> None:
    for rel_path in ACTIVE_SCRIPTS.values():
        path = ROOT / rel_path
        assert path.exists()
        source = path.read_text(encoding="utf-8")
        assert "wecom_ability" + "_service" not in source
        assert "create_app" not in source
        assert "app_context" not in source


def test_deploy_services_keep_existing_execstart_contracts() -> None:
    for rel_service, command in SERVICE_COMMANDS.items():
        service = (ROOT / rel_service).read_text(encoding="utf-8")
        assert command in service


def test_active_deploy_cli_dry_run_contracts() -> None:
    cases = [
        ["scripts/run_automation_ops_scheduler.py", "--dry-run"],
        ["scripts/run_broadcast_queue_worker.py", "--limit", "10", "--dry-run"],
        ["scripts/run_external_contact_sync.py", "--full", "--dry-run"],
        ["scripts/run_external_effect_queue_worker.py", "--dry-run", "--limit", "10"],
    ]
    for args in cases:
        payload = _run_cli(args)
        assert "ok" in payload
        assert payload.get("ok") is True or payload.get("errors")
        assert payload.get("dry_run") is True
