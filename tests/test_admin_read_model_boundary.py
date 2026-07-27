from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aicrm_next.admin_read_model.application import GetAdminProductsPageQuery
from aicrm_next.admin_read_model.dto import AdminReadDiagnostics
from aicrm_next.admin_read_model.errors import AdminReadModelError
from aicrm_next.admin_read_model.projections import config_payload
from aicrm_next.admin_read_model.repo import LocalContractAdminReadRepository
from tools import check_admin_read_model_boundary as checker

ROOT = Path(__file__).resolve().parents[1]


class FailingProductionRepo:
    source_status = "production_postgres"

    @property
    def is_production(self) -> bool:
        return True

    def rows(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        raise AdminReadModelError("mock sql failure", error_code="mock_sql_failure")

    def one(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
        raise AdminReadModelError("mock sql failure", error_code="mock_sql_failure")

    def count(self, table: str) -> int:
        raise AdminReadModelError("mock sql failure", error_code="mock_sql_failure")

    def runtime_health(self) -> dict[str, Any]:
        return {"production_data_ready": True, "database_mode": "postgres"}

    def diagnostics(self) -> AdminReadDiagnostics:
        return AdminReadDiagnostics(source_status=self.source_status, details={"database_mode": "postgres"})


def test_frontend_compat_admin_real_data_has_no_psycopg_or_sql_boundary():
    source = (ROOT / "aicrm_next" / "app" / "admin_console" / "admin_real_data.py").read_text(encoding="utf-8")

    assert "psycopg" not in source
    assert "SELECT " not in source
    assert "FROM " not in source


def test_production_sql_failure_returns_degraded_payload_not_empty_success():
    payload = GetAdminProductsPageQuery(FailingProductionRepo())()

    assert payload["ok"] is False
    assert payload["degraded"] is True
    assert payload["source_status"] == "production_unavailable"
    assert payload["error_code"] == "mock_sql_failure"
    assert payload["page_error"]
    assert payload["diagnostics"]["source_status"] == "production_unavailable"
    assert "local_contract" not in json.dumps(payload, ensure_ascii=False)


def test_admin_read_model_boundary_checker_returns_ok():
    result = checker.run_check()

    assert result["ok"] is True
    assert result["blockers"] == []


def test_runtime_config_projection_has_no_retired_callback_fallback_state() -> None:
    payload = config_payload(LocalContractAdminReadRepository())
    rendered = json.dumps(payload, ensure_ascii=False)

    assert "5013" not in rendered
    assert "callback_fallback" not in rendered
    assert "回调兜底" not in rendered
    assert [card["label"] for card in payload["cards"]] == ["数据库", "生产数据"]
