from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "aicrm_next" / "app" / "admin_console"
AUTOMATION = ROOT / "aicrm_next" / "automation" / "automation_engine"
STATIC = FRONTEND / "static" / "admin_console"
TEMPLATES = FRONTEND / "templates" / "admin_console"
AUTOMATION_STATIC = AUTOMATION / "static" / "admin_console"
AUTOMATION_TEMPLATES = AUTOMATION / "templates" / "admin_console"
GROUP_OPS_STATIC = AUTOMATION / "group_ops" / "static" / "admin_console"
GROUP_OPS_TEMPLATES = AUTOMATION / "group_ops" / "templates" / "admin_console"
OPERATION_PANEL = AUTOMATION_TEMPLATES / "_automation_operation_orchestration_panel.html"
OPERATION_JS = AUTOMATION_STATIC / "automation_operation_orchestration_panel.js"
HXC_TEMPLATE = TEMPLATES / "hxc_dashboard.html"
GROUP_OPS_TEMPLATE = GROUP_OPS_TEMPLATES / "group_ops.html"
CHANNEL_TEMPLATE = AUTOMATION_TEMPLATES / "channel_code_form.html"
CAMPAIGN_TEMPLATE = TEMPLATES / "cloud_campaigns_workspace.html"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_next_automation_operation_panel_has_no_private_asset_binding() -> None:
    assert not OPERATION_PANEL.exists()
    assert not OPERATION_JS.exists()


def test_next_hxc_migrated_template_has_no_private_material_grid() -> None:
    source = _read(HXC_TEMPLATE)

    assert "AICRMSendContentComposer.open" in source
    for forbidden in [
        "hxc-" + "img-grid",
        "hxc-" + "mp-grid",
        "hxc-" + "asset-card",
        "hxc-" + "broadcast-modal",
        "load" + "Assets",
        "render" + "ImageGrid",
        "render" + "MpGrid",
    ]:
        assert forbidden not in source


def test_next_campaign_cleanup_guard_only_applies_after_migration() -> None:
    source = _read(CAMPAIGN_TEMPLATE)

    if "AICRMSendContentComposer.open" not in source:
        return
    assert "attach" + "MiniprogramPicker" not in source
    assert "mount" + "ImagePicker" not in source
    assert "请输入附件" + "素材编号" not in source


def test_standard_send_content_components_still_exist() -> None:
    composer = _read(STATIC / "send_content_composer.js")
    picker = _read(STATIC / "material_picker.js")

    assert "window.AICRMSendContentComposer" in composer
    assert "AICRMSendContentComposer" in composer
    assert "window.AICRMMaterialPicker" in picker
    assert "AICRMMaterialPicker" in picker


def test_migrated_business_pages_do_not_direct_fetch_material_libraries() -> None:
    migrated_pages = [
        (HXC_TEMPLATE, ""),
        (CAMPAIGN_TEMPLATE, ""),
        (GROUP_OPS_TEMPLATE, _read(GROUP_OPS_STATIC / "group_ops.js")),
        (
            CHANNEL_TEMPLATE,
            _read(AUTOMATION_STATIC / "channel_admission_pages.js"),
        ),
    ]
    direct_fetch_markers = [
        "/api/admin/image-library",
        "/api/admin/miniprogram-library",
        "/api/admin/attachment-library",
    ]

    for path, extra_source in migrated_pages:
        source = _read(path) + extra_source
        assert "AICRMSendContentComposer.open" in source
        for marker in direct_fetch_markers:
            assert marker not in source
