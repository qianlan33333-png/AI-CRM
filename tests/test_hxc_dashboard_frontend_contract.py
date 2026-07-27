from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HXC_TEMPLATE = ROOT / "aicrm_next/app/admin_console/templates/admin_console/hxc_dashboard.html"
SEND_CONFIG_TEMPLATE = ROOT / "aicrm_next/app/admin_console/templates/admin_console/hxc_send_config.html"


def test_dashboard_frontend_calls_next_safe_mode_routes() -> None:
    source = HXC_TEMPLATE.read_text(encoding="utf-8")

    assert "/api/admin/hxc-dashboard/refresh" in source
    assert "/api/admin/hxc-dashboard/broadcast-tasks" in source
    assert "/admin/hxc-send-config" in source
    assert not re.search(r"fetch\([\"']/api/admin/hxc-dashboard/broadcast[\"']", source)


def test_send_config_frontend_calls_next_safe_mode_routes() -> None:
    source = SEND_CONFIG_TEMPLATE.read_text(encoding="utf-8")

    assert "/api/admin/hxc-dashboard/send-config" in source
    assert "/api/admin/hxc-dashboard/refresh-directory" in source
    assert "method: 'DELETE'" in source or 'method: "DELETE"' in source
