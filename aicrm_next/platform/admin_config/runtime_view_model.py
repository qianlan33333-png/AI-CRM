from __future__ import annotations

from typing import Any

from aicrm_next.platform.shared.release import current_release_sha
from aicrm_next.platform.shared.runtime import production_data_ready, runtime_health_state
from aicrm_next.platform.shared.runtime_settings import managed_runtime_setting, runtime_setting


class GetAdminConfigPageQuery:
    """Build the configuration-owned runtime status projection."""

    def __call__(self) -> dict[str, Any]:
        health = runtime_health_state()
        source_status = "production_postgres" if production_data_ready() else "local_contract_probe"
        database_label = health.get("database_mode")
        if database_label == "fixture":
            database_label = "local_contract_probe"
        rows = [
            ["database_mode", database_label],
            ["production_data_ready", health.get("production_data_ready")],
            ["release_sha", current_release_sha()],
            ["wechat_callback_token", "configured" if runtime_setting("WECOM_CALLBACK_TOKEN") else "missing"],
            ["wechat_pay_config", "configured" if runtime_setting("WECHAT_PAY_MCH_ID") else "missing"],
            [
                "oauth_config",
                "configured"
                if managed_runtime_setting("WECHAT_OAUTH_APPID") or managed_runtime_setting("WECHAT_MP_APPID")
                else "missing",
            ],
        ]
        return {
            "ok": True,
            "degraded": False,
            "error_code": "",
            "page_error": "",
            "diagnostics": {"source_status": source_status},
            "source_status": source_status,
            "cards": [
                {"label": "数据库", "value": database_label, "description": "runtime health"},
                {
                    "label": "生产数据",
                    "value": str(health.get("production_data_ready")),
                    "description": "production_data_ready",
                },
            ],
            "sections": [{"title": "运行配置", "headers": ["项目", "状态"], "rows": rows}],
        }


def page_row_count(payload: dict[str, Any]) -> int:
    return sum(len(section.get("rows") or []) for section in payload.get("sections") or [])


__all__ = ["GetAdminConfigPageQuery", "page_row_count"]
