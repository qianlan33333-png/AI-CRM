from __future__ import annotations

import json
from pathlib import Path

from scripts.ci.runtime_contract_inventory import (
    build_inventory,
    check_inventory,
    render_inventory,
    write_inventory,
)


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "docs" / "architecture" / "runtime_contract_inventory.json"


def test_runtime_contract_inventory_covers_r00_behavior_surfaces() -> None:
    inventory = build_inventory(ROOT)

    assert inventory["schema_version"] == 1
    assert inventory["composition_root"] == "aicrm_next.main:create_app"
    assert inventory["production_data_accessed"] is False
    assert inventory["fixture_records_included"] is False

    routes = inventory["routes"]
    assert len(routes) >= 600
    assert any(route["path"] == "/health" and route["methods"] == ["GET"] for route in routes)
    assert any(route["kind"] == "page" for route in routes)
    assert all(route["capability_owner"] for route in routes)
    assert all("responses" in route["contract"] for route in routes)

    assert inventory["migration_heads"] == ["0136_queue_runtime_validation_soak"]
    assert len(inventory["tables"]) >= 150
    owned_lifecycles = {"canonical", "read_model", "event", "queue", "config"}
    assert all(table["write_owner"] for table in inventory["tables"] if table["lifecycle"] in owned_lifecycles)
    tables_by_name = {table["table"]: table for table in inventory["tables"]}
    assert {
        "queue_runtime_control",
        "queue_lane_policy",
        "queue_policy_snapshot",
        "queue_fairness_cursor",
        "queue_rate_scope_cooldown",
        "queue_worker_heartbeat",
    } <= set(tables_by_name)
    assert {
        name: tables_by_name[name]["lifecycle"]
        for name in (
            "queue_runtime_control",
            "queue_lane_policy",
            "queue_policy_snapshot",
            "queue_fairness_cursor",
            "queue_rate_scope_cooldown",
            "queue_worker_heartbeat",
        )
    } == {
        "queue_runtime_control": "config",
        "queue_lane_policy": "config",
        "queue_policy_snapshot": "config",
        "queue_fairness_cursor": "read_model",
        "queue_rate_scope_cooldown": "queue",
        "queue_worker_heartbeat": "read_model",
    }
    assert all(
        tables_by_name[name]["migration_source"] == "0126_postgres_execution_runtime"
        for name in (
            "queue_runtime_control",
            "queue_lane_policy",
            "queue_policy_snapshot",
            "queue_fairness_cursor",
            "queue_rate_scope_cooldown",
            "queue_worker_heartbeat",
        )
    )
    assert inventory["internal_event_consumers"]
    assert inventory["external_effects"]
    assert any(unit["unit"] == "openclaw-wecom-callback-ingress.service" for unit in inventory["runtime_units"])
    runtime_units = {unit["unit"]: unit for unit in inventory["runtime_units"]}
    for unit_name in (
        "aicrm-internal-queue-runtime.service",
        "aicrm-inbox-queue-runtime.service",
        "aicrm-external-queue-runtime.service",
    ):
        assert runtime_units[unit_name]["kind"] == "service"
        assert runtime_units[unit_name]["state"] == "active"
        assert runtime_units[unit_name]["stop_for_migration"] is True
    for unit_name in (
        "openclaw-internal-event-worker.timer",
        "openclaw-external-effect-worker.timer",
        "openclaw-broadcast-queue-worker.timer",
        "openclaw-ai-audience-scheduler.timer",
        "openclaw-identity-resolution-worker.timer",
        "openclaw-customer-read-model-refresh.timer",
        "openclaw-automation-ops-scheduler.timer",
        "openclaw-wecom-callback-inbox-worker.service",
    ):
        assert runtime_units[unit_name]["state"] == "cutover_managed_legacy"
        assert runtime_units[unit_name]["owner_inventory"] == "pr3"
    assert runtime_units["aicrm-ai-audience-daily-intent.timer"] == {
        "unit": "aicrm-ai-audience-daily-intent.timer",
        "kind": "timer",
        "state": "cutover_replacement_autostart",
        "service": "aicrm-ai-audience-daily-intent.service",
        "owner_inventory": "pr3",
    }
    assert runtime_units["aicrm-next-broadcast-delegation.timer"] == {
        "unit": "aicrm-next-broadcast-delegation.timer",
        "kind": "timer",
        "state": "cutover_replacement_autostart",
        "service": "aicrm-next-broadcast-delegation.service",
        "owner_inventory": "pr3",
    }
    assert runtime_units["aicrm-next-group-ops-planning.timer"] == {
        "unit": "aicrm-next-group-ops-planning.timer",
        "kind": "timer",
        "state": "cutover_replacement_autostart",
        "service": "aicrm-next-group-ops-planning.service",
        "owner_inventory": "pr3",
    }
    assert "DATABASE_URL" in inventory["environment_variables"]
    assert {
        "AICRM_AUTH_ARCHIVE_WORKER_CLIENT_ID",
        "AICRM_AUTH_ARCHIVE_WORKER_CLIENT_SECRET_REF",
        "AICRM_AUTH_AUTOMATION_WORKER_CLIENT_ID",
        "AICRM_AUTH_AUTOMATION_WORKER_CLIENT_SECRET_REF",
        "AICRM_AUTH_ISSUER",
        "AICRM_AUTH_JWT_SIGNING_KEY",
    } <= set(inventory["environment_variables"])
    assert "AUTOMATION_INTERNAL_API_TOKEN" not in inventory["environment_variables"]
    assert all("value" not in item for item in inventory["environment_variable_references"])


def test_runtime_contract_inventory_render_is_deterministic() -> None:
    first = render_inventory(build_inventory(ROOT))
    second = render_inventory(build_inventory(ROOT))

    assert first == second
    assert json.loads(first)["schema_version"] == 1


def test_runtime_contract_inventory_write_and_check_detect_drift(tmp_path: Path) -> None:
    destination = tmp_path / "inventory.json"

    write_inventory(ROOT, destination)
    assert check_inventory(ROOT, destination) == ""

    payload = json.loads(destination.read_text(encoding="utf-8"))
    payload["migration_heads"] = ["drifted_head"]
    destination.write_text(json.dumps(payload), encoding="utf-8")

    diff = check_inventory(ROOT, destination)
    assert "drifted_head" in diff
    assert "0100_external_effect_delivery_lease" in diff


def test_checked_in_runtime_contract_inventory_matches_current_runtime() -> None:
    assert check_inventory(ROOT, SNAPSHOT) == ""
