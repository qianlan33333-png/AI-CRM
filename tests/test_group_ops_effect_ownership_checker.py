from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_group_ops_and_broadcast_have_one_provider_owner():
    token_broadcast = (ROOT / "aicrm_next/automation_engine/group_ops/broadcast.py").read_text(encoding="utf-8")
    action_dispatcher = (ROOT / "aicrm_next/automation_engine/group_ops/action_dispatcher.py").read_text(encoding="utf-8")
    broadcast_worker = (ROOT / "aicrm_next/background_jobs/broadcast_queue_worker.py").read_text(encoding="utf-8")

    assert "dispatch_one(" not in token_broadcast
    assert "build_upload_command" not in token_broadcast
    assert "INSERT INTO broadcast_jobs" not in action_dispatcher
    assert "build_wecom_private_message_adapter" not in broadcast_worker
    assert "build_wecom_group_message_adapter" not in broadcast_worker
    assert ".create_private_message_task(" not in broadcast_worker
    assert ".create_group_message_task(" not in broadcast_worker
