from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]


def test_global_application_error_handler_returns_status_specific_json(monkeypatch) -> None:
    from aicrm_next.main import create_app
    from aicrm_next.platform.shared.errors import ContractError

    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    app = create_app()

    @app.get("/__test__/contract-error")
    def contract_error():
        raise ContractError("bad request contract")

    response = TestClient(app, raise_server_exceptions=False).get("/__test__/contract-error")

    assert response.status_code == 400
    assert response.json() == {
        "ok": False,
        "error_code": "contract",
        "detail": "bad request contract",
    }
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"


def test_global_unhandled_error_handler_logs_and_hides_internal_details(monkeypatch) -> None:
    from aicrm_next.main import create_app

    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    app = create_app()

    @app.get("/__test__/boom")
    def boom():
        raise RuntimeError("SELECT secret FROM production_table")

    response = TestClient(app, raise_server_exceptions=False).get("/__test__/boom")

    assert response.status_code == 500
    assert response.json() == {
        "ok": False,
        "error_code": "internal_server_error",
        "detail": "internal server error",
    }
    assert "production_table" not in response.text


def test_commerce_unknown_exception_maps_to_safe_500() -> None:
    from aicrm_next.extensions.commerce.commerce.api import _raise_http

    with pytest.raises(HTTPException) as raised:
        _raise_http(RuntimeError("SELECT mobile FROM crm_user_identity"))

    assert raised.value.status_code == 500
    assert raised.value.detail == {
        "error_code": "commerce_internal_error",
        "message": "internal commerce error",
    }


def test_wechat_shop_notify_does_not_return_success_from_generic_exception_handler() -> None:
    source = (ROOT / "aicrm_next" / "extensions" / "commerce" / "commerce" / "api.py").read_text(encoding="utf-8")
    marker = 'safe_log_exception(logger, "wechat shop notify failed before durable event handling", exc)'
    assert marker in source
    handler = source.split(marker, 1)[1]
    handler = handler.split("\n\n", 1)[0]

    assert 'return Response("success"' not in handler
    assert 'status_code=500' in handler
