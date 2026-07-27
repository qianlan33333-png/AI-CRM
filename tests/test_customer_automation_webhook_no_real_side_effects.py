from __future__ import annotations

import ast
from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_customer_webhook_routes_return_retired_no_side_effect_flags(monkeypatch):
    monkeypatch.delenv("AICRM_NEXT_FORCE_PRODUCTION_DATA", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)

    cases = (
        ("post", "/api/customers/automation/activation-webhook", {"mobile": "13800000000"}),
        ("post", "/api/customers/automation/webhook-deliveries/1/retry", {}),
        ("post", "/api/customers/automation/webhook-deliveries/retry-due", {"limit": 2}),
        ("post", "/api/customer-automation/activation-webhook", {"mobile": "13800000000"}),
        ("get", "/api/customers/automation/signup-conversion/batches", None),
        ("get", "/api/customers/automation/signup-conversion/batches/1", None),
        ("get", "/api/customers/automation/webhook-deliveries", None),
    )
    for method, path, payload in cases:
        request = getattr(client, method)
        response = request(path, json=payload) if payload is not None else request(path)
        assert response.status_code == 410, path
        body = response.json()
        assert body["error"] == "legacy_customer_automation_retired"
        assert body["real_external_call_executed"] is False
        assert body["outbound_webhook_executed"] is False
        assert body["automation_runtime_executed"] is False
        assert body["wecom_send_executed"] is False


def test_customer_webhook_http_handlers_do_not_call_retired_commands_or_read_models():
    api_source = (ROOT / "aicrm_next/automation/automation_engine/api.py").read_text(encoding="utf-8")
    tree = ast.parse(api_source)
    handler_sources = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            "customer_automation" in node.name
            or node.name
            in {
                "activation_webhook",
                "signup_conversion_batches",
                "signup_conversion_batch",
                "customer_automation_webhook_deliveries",
            }
        ):
            handler_sources.append(ast.get_source_segment(api_source, node) or "")
    combined = "\n".join(handler_sources)

    forbidden = [
        "ApplyCustomerActivationWebhookCommand",
        "PlanCustomerWebhookDeliveryRetryCommand",
        "PlanCustomerWebhookDeliveryRetryDueCommand",
        "ApplyActivationWebhookCommand",
        "SignupConversionReadModel",
        "execute_customer_webhook_command",
        "ExternalEffectService",
    ]
    for marker in forbidden:
        assert marker not in combined


def test_signup_conversion_read_model_module_is_removed() -> None:
    assert not (ROOT / "aicrm_next/automation/automation_engine/signup_conversion_read_model.py").exists()
