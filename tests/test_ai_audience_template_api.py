from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from aicrm_next.extensions.ai.ai_audience_ops.automation_binding.repository import AutomationNotFoundError
from aicrm_next.extensions.ai.ai_audience_ops.repository import build_audience_repository
from aicrm_next.extensions.ai.ai_audience_ops.template_registry import TEMPLATES
from aicrm_next.platform.shared.db_session import get_session_factory
from tests.admin_auth_test_helpers import access_token_headers, admin_session_cookies, install_access_token


def _external_headers(next_client) -> dict[str, str]:
    token = install_access_token(
        next_client,
        audience="external_integration",
        capabilities=("external_write",),
        scopes=("write",),
        client_id="pytest-audience-template-agent",
        purpose="external_agent",
    )
    return access_token_headers(token)


def _ready(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_SPEC_ALLOWED_PREFIXES", "prod_verify_,official_")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_SPEC_ALLOW_NON_VERIFY_PREFIX", "false")
    monkeypatch.setenv("AICRM_AUDIENCE_READONLY_DATABASE_URL", os.environ["DATABASE_URL"])


def _request(package_key: str = "prod_verify_template_contacts") -> dict:
    return {
        "package_key": package_key,
        "name": "模板联系人测试",
        "template_key": "wecom_contact_registration",
        "parameters": {
            "owner_scope": "all",
            "owner_userids": [],
            "contact_statuses": ["active"],
            "registration_status": "any",
        },
        "refresh_mode": "manual",
        "senders": [],
        "allow_empty": True,
        "operator": "pytest",
    }


def test_external_template_routes_require_access_token(next_client, monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    response = next_client.post("/api/external/ai-audience/templates/preview", json=_request())
    assert response.status_code == 401
    assert response.json()["error"] == "access_token_required"


def test_template_preview_is_read_only_and_does_not_return_sql(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready(monkeypatch)
    response = next_client.post(
        "/api/external/ai-audience/templates/preview",
        headers=_external_headers(next_client),
        json=_request(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["template_key"] == "wecom_contact_registration"
    assert body["matched_count"] == 0
    assert "empty_audience" in body["risk_warnings"]
    assert "sql" not in json.dumps(body, ensure_ascii=False).lower()
    with get_session_factory()() as session:
        count = session.execute(
            text("SELECT COUNT(*) FROM ai_audience_package WHERE package_key = :key"),
            {"key": _request()["package_key"]},
        ).scalar_one()
    assert count == 0


def test_template_apply_creates_paused_package_and_is_idempotent(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready(monkeypatch)
    headers = _external_headers(next_client)
    first = next_client.post("/api/external/ai-audience/templates/apply", headers=headers, json=_request())
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["ok"] is True
    assert body["status"] == "paused"
    assert body["created"] is True
    assert body["published"] is True
    version_id = body["version_id"]

    second = next_client.post("/api/external/ai-audience/templates/apply", headers=headers, json=_request())
    assert second.status_code == 200, second.text
    assert second.json()["version_id"] == version_id
    assert second.json()["idempotent"] is True

    with get_session_factory()() as session:
        package = session.execute(
            text(
                """
                SELECT status, next_incremental_refresh_at, next_daily_refresh_at, current_version_id
                FROM ai_audience_package WHERE package_key = :key
                """
            ),
            {"key": _request()["package_key"]},
        ).mappings().one()
        version = session.execute(
            text(
                """
                SELECT template_key, template_version, template_params_json, template_fingerprint
                FROM ai_audience_package_version WHERE id = :id
                """
            ),
            {"id": version_id},
        ).mappings().one()
        version_count = session.execute(
            text("SELECT COUNT(*) FROM ai_audience_package_version WHERE package_id = :id"),
            {"id": body["package_id"]},
        ).scalar_one()
        run_count = session.execute(text("SELECT COUNT(*) FROM ai_audience_package_run WHERE package_id = :id"), {"id": body["package_id"]}).scalar_one()
        event_count = session.execute(text("SELECT COUNT(*) FROM ai_audience_member_event WHERE package_id = :id"), {"id": body["package_id"]}).scalar_one()
    assert package["status"] == "paused"
    assert package["next_incremental_refresh_at"] is None
    assert package["next_daily_refresh_at"] is None
    assert package["current_version_id"] == version_id
    assert version["template_key"] == "wecom_contact_registration"
    assert version["template_version"] == 1
    assert version["template_params_json"]["owner_scope"] == "all"
    assert version["template_fingerprint"]
    assert version_count == 1
    assert run_count == 0
    assert event_count == 0


def test_template_apply_requires_empty_confirmation(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready(monkeypatch)
    request = {**_request("prod_verify_empty_rejected"), "allow_empty": False}
    response = next_client.post(
        "/api/external/ai-audience/templates/apply",
        headers=_external_headers(next_client),
        json=request,
    )
    assert response.status_code == 409
    assert response.json()["error"] == "empty_audience_requires_confirmation"
    with get_session_factory()() as session:
        count = session.execute(text("SELECT COUNT(*) FROM ai_audience_package WHERE package_key = :key"), {"key": request["package_key"]}).scalar_one()
    assert count == 0


def test_template_active_package_rejects_changed_rule_but_allows_exact_idempotency(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready(monkeypatch)
    headers = _external_headers(next_client)
    request = _request("prod_verify_active_template")
    created = next_client.post("/api/external/ai-audience/templates/apply", headers=headers, json=request).json()
    with get_session_factory()() as session:
        session.execute(text("UPDATE ai_audience_package SET status = 'active' WHERE id = :id"), {"id": created["package_id"]})
        session.commit()

    exact = next_client.post("/api/external/ai-audience/templates/apply", headers=headers, json=request)
    assert exact.status_code == 200
    assert exact.json()["idempotent"] is True

    changed = _request("prod_verify_active_template")
    changed["parameters"] = {**changed["parameters"], "registration_status": "unregistered"}
    response = next_client.post("/api/external/ai-audience/templates/apply", headers=headers, json=changed)
    assert response.status_code == 409
    assert response.json()["error"] == "active_package_update_requires_pause"


def test_template_reference_ambiguity_returns_candidates_and_creates_nothing(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready(monkeypatch)
    with get_session_factory()() as session:
        session.execute(
            text(
                """
                INSERT INTO wechat_pay_products (product_code, name, status, enabled)
                VALUES ('ambiguous_a', '同名商品', 'active', TRUE), ('ambiguous_b', '同名商品', 'active', TRUE)
                """
            )
        )
        session.commit()
    request = {
        "package_key": "prod_verify_ambiguous_product",
        "name": "重名商品",
        "template_key": "paid_order",
        "parameters": {
            "products": ["同名商品"],
            "owner_scope": "all",
            "owner_userids": [],
            "require_active_wecom_contact": False,
        },
        "allow_empty": True,
    }
    response = next_client.post(
        "/api/external/ai-audience/templates/apply",
        headers=_external_headers(next_client),
        json=request,
    )
    assert response.status_code == 409
    assert response.json()["error"] == "reference_ambiguous"
    assert len(response.json()["candidates"]) == 2
    with get_session_factory()() as session:
        count = session.execute(text("SELECT COUNT(*) FROM ai_audience_package WHERE package_key = :key"), {"key": request["package_key"]}).scalar_one()
    assert count == 0


def test_admin_template_catalog_and_active_detail_are_next_native(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    monkeypatch.setenv("AICRM_ADMIN_AUTH_ENFORCED", "true")
    response = next_client.get("/api/admin/ai-audience/templates", cookies=admin_session_cookies(next_client, "super_admin"))
    assert response.status_code == 200
    assert {item["template_key"] for item in response.json()["templates"]} == {
        "wecom_contact_registration",
        "questionnaire_choice_answers",
        "paid_order",
        "channel_entry",
        "radar_first_click_elapsed",
        "member_usage_status",
    }
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert "sql" not in response.text.lower()


def test_all_six_template_compilers_execute_on_postgres_read_views(next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready(monkeypatch)
    owner = {"owner_scope": "specified", "owner_userids": ["owner_a"]}
    parameters = {
        "wecom_contact_registration": {
            **owner,
            "contact_statuses": ["active"],
            "registration_status": "any",
        },
        "questionnaire_choice_answers": {
            **owner,
            "questionnaire_id": -1,
            "questionnaire_title": "不存在的问卷",
            "conditions": [
                {
                    "question_id": -1,
                    "question_title": "不存在的题目",
                    "option_ids": [-1],
                    "option_texts": ["不存在的选项"],
                }
            ],
        },
        "paid_order": {
            **owner,
            "product_codes": ["missing_product"],
            "product_names": ["不存在的商品"],
            "paid_at_from": None,
            "paid_at_to": None,
            "require_active_wecom_contact": True,
        },
        "channel_entry": {
            **owner,
            "channel_codes": ["missing_channel"],
            "channel_names": ["不存在的渠道"],
            "entered_days_min": 1,
            "entered_days_max": 7,
            "require_active_wecom_contact": True,
        },
        "radar_first_click_elapsed": {
            **owner,
            "radar_ids": [-1],
            "radar_titles": ["不存在的雷达"],
            "elapsed_min": 1,
            "elapsed_max": 7,
            "elapsed_unit": "day",
        },
        "member_usage_status": {
            **owner,
            "service_period": "active",
            "registration_status": "registered",
            "usage_status": "unused",
            "membership_tiers": [],
            "membership_statuses": [],
        },
    }
    repository = build_audience_repository()
    for template in TEMPLATES:
        sql, bound = template.compiler(parameters[template.key])
        rows = repository.execute_readonly_query(
            sql,
            {
                **bound,
                "package_key": "prod_verify_compiler_contract",
                "package_id": 0,
                "refresh_started_at": "2026-08-01T00:00:00+08:00",
                "last_watermark_at": "1970-01-01T00:00:00+00:00",
                "lookback_seconds": 600,
            },
            limit=1,
            timeout_seconds=10,
        )
        assert isinstance(rows, list), template.key


def test_questionnaire_template_uses_first_submission_question_and_option_boolean_contract(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready(monkeypatch)
    with get_session_factory()() as session:
        questionnaire_id = session.execute(
            text("INSERT INTO questionnaires (slug, name, title) VALUES ('first-answer-test', '首次答案', '首次答案') RETURNING id")
        ).scalar_one()
        question_one = session.execute(
            text(
                "INSERT INTO questionnaire_questions (questionnaire_id, type, title, sort_order) VALUES (:id, 'multi_choice', '题一', 1) RETURNING id"
            ),
            {"id": questionnaire_id},
        ).scalar_one()
        question_two = session.execute(
            text(
                "INSERT INTO questionnaire_questions (questionnaire_id, type, title, sort_order) VALUES (:id, 'single_choice', '题二', 2) RETURNING id"
            ),
            {"id": questionnaire_id},
        ).scalar_one()
        option_a = session.execute(text("INSERT INTO questionnaire_options (question_id, option_text) VALUES (:id, 'A') RETURNING id"), {"id": question_one}).scalar_one()
        option_b = session.execute(text("INSERT INTO questionnaire_options (question_id, option_text) VALUES (:id, 'B') RETURNING id"), {"id": question_one}).scalar_one()
        option_c = session.execute(text("INSERT INTO questionnaire_options (question_id, option_text) VALUES (:id, 'C') RETURNING id"), {"id": question_two}).scalar_one()
        option_d = session.execute(text("INSERT INTO questionnaire_options (question_id, option_text) VALUES (:id, 'D') RETURNING id"), {"id": question_two}).scalar_one()
        for suffix in ("match", "later_only", "missing_question"):
            session.execute(
                text(
                    """
                    INSERT INTO crm_user_identity (
                        unionid, primary_external_userid, external_userids_json,
                        primary_owner_userid, identity_status, created_at, updated_at
                    )
                    VALUES (
                        :unionid, :external_userid, jsonb_build_array(CAST(:external_userid AS text)),
                        'owner_a', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {"unionid": f"union_{suffix}", "external_userid": f"wm_{suffix}"},
            )

        first_match = session.execute(
            text(
                """
                INSERT INTO questionnaire_submissions (questionnaire_id, unionid, follow_user_userid, submitted_at)
                VALUES (:qid, 'union_match', 'owner_a', CURRENT_TIMESTAMP - interval '3 days') RETURNING id
                """
            ),
            {"qid": questionnaire_id},
        ).scalar_one()
        first_later_only = session.execute(
            text(
                """
                INSERT INTO questionnaire_submissions (questionnaire_id, unionid, follow_user_userid, submitted_at)
                VALUES (:qid, 'union_later_only', 'owner_a', CURRENT_TIMESTAMP - interval '3 days') RETURNING id
                """
            ),
            {"qid": questionnaire_id},
        ).scalar_one()
        second_later_only = session.execute(
            text(
                """
                INSERT INTO questionnaire_submissions (questionnaire_id, unionid, follow_user_userid, submitted_at)
                VALUES (:qid, 'union_later_only', 'owner_a', CURRENT_TIMESTAMP - interval '1 day') RETURNING id
                """
            ),
            {"qid": questionnaire_id},
        ).scalar_one()
        missing_question = session.execute(
            text(
                """
                INSERT INTO questionnaire_submissions (questionnaire_id, unionid, follow_user_userid, submitted_at)
                VALUES (:qid, 'union_missing_question', 'owner_a', CURRENT_TIMESTAMP - interval '2 days') RETURNING id
                """
            ),
            {"qid": questionnaire_id},
        ).scalar_one()

        def answer(submission_id: int, question_id: int, question_type: str, option_ids: list[int]) -> None:
            session.execute(
                text(
                    """
                    INSERT INTO questionnaire_submission_answers (
                        submission_id, question_id, question_type, question_title_snapshot,
                        selected_option_ids, selected_option_texts_snapshot
                    )
                    VALUES (
                        :submission_id, :question_id, :question_type, 'snapshot',
                        CAST(:option_ids AS jsonb), '[]'::jsonb
                    )
                    """
                ),
                {
                    "submission_id": submission_id,
                    "question_id": question_id,
                    "question_type": question_type,
                    "option_ids": json.dumps(option_ids),
                },
            )

        answer(first_match, question_one, "multi_choice", [option_b])
        answer(first_match, question_two, "single_choice", [option_c])
        answer(first_later_only, question_one, "multi_choice", [option_a])
        answer(first_later_only, question_two, "single_choice", [option_d])
        answer(second_later_only, question_one, "multi_choice", [option_b])
        answer(second_later_only, question_two, "single_choice", [option_c])
        answer(missing_question, question_one, "multi_choice", [option_b])
        session.commit()

    request = {
        "package_key": "prod_verify_questionnaire_boolean",
        "name": "问卷布尔规则",
        "template_key": "questionnaire_choice_answers",
        "parameters": {
            "questionnaire": questionnaire_id,
            "conditions": [
                {"question": question_one, "options": [option_a, option_b]},
                {"question": question_two, "options": [option_c]},
            ],
            "owner_scope": "all",
            "owner_userids": [],
        },
    }
    response = next_client.post(
        "/api/external/ai-audience/templates/preview",
        headers=_external_headers(next_client),
        json=request,
    )
    assert response.status_code == 200, response.text
    assert response.json()["matched_count"] == 1


def test_radar_template_keeps_first_attributable_click_as_anchor(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready(monkeypatch)
    suffix_token = uuid4().hex[:10]
    radar_code = f"first-radar-{suffix_token}"
    first_unionid = f"union_first_old_{suffix_token}"
    recent_unionid = f"union_recent_only_{suffix_token}"
    with get_session_factory()() as session:
        radar_id = session.execute(
            text("INSERT INTO radar_links (code, title, target_type) VALUES (:code, :title, 'link') RETURNING id"),
            {"code": radar_code, "title": f"首次雷达{suffix_token}"},
        ).scalar_one()
        for suffix, unionid in (("first_old", first_unionid), ("recent_only", recent_unionid)):
            session.execute(
                text(
                    """
                    INSERT INTO crm_user_identity (
                        unionid, primary_external_userid, external_userids_json,
                        primary_owner_userid, identity_status, created_at, updated_at
                    )
                    VALUES (
                        :unionid, :external_userid, jsonb_build_array(CAST(:external_userid AS text)),
                        'owner_a', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {"unionid": unionid, "external_userid": f"wm_{suffix}_{suffix_token}"},
            )
        session.execute(
            text(
                """
                INSERT INTO radar_click_events (link_id, code, stage, unionid, created_at)
                VALUES
                  (:radar_id, :code, 'authorized', :first_unionid, CURRENT_TIMESTAMP - interval '48 hours'),
                  (:radar_id, :code, 'authorized', :first_unionid, CURRENT_TIMESTAMP - interval '1 hour'),
                  (:radar_id, :code, 'authorized', :recent_unionid, CURRENT_TIMESTAMP - interval '1 hour')
                """
            ),
            {"radar_id": radar_id, "code": radar_code, "first_unionid": first_unionid, "recent_unionid": recent_unionid},
        )
        session.commit()
    request = {
        "package_key": "prod_verify_radar_first_click",
        "name": "雷达首次点击",
        "template_key": "radar_first_click_elapsed",
        "parameters": {
            "radars": [radar_id],
            "elapsed_min": 24,
            "elapsed_max": 72,
            "elapsed_unit": "hour",
            "owner_scope": "all",
            "owner_userids": [],
        },
    }
    response = next_client.post(
        "/api/external/ai-audience/templates/preview",
        headers=_external_headers(next_client),
        json=request,
    )
    assert response.status_code == 200, response.text
    assert response.json()["matched_count"] == 1


def test_template_repository_rolls_back_package_version_and_senders_when_binding_fails(next_pg_schema) -> None:
    del next_pg_schema
    package_key = "prod_verify_template_atomic_rollback"
    repository = build_audience_repository()
    with pytest.raises(AutomationNotFoundError):
        repository.apply_template_package(
            {
                "package_key": package_key,
                "name": "事务回滚验证",
                "template_key": "wecom_contact_registration",
                "template_version": 1,
                "template_parameters": {
                    "owner_scope": "all",
                    "owner_userids": [],
                    "contact_statuses": ["active"],
                    "registration_status": "any",
                },
                "template_fingerprint": "f" * 64,
                "natural_language_definition": "事务回滚验证",
                "compiled_sql": "SELECT 'external_userid' AS identity_type, external_userid AS identity_value, 'x' AS event_source_key, '{}'::jsonb AS payload_json, external_userid, CURRENT_TIMESTAMP AS event_at FROM audience_read.wecom_contacts_v1",
                "execution_parameters": {},
                "dependencies": ["audience_read.wecom_contacts_v1"],
                "refresh_mode": "manual",
                "refresh_config": {
                    "incremental_enabled": False,
                    "incremental_interval_seconds": 180,
                    "daily_enabled": False,
                    "daily_refresh_time": "02:00",
                },
                "senders": [
                    {
                        "sender_userid": "sender_atomic",
                        "display_name": "事务发送人",
                        "priority": 1,
                        "status": "active",
                    }
                ],
                "automation_agent_code": "missing_automation_for_rollback",
                "operator": "pytest",
            }
        )
    with get_session_factory()() as session:
        package_count = session.execute(
            text("SELECT COUNT(*) FROM ai_audience_package WHERE package_key = :key"),
            {"key": package_key},
        ).scalar_one()
        version_count = session.execute(
            text(
                """
                SELECT COUNT(*)
                FROM ai_audience_package_version version
                JOIN ai_audience_package package ON package.id = version.package_id
                WHERE package.package_key = :key
                """
            ),
            {"key": package_key},
        ).scalar_one()
        sender_count = session.execute(
            text(
                """
                SELECT COUNT(*)
                FROM ai_audience_package_sender sender
                JOIN ai_audience_package package ON package.id = sender.package_id
                WHERE package.package_key = :key
                """
            ),
            {"key": package_key},
        ).scalar_one()
    assert package_count == 0
    assert version_count == 0
    assert sender_count == 0
