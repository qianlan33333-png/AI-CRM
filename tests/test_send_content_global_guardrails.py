from __future__ import annotations

from pathlib import Path

from aicrm_next.engagement.send_content.dto import (
    SendContentPackage,
    SendContentPreviewRequest,
    SendContentValidateRequest,
)


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "aicrm_next" / "app" / "admin_console"
AUTOMATION = ROOT / "aicrm_next" / "automation" / "automation_engine"
STATIC = FRONTEND / "static" / "admin_console"
TEMPLATES = FRONTEND / "templates" / "admin_console"
AUTOMATION_STATIC = AUTOMATION / "static" / "admin_console"
AUTOMATION_TEMPLATES = AUTOMATION / "templates" / "admin_console"
GROUP_OPS_STATIC = AUTOMATION / "group_ops" / "static" / "admin_console"
GROUP_OPS_TEMPLATES = AUTOMATION / "group_ops" / "templates" / "admin_console"
DOC = ROOT / "docs" / "migration" / "send_content_next_surface_inventory.md"


SURFACES = {
    "hxc_dashboard": [TEMPLATES / "hxc_dashboard.html"],
    "channel_welcome": [
        AUTOMATION_TEMPLATES / "channel_code_form.html",
        AUTOMATION_STATIC / "channel_admission_pages.js",
    ],
    "group_ops_action": [
        GROUP_OPS_TEMPLATES / "group_ops.html",
        GROUP_OPS_STATIC / "group_ops.js",
    ],
    "campaign_step": [TEMPLATES / "cloud_campaigns_workspace.html"],
}
RETIRED_OPERATION_PANEL = AUTOMATION_TEMPLATES / "_automation_operation_orchestration_panel.html"
RETIRED_OPERATION_JS = AUTOMATION_STATIC / "automation_operation_orchestration_panel.js"


DIRECT_MATERIAL_API_MARKERS = [
    "/api/admin/image-library",
    "/api/admin/miniprogram-library",
    "/api/admin/attachment-library",
]


PRIVATE_PICKER_MARKERS = [
    "attach" + "MiniprogramPicker",
    "mount" + "ImagePicker",
    "setup" + "WelcomeMaterialPicker",
    "setup" + "ChannelWelcomeMaterialPicker",
    "render" + "AttachmentJsonTextarea",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _surface_source(paths: list[Path]) -> str:
    return "\n".join(_read(path) for path in paths)


def test_all_migrated_send_content_surfaces_use_standard_composer() -> None:
    for name, paths in SURFACES.items():
        source = _surface_source(paths)
        api = "AICRMSendContentComposer.mount" if name == "channel_welcome" else "AICRMSendContentComposer.open"
        assert api in source, name


def test_retired_automation_operation_surface_stays_removed() -> None:
    assert not RETIRED_OPERATION_PANEL.exists()
    assert not RETIRED_OPERATION_JS.exists()


def test_migrated_send_content_surfaces_do_not_fetch_material_libraries_directly() -> None:
    for name, paths in SURFACES.items():
        source = _surface_source(paths)
        for marker in DIRECT_MATERIAL_API_MARKERS:
            assert marker not in source, f"{name} must use AICRMMaterialPicker, not {marker}"


def test_migrated_send_content_surfaces_do_not_define_private_material_pickers() -> None:
    for name, paths in SURFACES.items():
        source = _surface_source(paths)
        for marker in PRIVATE_PICKER_MARKERS:
            assert marker not in source, f"{name} reintroduced private picker {marker}"


def test_group_ops_does_not_expose_operator_json_attachment_fields() -> None:
    source = _surface_source(SURFACES["group_ops_action"])

    for marker in [
        "node_" + "attachments",
        "素材 " + "JSON",
        "attachments " + "JSON",
    ]:
        assert marker not in source


def test_send_content_package_contract_stays_narrow() -> None:
    assert set(SendContentPackage.model_fields) == {
        "content_text",
        "image_library_ids",
        "miniprogram_library_ids",
        "attachment_library_ids",
        "group_invite_library_ids",
        "dynamic_miniprogram_card",
    }
    assert set(SendContentValidateRequest.model_fields) == {"content_package", "text_enabled", "require_body"}
    assert set(SendContentPreviewRequest.model_fields) == {"content_package", "text_enabled", "require_body"}


def test_standard_composer_does_not_emit_outer_business_fields() -> None:
    source = _read(STATIC / "send_content_composer.js")

    for forbidden in [
        "source_type",
        "source_id",
        "delivery_mode",
        "audience_filter",
        "sender_userid",
        "content_mode",
    ]:
        assert forbidden not in source


def test_send_content_surface_inventory_has_required_status_sections() -> None:
    source = _read(DOC)

    assert "## Migrated" in source
    assert "## Pending" in source
    assert "## Legacy Only / Not Migrating" in source
    for row in [
        "| 自动化运营编排 | retired |",
        "| HXC 漏斗看板 | migrated |",
        "| 渠道码中心欢迎语 | migrated |",
        "| 群运营计划动作 | migrated |",
        "| Campaign Step | migrated |",
        "| Sidebar 单发 | pending |",
    ]:
        assert row in source
