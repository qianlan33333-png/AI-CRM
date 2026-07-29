from __future__ import annotations

import pytest

from aicrm_next.channels.integration_gateway.dispatch import DispatchGateway, McpToolDispatcher
from aicrm_next.platform.shared.errors import ContractError


class FakeBlockedWeComAdapter:
    def send_private_message(self, *, owner_userid, external_userids, content, media_refs):
        return {
            "ok": False,
            "adapter": "fake_blocked_wecom",
            "error_code": "real_external_call_blocked",
            "error_message": "blocked by test fake",
            "result": {},
        }


def test_dispatcher_prefers_explicit_external_userid() -> None:
    dispatcher = McpToolDispatcher()

    assert dispatcher.resolve_external_userid({"external_userid": " wx_ext_001 ", "customer_ref": "ignored"}) == "wx_ext_001"


def test_dispatcher_requires_customer_reference() -> None:
    dispatcher = McpToolDispatcher()

    with pytest.raises(ContractError, match="customer_ref or external_userid is required"):
        dispatcher.resolve_external_userid({})


def test_dispatch_gateway_returns_blocked_summary_without_real_send() -> None:
    gateway = DispatchGateway(adapter=FakeBlockedWeComAdapter())

    payload = gateway.dispatch_user_ops_private_message_batch(
        owner_bucket={"sender_userid": "sales_01", "external_userids": ["wx_ext_001"]},
        content="hello",
        images=[{"id": "image_1"}],
    )

    assert payload["status"] == "blocked"
    assert payload["dispatch_adapter"] == "fake_blocked_wecom"
    assert payload["sender_userid"] == "sales_01"
    assert payload["external_userids"] == ["wx_ext_001"]
    assert payload["target_count"] == 1
    assert payload["image_count"] == 1
