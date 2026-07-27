from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_channel_list_does_not_expose_retired_program_availability_filter() -> None:
    source = (PROJECT_ROOT / "aicrm_next" / "automation_engine" / "channels_api.py").read_text(encoding="utf-8")

    assert "available_for_program_id" not in source
    assert "automation_program_channel_binding" not in source


def test_wecom_customer_acquisition_links_do_not_expose_retired_program_fields() -> None:
    api_source = (PROJECT_ROOT / "aicrm_next" / "automation_engine" / "channels_api.py").read_text(encoding="utf-8")
    template_source = (
        PROJECT_ROOT / "aicrm_next" / "app" / "admin_console" / "templates" / "admin_console" / "wecom_customer_acquisition_links.html"
    ).read_text(encoding="utf-8")

    for retired in ("program_id", "workflow_id", "initial_audience_code", "初始人群", "不再入池"):
        assert retired not in api_source
        assert retired not in template_source
    assert "供 AI 人群包查询" in template_source
