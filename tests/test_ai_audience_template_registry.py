from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aicrm_next.extensions.ai.ai_audience_ops.simple_sql import validate_simple_sql
from aicrm_next.extensions.ai.ai_audience_ops.template_registry import TEMPLATES, get_template
from aicrm_next.extensions.ai.ai_audience_ops.template_service import (
    AudienceTemplateService,
    TEMPLATE_ERROR_CODES,
)


EXPECTED_TEMPLATE_KEYS = {
    "wecom_contact_registration",
    "questionnaire_choice_answers",
    "paid_order",
    "channel_entry",
    "radar_first_click_elapsed",
    "member_usage_status",
}


def _compiled_parameters() -> dict[str, dict[str, Any]]:
    owner = {"owner_scope": "specified", "owner_userids": ["owner_a"]}
    return {
        "wecom_contact_registration": {**owner, "contact_statuses": ["active"], "registration_status": "unregistered"},
        "questionnaire_choice_answers": {
            **owner,
            "questionnaire_id": 1,
            "questionnaire_title": "问卷",
            "conditions": [
                {"question_id": 11, "question_title": "题一", "option_ids": [101, 102], "option_texts": ["甲", "乙"]},
                {"question_id": 12, "question_title": "题二", "option_ids": [103], "option_texts": ["丙"]},
            ],
        },
        "paid_order": {
            **owner,
            "product_codes": ["product_a"],
            "product_names": ["商品A"],
            "paid_at_from": "2026-07-01T00:00:00+08:00",
            "paid_at_to": "2026-08-01T00:00:00+08:00",
            "require_active_wecom_contact": True,
        },
        "channel_entry": {
            **owner,
            "channel_codes": ["channel_a"],
            "channel_names": ["渠道A"],
            "entered_days_min": 3,
            "entered_days_max": 7,
            "require_active_wecom_contact": True,
        },
        "radar_first_click_elapsed": {
            **owner,
            "radar_ids": [1, 2],
            "radar_titles": ["雷达A", "雷达B"],
            "elapsed_min": 24,
            "elapsed_max": 72,
            "elapsed_unit": "hour",
        },
        "member_usage_status": {
            **owner,
            "service_period": "active",
            "registration_status": "registered",
            "usage_status": "unused",
            "membership_tiers": ["annual"],
            "membership_statuses": ["active"],
        },
    }


def test_template_registry_has_six_immutable_versioned_templates() -> None:
    assert {template.key for template in TEMPLATES} == EXPECTED_TEMPLATE_KEYS
    assert all(template.version == 1 for template in TEMPLATES)
    assert get_template("paid_order", 1) is not None
    assert get_template("paid_order", 2) is None


def test_all_template_compilers_emit_valid_parameterized_audience_read_sql() -> None:
    for template in TEMPLATES:
        sql, params = template.compiler(_compiled_parameters()[template.key])
        validation = validate_simple_sql(sql, params)
        assert validation.ok, (template.key, validation.errors)
        assert set(validation.dependencies) == set(template.dependencies)
        assert "owner_a" not in sql
        assert "SELECT *" not in sql.upper()
        assert all(dependency.startswith("audience_read.") for dependency in validation.dependencies)


def test_questionnaire_template_uses_first_complete_submission_and_and_or_contract() -> None:
    template = get_template("questionnaire_choice_answers")
    assert template is not None
    sql, params = template.compiler(_compiled_parameters()[template.key])

    assert "ROW_NUMBER() OVER" in sql
    assert "ORDER BY submitted_at ASC, submission_id ASC" in sql
    assert "submission_rank = 1" in sql
    assert sql.count("EXISTS (SELECT 1 FROM audience_read.questionnaire_answers_v1") == 2
    assert " OR " in sql
    assert " AND EXISTS" in sql
    assert params["option_id_json_0_0"] == "[101]"


def test_radar_template_anchors_first_click_and_uses_half_open_window() -> None:
    template = get_template("radar_first_click_elapsed")
    assert template is not None
    sql, _params = template.compiler(_compiled_parameters()[template.key])

    assert "MIN(clicks.clicked_at) AS first_clicked_at" in sql
    assert "ARRAY_AGG(clicks.owner_userid ORDER BY clicks.clicked_at ASC" in sql
    assert "first_clicked_at <=" in sql
    assert "first_clicked_at >" in sql
    assert "GROUP BY clicks.external_userid, clicks.radar_id" in sql


class _FakeTemplateRepository:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows if rows is not None else [{"external_userid": "wm_secret_1"}]
        self.applied: dict[str, Any] | None = None

    def resolve_template_reference(self, reference_type: str, value: Any, *, parent_id: int | None = None):
        del parent_id
        if reference_type == "product":
            return [{"id": 1, "code": "product_a", "title": "商品A"}]
        if reference_type == "group":
            return [{"id": 9, "title": str(value)}]
        return []

    def execute_readonly_query(self, sql: str, params: dict[str, Any], *, limit: int, timeout_seconds: int, serialization_lock_key=None):
        del serialization_lock_key
        assert "商品A" not in sql
        assert params["product_code_0"] == "product_a"
        assert limit == 10001
        assert timeout_seconds == 10
        return list(self.rows)

    def apply_template_package(self, payload: dict[str, Any]):
        self.applied = dict(payload)
        return {
            "package": {"id": 10, "status": "paused"},
            "version": {"id": 20},
            "created": True,
            "updated": False,
            "idempotent": False,
        }


def _paid_request() -> dict[str, Any]:
    return {
        "package_key": "prod_verify_paid_a",
        "name": "已支付商品A",
        "template_key": "paid_order",
        "parameters": {
            "products": ["商品A"],
            "paid_at_from": None,
            "paid_at_to": None,
            "owner_scope": "all",
            "owner_userids": [],
            "require_active_wecom_contact": False,
        },
        "senders": [],
        "allow_empty": False,
        "operator": "pytest",
    }


def test_template_preview_masks_samples_and_never_returns_sql() -> None:
    result = AudienceTemplateService(repository=_FakeTemplateRepository()).preview(_paid_request())

    assert result["ok"] is True
    assert result["matched_count"] == 1
    assert result["sample_rows"][0]["masked_identity"].startswith("sha256:")
    body = json.dumps(result, ensure_ascii=False).lower()
    assert "wm_secret_1" not in body
    assert "select " not in body


def test_template_apply_requires_explicit_empty_confirmation() -> None:
    repository = _FakeTemplateRepository(rows=[])
    service = AudienceTemplateService(repository=repository)

    rejected = service.apply(_paid_request())
    assert rejected["error"] == "empty_audience_requires_confirmation"
    assert repository.applied is None

    request = {**_paid_request(), "allow_empty": True}
    applied = service.apply(request)
    assert applied["ok"] is True
    assert applied["status"] == "paused"
    assert repository.applied is not None
    assert repository.applied["compiled_sql"]


def test_template_hybrid_refresh_mode_preserves_incremental_and_daily_flags() -> None:
    repository = _FakeTemplateRepository()
    request = {**_paid_request(), "refresh_mode": "every_3m_plus_daily_0200"}

    applied = AudienceTemplateService(repository=repository).apply(request)

    assert applied["ok"] is True
    assert repository.applied is not None
    assert repository.applied["refresh_config"]["incremental_enabled"] is True
    assert repository.applied["refresh_config"]["daily_enabled"] is True


def test_template_request_rejects_unknown_public_and_parameter_fields() -> None:
    service = AudienceTemplateService(repository=_FakeTemplateRepository())
    public_unknown = service.preview({**_paid_request(), "sql": "SELECT 1"})
    assert public_unknown["error"] == "invalid_request"

    request = _paid_request()
    request["parameters"] = {**request["parameters"], "custom_sql_fragment": "DROP TABLE users"}
    parameter_unknown = service.preview(request)
    assert parameter_unknown["error"] == "unknown_parameter"


def test_agent_configuration_guide_covers_registry_fields_enums_and_errors() -> None:
    guide = Path("docs/ai_audience/agent_package_configuration_guide.md").read_text(encoding="utf-8")
    for template in TEMPLATES:
        assert template.key in guide
        assert f"`{template.version}`" in guide or f"v{template.version}" in guide
        for field in template.fields:
            assert f"`{field['name']}`" in guide
            for value in field.get("enum", []):
                assert f"`{value}`" in guide
    for error_code in TEMPLATE_ERROR_CODES:
        assert f"`{error_code}`" in guide
