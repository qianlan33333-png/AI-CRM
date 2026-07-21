from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNITS = {
    "aicrm-internal-queue-runtime.service": "internal",
    "aicrm-inbox-queue-runtime.service": "webhook",
    "aicrm-external-queue-runtime.service": "external",
}


def test_queue_runtime_units_are_registered_as_fail_closed_persistent_services() -> None:
    manifest = json.loads((ROOT / "deploy" / "production_runtime_units.json").read_text(encoding="utf-8"))
    active = {str(item.get("service")): item for item in manifest.get("active_services") or []}

    for unit, queue_kind in UNITS.items():
        item = active[unit]
        assert item["stop_for_migration"] is True
        body = (ROOT / "deploy" / unit).read_text(encoding="utf-8")
        assert "Restart=always" in body
        assert "AICRM_LISTENER_DATABASE_URL" in body
        assert "EnvironmentFile=-/home/ubuntu/.aicrm-queue-runtime-generation.env" in body
        assert f"run_execution_runtime.py --queue-kind {queue_kind}" in body
        assert "--generation" not in body
        assert " --execute" not in body


def test_queue_invariant_timer_is_read_only_and_runs_every_fifteen_minutes() -> None:
    timer = (ROOT / "deploy" / "aicrm-queue-invariant-check.timer").read_text(encoding="utf-8")
    service = (ROOT / "deploy" / "aicrm-queue-invariant-check.service").read_text(encoding="utf-8")

    assert "OnCalendar=*:0/15" in timer
    assert "Persistent=true" in timer
    assert "scripts/ops/check_queue_runtime_invariants.py" in service
    assert "run_execution_runtime.py" not in service
    assert "--execute" not in service


def test_queue_runtime_execute_mode_requires_positive_generation() -> None:
    script = (ROOT / "scripts" / "run_execution_runtime.py").read_text(encoding="utf-8")
    assert 'if args.execute and args.generation <= 0:' in script
    assert 'parser.error("--execute requires --generation > 0")' in script
