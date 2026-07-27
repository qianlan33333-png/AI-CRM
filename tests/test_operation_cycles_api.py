from __future__ import annotations

import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.extensions.hxc.operation_cycles import api as operation_cycles_api
from aicrm_next.extensions.hxc.operation_cycles.domain import OperationCycleConflictError
from aicrm_next.platform.platform_foundation.auth_platform.context import PrincipalType
from tests.admin_auth_test_helpers import install_admin_auth_service, install_admin_session


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "operation_cycles" / "hxc_monday_20260713_snapshot.json"


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_ADMIN_AUTH_ENFORCED", "true")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("SECRET_KEY", "operation-cycle-api-test-secret")
    client = TestClient(create_app(), raise_server_exceptions=False)
    install_admin_auth_service(client)
    return client


def _machine_token(
    client: TestClient,
    *,
    purpose: str,
    capabilities: tuple[str, ...],
    client_id: str,
) -> str:
    service = client.app.state.auth_client_service
    issued = service.create_client(
        client_id=client_id,
        principal_id=f"api_client:{client_id}",
        principal_type=PrincipalType.API_CLIENT,
        purpose=purpose,
        display_name=f"Pytest {purpose}",
        audiences=("external_integration",),
        scopes=("write",),
        capabilities=capabilities,
    )
    basic = base64.b64encode(f"{issued.client.client_id}:{issued.client_secret}".encode()).decode()
    response = client.post(
        "/oauth/token",
        headers={"Authorization": f"Basic {basic}"},
        data={
            "grant_type": "client_credentials",
            "audience": "external_integration",
            "scope": "write",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _headers(token: str, *, idempotency_key: str = "hxc-monday-20260713-r1") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": idempotency_key,
    }


def test_ops_reporter_can_submit_aggregate_snapshot_without_external_effect(monkeypatch) -> None:
    client = _client(monkeypatch)
    token = _machine_token(
        client,
        purpose="ops_reporter",
        capabilities=("operation_cycle_report_write",),
        client_id="pytest-ops-reporter",
    )
    captured: dict = {}

    def report(snapshot, *, idempotency_key, reporter_id, client_id):
        captured.update(
            {
                "snapshot": snapshot,
                "idempotency_key": idempotency_key,
                "reporter_id": reporter_id,
                "client_id": client_id,
            }
        )
        return {
            "ok": True,
            "receipt_id": "ocrcpt_test",
            "strategy_key": snapshot.strategy.strategy_key,
            "run_key": snapshot.run.run_key,
            "accepted_revision": snapshot.snapshot_revision,
            "projection_updated": True,
            "snapshot_hash": "a" * 64,
        }

    monkeypatch.setattr(operation_cycles_api, "report_operation_cycle", report)
    response = client.post(
        "/api/operation-cycles/reports",
        headers=_headers(token),
        json=_fixture(),
    )

    assert response.status_code == 200, response.text
    assert response.json()["projection_updated"] is True
    assert response.headers["X-AICRM-Real-External-Call-Executed"] == "false"
    assert captured["idempotency_key"] == "hxc-monday-20260713-r1"
    assert captured["reporter_id"] == "api_client:pytest-ops-reporter"
    assert captured["client_id"] == "pytest-ops-reporter"
    assert captured["snapshot"].external_effects == "none"
    assert captured["snapshot"].documents.retrospective_details.markdown.startswith("# 2026-07-13 本周复盘明细")


def test_wrong_machine_purpose_and_admin_session_cannot_report(monkeypatch) -> None:
    client = _client(monkeypatch)
    wrong_token = _machine_token(
        client,
        purpose="campaign_agent",
        capabilities=("operation_cycle_report_write",),
        client_id="pytest-wrong-reporter",
    )
    wrong_machine = client.post(
        "/api/operation-cycles/reports",
        headers=_headers(wrong_token),
        json=_fixture(),
    )
    assert wrong_machine.status_code == 403

    install_admin_session(client, "super_admin")
    admin = client.post(
        "/api/operation-cycles/reports",
        headers={"Idempotency-Key": "admin-must-not-report"},
        json=_fixture(),
    )
    assert admin.status_code == 401


def test_report_requires_idempotency_rejects_private_payload_and_limits_body(monkeypatch) -> None:
    client = _client(monkeypatch)
    token = _machine_token(
        client,
        purpose="ops_reporter",
        capabilities=("operation_cycle_report_write",),
        client_id="pytest-safe-reporter",
    )
    called = False

    def should_not_report(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("invalid reports must not reach persistence")

    monkeypatch.setattr(operation_cycles_api, "report_operation_cycle", should_not_report)
    missing_key = client.post(
        "/api/operation-cycles/reports",
        headers={"Authorization": f"Bearer {token}"},
        json=_fixture(),
    )
    assert missing_key.status_code == 400

    private = _fixture()
    private["strategy"]["definition"]["external_userid"] = "must-not-be-stored"
    rejected = client.post(
        "/api/operation-cycles/reports",
        headers=_headers(token, idempotency_key="private-rejected"),
        json=private,
    )
    assert rejected.status_code == 422

    oversized = client.post(
        "/api/operation-cycles/reports",
        headers={
            **_headers(token, idempotency_key="oversized-rejected"),
            "Content-Type": "application/json",
        },
        content=b"{" + b" " * operation_cycles_api.MAX_REPORT_BYTES + b"}",
    )
    assert oversized.status_code == 413
    assert called is False


def test_report_conflict_maps_to_409_without_external_effect(monkeypatch) -> None:
    client = _client(monkeypatch)
    token = _machine_token(
        client,
        purpose="ops_reporter",
        capabilities=("operation_cycle_report_write",),
        client_id="pytest-conflict-reporter",
    )

    def conflict(*_args, **_kwargs):
        raise OperationCycleConflictError("snapshot_revision_regression")

    monkeypatch.setattr(operation_cycles_api, "report_operation_cycle", conflict)
    response = client.post(
        "/api/operation-cycles/reports",
        headers=_headers(token, idempotency_key="conflict-r1"),
        json=_fixture(),
    )

    assert response.status_code == 409
    assert response.json()["error"] == "snapshot_revision_regression"
    assert response.json()["real_external_call_executed"] is False


def test_admin_can_read_but_ops_reporter_cannot_use_admin_routes(monkeypatch) -> None:
    client = _client(monkeypatch)
    monkeypatch.setattr(
        operation_cycles_api,
        "list_strategies",
        lambda **_: {"ok": True, "items": [], "limit": 50, "offset": 0},
    )
    install_admin_session(client, "super_admin")
    admin_response = client.get("/api/admin/operation-cycles/strategies")
    assert admin_response.status_code == 200
    assert admin_response.json()["items"] == []

    client.cookies.clear()
    token = _machine_token(
        client,
        purpose="ops_reporter",
        capabilities=("operation_cycle_report_write",),
        client_id="pytest-read-forbidden-reporter",
    )
    machine_response = client.get(
        "/api/admin/operation-cycles/strategies",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert machine_response.status_code == 403


def test_operation_cycle_admin_surface_exposes_no_write_routes(monkeypatch) -> None:
    client = _client(monkeypatch)
    openapi = client.get("/openapi.json").json()
    methods_by_path = {
        path: set(definition)
        for path, definition in openapi["paths"].items()
        if path.startswith("/api/admin/operation-cycles") or path.startswith("/admin/operation-cycles")
    }

    assert methods_by_path
    assert all(methods <= {"get"} for methods in methods_by_path.values())
    report_operation = openapi["paths"]["/api/operation-cycles/reports"]["post"]
    assert report_operation["requestBody"]["required"] is True
    assert report_operation["requestBody"]["content"]["application/json"]["schema"]["title"] == ("OperationCycleSnapshotV1")
