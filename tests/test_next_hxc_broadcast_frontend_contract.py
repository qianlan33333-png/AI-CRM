from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HXC_TEMPLATE = ROOT / "aicrm_next" / "app" / "admin_console" / "templates" / "admin_console" / "hxc_dashboard.html"


def _read() -> str:
    return HXC_TEMPLATE.read_text(encoding="utf-8")


def test_hxc_dashboard_opens_standard_composer_and_calls_next_native_broadcast_task_api() -> None:
    source = _read()

    assert "AICRMSendContentComposer.open" in source
    assert "/api/admin/hxc-dashboard/broadcast-tasks" in source
    assert "hxc_dashboard_broadcast" in source
    assert "idempotency_key" in source
    assert "audience_filter" in source


def test_hxc_dashboard_no_longer_shows_later_pr_prepare_state() -> None:
    source = _read()

    assert "后续 PR 接入" not in source
    assert "已生成标准内容包" not in source


def test_hxc_dashboard_does_not_call_legacy_broadcast_route() -> None:
    source = _read()

    assert not re.search(r"fetch\([\"']/api/admin/hxc-dashboard/broadcast[\"']", source)
    assert '"/api/admin/hxc-dashboard/broadcast"' not in source
    assert "'/api/admin/hxc-dashboard/broadcast'" not in source
