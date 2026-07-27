from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

from starlette.requests import Request

from aicrm_next.extensions.commerce.public_product import h5_wechat_pay
from aicrm_next.shared.signed_context import SIDEBAR_PRODUCT_CONTEXT_COOKIE, build_sidebar_product_context_token


def _request(path: str, *, context_token: str = "", query: str = "") -> Request:
    headers = [(b"host", b"pay.example.test")]
    if context_token:
        headers.append((b"cookie", f"{SIDEBAR_PRODUCT_CONTEXT_COOKIE}={context_token}".encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": query.encode("utf-8"),
            "headers": headers,
            "scheme": "https",
            "server": ("pay.example.test", 443),
            "client": ("testclient", 12345),
        }
    )


def test_checkout_page_state_reads_http_only_context_cookie_without_url_credential(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ACTION_TOKEN_SECRET", "h5-pay-sidebar-context-secret")
    token = build_sidebar_product_context_token(
        external_userid="wm_ext_checkout_001",
        owner_userid="sales_01",
        bind_by_userid="advisor_01",
    )
    product = {
        "product_code": "demo",
        "title": "Demo product",
        "price_cents": 9900,
        "currency": "CNY",
        "require_mobile": False,
    }

    state = h5_wechat_pay.checkout_page_state(product, _request("/pay/demo", context_token=token))

    assert state["context_status"] == "valid"
    parsed_start = urlparse(state["oauth_start_url"])
    assert parsed_start.path == "/api/h5/wechat-pay/oauth/start"
    assert parse_qs(parsed_start.query)["return_url"] == ["/pay/demo"]
    assert "ctx" not in parse_qs(parsed_start.query)


def test_h5_pay_runtime_uses_native_sidebar_context_imports() -> None:
    source = Path("aicrm_next/extensions/commerce/public_product/h5_wechat_pay.py").read_text(encoding="utf-8")
    forbidden = [
        "wecom_ability" + "_service.infra." + "signed_context",
        "wecom_ability" + "_service.domains.wechat_pay." + "sidebar_context",
    ]

    for marker in forbidden:
        assert marker not in source
    assert "from aicrm_next.shared.signed_context import" in source
    assert "from .sidebar_order_context import" in source
