from __future__ import annotations

from aicrm_next.channels.integration_gateway.wecom_group_adapter import WeComGroupMessageAdapter


def test_group_ops_message_adapter_fake_mode_verifies_exact_targets() -> None:
    adapter = WeComGroupMessageAdapter(mode="fake")

    result = adapter.create_group_message_task(
        {
            "sender": "owner_1",
            "chat_ids": ["chat_a", "chat_b"],
            "text": {"content": "hello"},
        },
        idempotency_key="group-ops-fake",
    )

    assert result["ok"] is True
    assert result["side_effect_executed"] is False
    assert result["exact_target_verified"] is True
    assert result["requested_chat_ids"] == ["chat_a", "chat_b"]


def test_group_ops_message_adapter_respects_global_execution_disabled(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_WECOM_EXECUTION_MODE", "disabled")
    adapter = WeComGroupMessageAdapter(mode=None)

    result = adapter.create_group_message_task(
        {
            "sender": "owner_1",
            "chat_ids": ["chat_a"],
            "text": {"content": "hello"},
        },
        idempotency_key="group-ops-global-disabled",
    )

    assert result["ok"] is False
    assert result["mode"] == "disabled"
    assert result["side_effect_executed"] is False


def test_group_ops_message_adapter_disabled_mode_blocks_send() -> None:
    adapter = WeComGroupMessageAdapter(mode="disabled")

    result = adapter.create_group_message_task(
        {
            "sender": "owner_1",
            "chat_ids": ["chat_a"],
            "text": {"content": "hello"},
        },
        idempotency_key="group-ops-disabled",
    )

    assert result["ok"] is False
    assert result["side_effect_executed"] is False
    assert result["error_code"] == "wecom_group_message_disabled"
    assert result["exact_target_verified"] is False
