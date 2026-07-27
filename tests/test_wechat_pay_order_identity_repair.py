from __future__ import annotations

from pathlib import Path
from tests.admin_auth_test_helpers import install_admin_action_tokens


def test_order_identity_repair_route_is_retired_after_auth(next_client, monkeypatch) -> None:
    del monkeypatch
    token = install_admin_action_tokens(
        next_client,
        ("POST", "/api/admin/jobs/order-identity-repair/run"),
    )[("POST", "/api/admin/jobs/order-identity-repair/run")]

    response = next_client.post(
        "/api/admin/jobs/order-identity-repair/run",
        headers={"X-Admin-Action-Token": token},
        json={"limit": 10, "max_attempts": 3, "dry_run": False},
    )

    assert response.status_code == 410
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"] == "order_identity_repair_retired"
    assert payload["retired"] is True
    assert payload["replacement"] == "current_order_customer_identity_projection"
    assert payload["real_external_call_executed"] is False


def test_order_identity_repair_requires_cron_or_action_token_before_retired_response(next_client) -> None:
    response = next_client.post("/api/admin/jobs/order-identity-repair/run", json={})

    assert response.status_code == 401
    assert response.json()["ok"] is False


def test_order_identity_repair_runtime_module_is_removed() -> None:
    routes_source = Path("aicrm_next/admin_jobs/routes.py").read_text(encoding="utf-8")

    assert not Path("aicrm_next/extensions/commerce/commerce/order_identity_repair.py").exists()
    assert "aicrm_next.extensions.commerce.commerce.order_identity_repair" not in routes_source
    assert "repair_missing_order_identities" not in routes_source
    assert "order_identity_repair_retired" in routes_source


def test_order_identity_repair_contract_migration_drops_orphan_table() -> None:
    source = Path("migrations/versions/0091_retire_wechat_pay_order_identity_repair.py").read_text(encoding="utf-8")
    conftest_source = Path("tests/conftest.py").read_text(encoding="utf-8")

    assert 'down_revision = "0090_automation_agent_output_guard"' in source
    assert "DROP INDEX IF EXISTS idx_wechat_pay_order_identity_repair_trade_no" in source
    assert "DROP INDEX IF EXISTS idx_wechat_pay_order_identity_repair_due" in source
    assert "DROP TABLE IF EXISTS wechat_pay_order_identity_repair" in source
    assert '"wechat_pay_order_identity_repair"' not in conftest_source


def test_historical_repair_migration_skips_fresh_schema_without_order_table() -> None:
    source = Path("migrations/versions/0062_wechat_pay_order_identity_repair.py").read_text(encoding="utf-8")

    assert 'inspect(op.get_bind()).has_table("wechat_pay_orders")' in source
    assert "return" in source.split('has_table("wechat_pay_orders")', 1)[1].split("op.execute", 1)[0]
