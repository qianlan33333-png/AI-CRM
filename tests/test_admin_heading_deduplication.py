from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_shell_owned_pages_do_not_repeat_the_shell_title_as_first_card_heading() -> None:
    assert "<h2>企微标签管理</h2>" not in _read(
        "aicrm_next/customer_tags/templates/admin_console/config_wecom_tags.html"
    )
    assert "<h2>系统配置向导</h2>" not in _read(
        "aicrm_next/frontend_compat/templates/admin_console/setup_wizard.html"
    )
    assert "<h2>群发发送人管理</h2>" not in _read(
        "aicrm_next/frontend_compat/templates/admin_console/hxc_send_config.html"
    )
    assert 'title="运行状态快照"' in _read("aicrm_next/admin_config/api.py")


def test_automation_agent_pages_leave_the_single_h1_to_admin_shell() -> None:
    list_template = _read(
        "aicrm_next/extensions/ai/automation_agents/templates/admin_console/automation_agent_list.html"
    )
    edit_template = _read(
        "aicrm_next/extensions/ai/automation_agents/templates/admin_console/automation_agent_edit.html"
    )

    assert "<h1" not in list_template
    assert "<h1" not in edit_template
    assert "<h2 class=\"automation-agent-title\">自动化列表</h2>" in list_template
    assert "<h2 class=\"automation-agent-subtitle\">基本信息</h2>" in edit_template
