from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNITS = {
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


def test_combined_internal_worker_is_the_enforced_generation_gated_owner() -> None:
    manifest = json.loads(
        (ROOT / "deploy" / "production_runtime_units.json").read_text(encoding="utf-8")
    )
    contract = manifest["internal_worker_consolidation"]
    unit = contract["service"]
    active = {str(item["service"]): item for item in manifest["active_services"]}
    body = (ROOT / "deploy" / unit).read_text(encoding="utf-8")

    assert contract["activation_mode"] == "enforce"
    assert contract["legacy_units_remain_authoritative"] is False
    assert contract["real_external_calls_allowed"] is False
    assert active[unit]["stop_for_migration"] is True
    assert "run_execution_runtime.py --role internal_worker" in body
    assert "--standby" not in body
    assert "--execute" not in body
    assert "EnvironmentFile=-/home/ubuntu/.aicrm-queue-runtime-generation.env" in body
    assert "DB_APPLICATION_NAME=aicrm-next-internal-worker" in body
    assert "Restart=always" in body
    retired = set(manifest["retired_forbidden"])
    retired_files = set(manifest["retired_unit_files"])
    predecessors = set(contract["predecessor_services"])
    assert predecessors | {"aicrm-internal-worker-observer.service"} <= retired & retired_files
    assert not predecessors & set(active)


def test_queue_invariant_timer_is_read_only_and_runs_every_fifteen_minutes() -> None:
    timer = (ROOT / "deploy" / "aicrm-queue-invariant-check.timer").read_text(encoding="utf-8")
    service = (ROOT / "deploy" / "aicrm-queue-invariant-check.service").read_text(encoding="utf-8")

    assert "OnCalendar=*:0/15" in timer
    assert "Persistent=true" in timer
    assert "scripts/ops/check_queue_runtime_invariants.py" in service
    assert "run_execution_runtime.py" not in service
    assert "--execute" not in service


def test_queue_soak_timer_captures_read_only_evidence_every_fifteen_minutes() -> None:
    timer = (ROOT / "deploy" / "aicrm-queue-soak-snapshot.timer").read_text(encoding="utf-8")
    service = (ROOT / "deploy" / "aicrm-queue-soak-snapshot.service").read_text(encoding="utf-8")

    assert "OnCalendar=*:0/15" in timer
    assert "Persistent=true" in timer
    assert "manage_queue_runtime_soak.py --action snapshot" in service
    assert "--execute" not in service


def test_queue_runtime_execute_mode_requires_positive_generation() -> None:
    script = (ROOT / "scripts" / "run_execution_runtime.py").read_text(encoding="utf-8")
    assert 'if args.execute and args.generation <= 0:' in script
    assert 'parser.error("--execute requires --generation > 0")' in script


def test_queue_runtime_reserves_end_to_end_welcome_lanes() -> None:
    script = (ROOT / "scripts" / "run_execution_runtime.py").read_text(encoding="utf-8")

    assert '"wecom_welcome_ingress", "webhook_inbox"' in script
    assert '"wecom_welcome",' in script
    assert "WECOM_WELCOME_FALLBACK_SECONDS" in script
    assert "critical_post_commit_hook=run_welcome_realtime_post_commit" in script
