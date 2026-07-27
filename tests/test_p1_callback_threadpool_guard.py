from __future__ import annotations

import inspect


def test_external_callback_and_webhook_routes_are_sync_threadpool_handlers() -> None:
    from aicrm_next.extensions.ai.ai_audience_ops import api as ai_audience_api
    from aicrm_next.extensions.ai.automation_agents import api as automation_agents_api
    from aicrm_next.channels.channel_entry import api as channel_entry_api
    from aicrm_next.extensions.commerce.commerce import api as commerce_api
    from aicrm_next.extensions.commerce.public_product import api as public_product_api

    critical_handlers = [
        commerce_api.wechat_shop_notify,
        commerce_api.wechat_refund_notify,
        public_product_api.h5_wechat_pay_create_jsapi_order,
        public_product_api.h5_wechat_pay_notify,
        channel_entry_api.external_contact_callback,
        channel_entry_api.wecom_events,
        automation_agents_api.automation_agent_audience_webhook,
        ai_audience_api.inbound_webhook,
        ai_audience_api.test_agent_webhook,
    ]

    assert all(not inspect.iscoroutinefunction(handler) for handler in critical_handlers)


def test_sync_callback_handlers_read_raw_body_via_threadpool_helper() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for relative_path in (
        "aicrm_next/extensions/commerce/commerce/api.py",
        "aicrm_next/extensions/commerce/public_product/api.py",
        "aicrm_next/channels/channel_entry/api.py",
        "aicrm_next/extensions/ai/automation_agents/api.py",
        "aicrm_next/extensions/ai/ai_audience_ops/api.py",
    ):
        source = (root / relative_path).read_text(encoding="utf-8")
        assert "read_request_body(" in source


def test_uvicorn_workers_are_explicit_and_env_driven(monkeypatch) -> None:
    import app

    monkeypatch.delenv("AICRM_UVICORN_WORKERS", raising=False)
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    assert app._workers() == 1

    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    assert app._workers() == 4

    monkeypatch.setenv("AICRM_UVICORN_WORKERS", "bad")
    assert app._workers() == 1
