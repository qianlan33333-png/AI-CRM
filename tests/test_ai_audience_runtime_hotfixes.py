from __future__ import annotations

from pathlib import Path

from aicrm_next.extensions.ai.ai_audience_ops.constants import (
    AI_AUDIENCE_REFRESH_DEFAULT_ROW_LIMIT,
    AI_AUDIENCE_REFRESH_MAX_ROW_LIMIT,
)
from aicrm_next.extensions.ai.ai_audience_ops.refresh_service import (
    AI_AUDIENCE_REFRESH_QUERY_TIMEOUT_SECONDS,
    AudienceRefreshService,
)
from aicrm_next.extensions.ai.ai_audience_ops.schemas import RefreshRequest
from aicrm_next.extensions.ai.ai_audience_ops.simple_sql import compile_simple_sql
from scripts.ops.ensure_ai_audience_external_api_env import ensure_allowed_prefixes


ROOT = Path(__file__).resolve().parents[1]


def test_simple_sql_uses_sqlalchemy_safe_timestamptz_cast() -> None:
    compiled = compile_simple_sql("SELECT DISTINCT external_userid FROM audience_read.wecom_contacts_v1")

    assert "CAST(:refresh_started_at AS timestamptz) AS event_at" in compiled
    assert ":refresh_started_at::timestamptz" not in compiled


def test_huangxiaocan_member_usage_migration_casts_text_timestamps_before_coalesce() -> None:
    source = (ROOT / "migrations/versions/0060_ai_audience_huangxiaocan_member_usage_view.py").read_text(encoding="utf-8")

    assert "COALESCE(last_msg_at::timestamptz, refreshed_at::timestamptz) AS used_at" in source
    assert "COALESCE(finished_at::timestamptz, updated_at::timestamptz, created_at::timestamptz, CURRENT_TIMESTAMP) AS used_at" in source
    assert "COALESCE(updated_at::timestamptz, created_at::timestamptz, CURRENT_TIMESTAMP) AS used_at" in source
    assert "COALESCE(finished_at, updated_at, created_at, CURRENT_TIMESTAMP)::timestamptz" not in source


def test_ai_audience_refresh_query_timeout_allows_heavier_catalog_views() -> None:
    source = (ROOT / "aicrm_next/extensions/ai/ai_audience_ops/refresh_service.py").read_text(encoding="utf-8")

    assert AI_AUDIENCE_REFRESH_QUERY_TIMEOUT_SECONDS == 120
    assert "timeout_seconds=AI_AUDIENCE_REFRESH_QUERY_TIMEOUT_SECONDS" in source
    assert "timeout_seconds=30" not in source


def test_ai_audience_refresh_defaults_to_full_platform_row_limit() -> None:
    source = (ROOT / "aicrm_next/extensions/ai/ai_audience_ops/refresh_service.py").read_text(encoding="utf-8")

    assert AI_AUDIENCE_REFRESH_DEFAULT_ROW_LIMIT == 100000
    assert AI_AUDIENCE_REFRESH_MAX_ROW_LIMIT == 100000
    assert RefreshRequest().row_limit == AI_AUDIENCE_REFRESH_DEFAULT_ROW_LIMIT
    assert AudienceRefreshService.refresh_package.__kwdefaults__["row_limit"] == AI_AUDIENCE_REFRESH_DEFAULT_ROW_LIMIT
    assert "row_limit: int = 5000" not in source


def test_ai_audience_external_api_env_allows_runtime_business_prefix(tmp_path) -> None:
    env_path = tmp_path / "aicrm.env"
    env_path.write_text("DATABASE_URL=postgresql://example\nAICRM_AI_AUDIENCE_SPEC_ALLOWED_PREFIXES=prod_verify_\n", encoding="utf-8")

    changed = ensure_allowed_prefixes(env_path)

    assert changed is True
    assert "AICRM_AI_AUDIENCE_SPEC_ALLOWED_PREFIXES=prod_verify_,audience_" in env_path.read_text(encoding="utf-8")


def test_deploy_workflow_repairs_ai_audience_external_api_prefixes() -> None:
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")

    assert "scripts/ops/ensure_ai_audience_external_api_env.py /home/ubuntu/.openclaw-wecom-pg.env" in workflow
