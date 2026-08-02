from __future__ import annotations

from pathlib import Path

from tools.check_retired_runtime_references import DEFAULT_REGISTRY, load_registry, run_check, scan_references


ROOT = Path(__file__).resolve().parents[1]


def test_current_active_runtime_has_zero_retired_references() -> None:
    result = run_check(ROOT)

    assert result["ok"] is True, result["violations"]
    assert result["violation_count"] == 0
    assert result["artifact_count"] >= 5
    assert result["scanned_file_count"] > 100


def test_scanner_fails_closed_when_retired_route_returns(tmp_path: Path) -> None:
    runtime = tmp_path / "aicrm_next"
    runtime.mkdir()
    (runtime / "restored.py").write_text(
        'ROUTE = "/api/admin/legacy-webhook-cleanup/run-due"\n',
        encoding="utf-8",
    )
    registry = load_registry(DEFAULT_REGISTRY)

    result = scan_references(tmp_path, registry)

    assert result["ok"] is False
    assert result["violation_count"] == 1
    assert result["violations"][0] == {
        "artifact_id": "legacy_cleanup_routes",
        "kind": "route",
        "pattern": "/api/admin/legacy-webhook-cleanup",
        "path": "aicrm_next/restored.py",
        "line": 1,
    }


def test_registry_is_json_compatible_and_runtime_units_only_allow_enforcement_paths() -> None:
    registry = load_registry(DEFAULT_REGISTRY)
    units = next(item for item in registry["artifacts"] if item["id"] == "retired_runtime_units")

    assert {
        "openclaw-questionnaire-continuation-scheduler.timer",
        "openclaw-questionnaire-continuation-scheduler.service",
        "run_questionnaire_continuation_scheduler.py",
    }.issubset(set(units["patterns"]))
    assert units["allowed_paths"] == [
        "deploy/production_runtime_units.json",
        "scripts/ops/prepare_wecom_callback_ingress_cutover.py",
        "scripts/ops/reconcile_wecom_callback_runtime.py",
    ]
    assert "aicrm_next" in registry["scan_roots"]
    assert "migrations" not in registry["scan_roots"]
