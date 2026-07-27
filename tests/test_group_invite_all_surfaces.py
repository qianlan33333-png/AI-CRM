from __future__ import annotations

from pathlib import Path

from aicrm_next.extensions.ai.ai_assist.external_campaigns import _content_package_from_sources
from aicrm_next.background_jobs.broadcast_queue_worker import _extract_private_content_package
from aicrm_next.extensions.growth.cloud_orchestrator.repository import _content_payload_for_package
from aicrm_next.ops_enrollment.application import _media_refs_from_batch_request
from aicrm_next.ops_enrollment.dto import BatchSendRequest


ROOT = Path(__file__).resolve().parents[1]


def test_user_ops_private_send_preserves_group_invite_library_id_until_worker_resolution() -> None:
    request = BatchSendRequest(
        content="欢迎加入",
        attachments=[{"msgtype": "link", "link": {"library_id": 78}}],
    )

    media_refs = _media_refs_from_batch_request(request)
    content_package = _extract_private_content_package({"media_refs": media_refs})

    assert media_refs == [{"kind": "link", "index": 0, "library_id": 78}]
    assert content_package["group_invite_library_ids"] == [78]


def test_ai_assistant_and_cloud_plan_preserve_group_invite_content_package() -> None:
    ai_package = _content_package_from_sources(
        {"material_asset_ids": ["group_invite:78"]},
    )
    cloud_payload = _content_payload_for_package(
        {"content_text": "进群交流", "group_invite_library_ids": [78]},
    )

    assert ai_package["group_invite_library_ids"] == ["78"]
    assert cloud_payload["content_package"]["group_invite_library_ids"] == [78]
    assert cloud_payload["group_invite_library_ids"] == [78]


def test_all_requested_frontend_surfaces_expose_group_invite_selection() -> None:
    surfaces = {
        "群发与私信群发": "aicrm_next/frontend_compat/static/admin_console/send_content_composer.js",
        "User Ops 私信群发": "aicrm_next/frontend_compat/static/admin_console/user_ops_batch_send_modal.js",
        "AI 助手": "aicrm_next/extensions/ai/automation_agents/templates/admin_console/automation_agent_edit.html",
        "自动化运营": "aicrm_next/automation_engine/group_ops/static/admin_console/group_ops.js",
        "欢迎语": "aicrm_next/automation_engine/static/admin_console/channel_admission_pages.js",
        "云 Campaign": "aicrm_next/frontend_compat/templates/admin_console/cloud_campaigns_workspace.html",
    }

    for surface, relative_path in surfaces.items():
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "group_invite" in source, surface
        assert "group_invite_library_ids" in source, surface


def test_operator_facing_controls_select_groups_instead_of_invite_materials() -> None:
    composer = (ROOT / "aicrm_next/frontend_compat/static/admin_console/send_content_composer.js").read_text(encoding="utf-8")
    agent_editor = (ROOT / "aicrm_next/extensions/ai/automation_agents/templates/admin_console/automation_agent_edit.html").read_text(encoding="utf-8")
    navigation = (ROOT / "aicrm_next/admin_shell/navigation.py").read_text(encoding="utf-8")

    assert "+选择群聊" in composer
    assert "+选择群聊" in agent_editor
    assert "+群邀请" not in composer
    assert "+群邀请" not in agent_editor
    assert '"label": "群邀请托管"' not in navigation
    assert '"/admin/group-invite-library"' not in navigation
    assert '"label": "群邀请卡片库"' not in navigation
