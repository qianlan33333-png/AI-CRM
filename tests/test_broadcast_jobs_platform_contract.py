from __future__ import annotations

from aicrm_next.background_jobs.broadcast_queue_worker import SafeSkippedBroadcastDispatcher


def test_default_broadcast_dispatcher_skips_unknown_source_type() -> None:
    result = SafeSkippedBroadcastDispatcher().dispatch(
        {
            "id": 1,
            "source_type": "unknown",
            "source_table": "mystery_table",
            "content_type": "mystery",
            "channel": "",
            "target_kind": "",
            "content_payload": {"channel": "unknown_channel"},
        }
    )

    assert result == {
        "ok": False,
        "status": "skipped",
        "reason": "next_native_dispatcher_missing",
        "source_type": "unknown",
        "source_table": "mystery_table",
        "content_type": "mystery",
        "channel": "",
        "target_kind": "",
        "payload_channel": "unknown_channel",
    }


def test_group_broadcast_payload_is_delegated_without_building_adapter(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_WECOM_GROUP_ADAPTER_MODE", "disabled")

    result = SafeSkippedBroadcastDispatcher().dispatch(
        {
            "id": 2,
            "source_type": "group_ops",
            "content_payload": {
                "channel": "wecom_customer_group",
                "sender": "owner_1",
                "chat_ids": ["chat_a"],
                "text": {"content": "hello"},
            },
        }
    )

    assert result["ok"] is True
    assert result["status"] == "delegated"
    assert result["external_effect_job_ids"] == []
    assert len(result["effect_plan_requests"]) == 1
    assert result["side_effect_executed"] is False


def test_group_broadcast_global_execution_mode_is_enforced_by_external_effect_owner(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_WECOM_EXECUTION_MODE", "disabled")

    def fail_adapter():
        raise AssertionError("broadcast planner must never build a provider adapter")

    monkeypatch.setattr("aicrm_next.integration_gateway.wecom_group_adapter.build_wecom_group_message_adapter", fail_adapter)

    result = SafeSkippedBroadcastDispatcher().dispatch(
        {
            "id": 3,
            "source_type": "group_ops",
            "content_payload": {
                "channel": "wecom_customer_group",
                "sender": "owner_1",
                "chat_ids": ["chat_a"],
                "text": {"content": "hello"},
            },
        }
    )

    assert result["ok"] is True
    assert result["status"] == "delegated"
    assert len(result["effect_plan_requests"]) == 1
    assert result["side_effect_executed"] is False
