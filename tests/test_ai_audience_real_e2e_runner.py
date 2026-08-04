from __future__ import annotations

from aicrm_next.extensions.ai.ai_audience_ops.e2e_runner import (
    TEST_EXTERNAL_USERID,
    TEST_SENDER_USERID,
    AudienceRealE2ERunner,
    _scenario_spec_markdown,
    _private_job_guard,
    _skipped_count,
    _webhook_job_guard,
)
from aicrm_next.extensions.ai.ai_audience_ops.automation_binding.precheck import inspect_automation_bindings
from aicrm_next.extensions.ai.ai_audience_ops.service import AudiencePackageService
from aicrm_next.platform.shared.db_session import get_session_factory
from aicrm_next.extensions.ai.ai_audience_ops.package_spec import parse_markdown_spec_text, validate_spec
from aicrm_next.extensions.ai.ai_audience_ops.test_agent_service import AudienceTestAgentService
from aicrm_next.platform.platform_foundation.external_effects import WEBHOOK_GENERIC_PUSH, WECOM_MESSAGE_PRIVATE_SEND
from aicrm_next.platform.platform_foundation.external_effects.models import ExternalEffectJob
from sqlalchemy import text


def test_external_e2e_route_is_removed(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    monkeypatch.delenv("AICRM_AI_AUDIENCE_E2E_RUNNER_ENABLED", raising=False)

    missing = next_client.post("/api/external/ai-audience/e2e/run", json={})
    assert missing.status_code == 404

    disabled = next_client.post(
        "/api/external/ai-audience/e2e/run",
        headers={"Authorization": "Bearer retired-shared-token"},
        json={
            "run_id": "e2e_test",
            "external_userid": TEST_EXTERNAL_USERID,
            "sender_userid": TEST_SENDER_USERID,
            "confirm_real_send": True,
            "scenarios": ["questionnaire"],
        },
    )
    assert disabled.status_code == 404
    assert "retired-shared-token" not in disabled.text


def test_e2e_runner_hard_guards(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_AI_AUDIENCE_E2E_RUNNER_ENABLED", "true")
    runner = AudienceRealE2ERunner(
        repository=object(),
        package_service=object(),
        refresh_service=object(),
        outbound_service=object(),
        external_effects=object(),
        worker=object(),
        user_ops_gateway=object(),
    )

    base = {
        "run_id": "e2e_guard",
        "external_userid": TEST_EXTERNAL_USERID,
        "sender_userid": TEST_SENDER_USERID,
        "confirm_real_send": True,
        "scenarios": ["questionnaire"],
    }
    assert runner._guard({**base, "external_userid": "wm_other"})["error"] == "external_userid_not_allowed"
    assert runner._guard({**base, "sender_userid": "QianLan"})["error"] == "sender_userid_not_allowed"
    assert runner._guard({**base, "confirm_real_send": False})["error"] == "confirm_real_send_required"
    assert runner._guard({**base, "run_id": "prod_run"})["error"] == "run_id_must_start_with_e2e"
    assert runner._guard(base) is None


def test_e2e_generated_specs_are_linted_and_hard_filter_test_user() -> None:
    for scenario in ("questionnaire", "payment", "channel_entry", "user_ops_batch_send"):
        markdown = _scenario_spec_markdown(scenario, package_key=f"prod_e2e_{scenario}_e2e_test", run_id="e2e_test")
        spec = parse_markdown_spec_text(markdown)
        errors, warnings = validate_spec(spec)
        assert errors == []
        assert warnings == []
        assert "wc.external_userid = :test_external_userid" in spec.incremental_sql
        if scenario in {"questionnaire", "payment", "channel_entry"}:
            assert "WHEN :e2e_force_test_match THEN CAST(:refresh_started_at AS timestamptz)" in spec.incremental_sql
        if scenario == "questionnaire":
            assert "audience_read.questionnaire_submissions_v1" in spec.incremental_sql
        if scenario == "payment":
            assert "audience_read.orders_v1" in spec.incremental_sql
        if scenario == "channel_entry":
            assert "audience_read.channel_entries_v1" in spec.incremental_sql


def test_test_agent_accepts_run_level_external_userid_array(monkeypatch) -> None:
    package_key = "prod_e2e_questionnaire_added_wecom_auto_send_e2e_test"
    body = [TEST_EXTERNAL_USERID]

    class Repo:
        def get_package_by_key(self, value):
            assert value == package_key
            return {"id": 7, "package_key": package_key}

        def list_subscriptions(self, package_id, active_only=True, trigger_event_type="entered"):
            assert package_id == 7
            assert active_only is True
            assert trigger_event_type == "entered"
            return [{"id": 1}]

    class Inbound:
        def handle(self, package, payload, *, raw_body):
            assert package == package_key
            assert payload["action"]["target_external_userid"] == TEST_EXTERNAL_USERID
            assert payload["action"]["sender_userid"] == TEST_SENDER_USERID
            assert "run_id=123" in payload["message"]["text"]
            assert payload["external_event_id"] == f"self_agent_run:{package_key}:123:{TEST_EXTERNAL_USERID}"
            return {"ok": True, "external_effect_job_id": 99, "record_only": False}

    monkeypatch.setenv("AICRM_AI_AUDIENCE_TEST_AGENT_ENABLED", "true")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_TEST_AGENT_PACKAGE_KEYS", package_key)
    monkeypatch.setenv("AICRM_AI_AUDIENCE_TEST_AGENT_ALLOWED_EXTERNAL_USERIDS", TEST_EXTERNAL_USERID)
    monkeypatch.setenv("AICRM_AI_AUDIENCE_TEST_AGENT_SENDER_USERID", TEST_SENDER_USERID)

    result = AudienceTestAgentService(repository=Repo(), inbound_service=Inbound()).handle(
        body,
        headers={
            "X-AICRM-Package-Key": package_key,
            "X-AICRM-Event-Type": "audience.incremental.entered",
            "X-AICRM-Refresh-Run-Id": "123",
        },
    )

    assert result["ok"] is True
    assert result["external_effect_job_id"] == 99
    assert result["external_userid"] == TEST_EXTERNAL_USERID
    assert result["sender_userid"] == TEST_SENDER_USERID
    assert result["real_external_call_executed"] is False


def test_test_agent_allows_prod_e2e_package_when_runner_enabled(monkeypatch) -> None:
    package_key = "prod_e2e_questionnaire_added_wecom_auto_send_e2e_test"
    body = [TEST_EXTERNAL_USERID]

    class Repo:
        def get_package_by_key(self, value):
            assert value == package_key
            return {"id": 7, "package_key": package_key}

        def list_subscriptions(self, package_id, active_only=True, trigger_event_type="entered"):
            assert package_id == 7
            return [{"id": 1}]

    class Inbound:
        def handle(self, package, payload, *, raw_body):
            assert payload["action"]["target_external_userid"] == TEST_EXTERNAL_USERID
            return {"ok": True, "external_effect_job_id": 99, "record_only": False}

    monkeypatch.setenv("AICRM_AI_AUDIENCE_TEST_AGENT_ENABLED", "true")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_E2E_RUNNER_ENABLED", "true")
    monkeypatch.delenv("AICRM_AI_AUDIENCE_TEST_AGENT_PACKAGE_KEYS", raising=False)
    monkeypatch.setenv("AICRM_AI_AUDIENCE_TEST_AGENT_ALLOWED_EXTERNAL_USERIDS", TEST_EXTERNAL_USERID)
    monkeypatch.setenv("AICRM_AI_AUDIENCE_TEST_AGENT_SENDER_USERID", TEST_SENDER_USERID)

    result = AudienceTestAgentService(repository=Repo(), inbound_service=Inbound()).handle(
        body,
        headers={
            "X-AICRM-Package-Key": package_key,
            "X-AICRM-Event-Type": "audience.incremental.entered",
            "X-AICRM-Refresh-Run-Id": "123",
        },
    )

    assert result["ok"] is True
    assert result["external_effect_job_id"] == 99


def test_test_agent_rejects_non_test_array_member(monkeypatch) -> None:
    package_key = "prod_e2e_questionnaire_added_wecom_auto_send_e2e_test"
    monkeypatch.setenv("AICRM_AI_AUDIENCE_TEST_AGENT_ENABLED", "true")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_TEST_AGENT_PACKAGE_KEYS", package_key)
    monkeypatch.setenv("AICRM_AI_AUDIENCE_TEST_AGENT_ALLOWED_EXTERNAL_USERIDS", TEST_EXTERNAL_USERID)

    result = AudienceTestAgentService(repository=object(), inbound_service=object()).handle(
        ["wm_other"],
        headers={
            "X-AICRM-Package-Key": package_key,
            "X-AICRM-Event-Type": "audience.incremental.entered",
            "X-AICRM-Refresh-Run-Id": "123",
        },
    )

    assert result["ok"] is False
    assert result["error"] == "external_userid_not_allowed"


def test_e2e_job_guards_require_array_body_and_exact_private_target() -> None:
    webhook = ExternalEffectJob(
        id=1,
        effect_type=WEBHOOK_GENERIC_PUSH,
        business_type="ai_audience_package_run",
        business_id="123",
        payload_json={"body": [TEST_EXTERNAL_USERID], "is_test": True, "execution_scope": "test_loopback"},
    )
    assert _webhook_job_guard(webhook, 123) == ""
    leaked = ExternalEffectJob(
        id=2,
        effect_type=WEBHOOK_GENERIC_PUSH,
        business_type="ai_audience_package_run",
        business_id="123",
        payload_json={"body": {"package_key": "prod_e2e", "members": [TEST_EXTERNAL_USERID]}},
    )
    assert _webhook_job_guard(leaked, 123) == "webhook_body_not_external_userid_array"

    private = ExternalEffectJob(
        id=3,
        effect_type=WECOM_MESSAGE_PRIVATE_SEND,
        payload_json={"external_userids": [TEST_EXTERNAL_USERID], "owner_userid": TEST_SENDER_USERID},
    )
    assert _private_job_guard(private) == ""
    wrong_sender = ExternalEffectJob(
        id=4,
        effect_type=WECOM_MESSAGE_PRIVATE_SEND,
        payload_json={"external_userids": [TEST_EXTERNAL_USERID], "owner_userid": "QianLan"},
    )
    assert _private_job_guard(wrong_sender) == "private_sender_not_allowed"


def test_e2e_skipped_count_accepts_user_ops_summary_formats() -> None:
    assert _skipped_count({"no_allowed_sender": 2}, "no_allowed_sender") == 2
    assert (
        _skipped_count(
            [{"reason": "do_not_disturb", "count": 1}, {"reason": "no_allowed_sender", "reason_label": "无可用发送人", "count": 3}],
            "no_allowed_sender",
        )
        == 3
    )
    assert _skipped_count([], "no_allowed_sender") == 0


def test_archiving_e2e_package_archives_subscriptions_and_keeps_binding_precheck_clean(next_pg_schema) -> None:
    del next_pg_schema
    session_factory = get_session_factory()
    with session_factory() as session:
        package_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO ai_audience_package (package_key, name, status, created_at, updated_at)
                    VALUES ('prod_e2e_cleanup_test', 'E2E cleanup test', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    RETURNING id
                    """
                )
            ).scalar_one()
        )
        subscription_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO ai_audience_outbound_subscription (package_id, status, webhook_url)
                    VALUES (:package_id, 'active', 'https://www.youcangogogo.com/api/ai/audience/test-agent/webhook')
                    RETURNING id
                    """
                ),
                {"package_id": package_id},
            ).scalar_one()
        )
        session.commit()

    result = AudiencePackageService().archive_admin_package(package_id)

    assert result["ok"] is True
    with session_factory() as session:
        states = (
            session.execute(
                text(
                    """
                SELECT p.status AS package_status, s.status AS subscription_status
                FROM ai_audience_package p
                JOIN ai_audience_outbound_subscription s ON s.package_id = p.id
                WHERE p.id = :package_id AND s.id = :subscription_id
                """
                ),
                {"package_id": package_id, "subscription_id": subscription_id},
            )
            .mappings()
            .one()
        )
        report = inspect_automation_bindings(session.connection())
    assert dict(states) == {"package_status": "archived", "subscription_status": "archived"}
    assert report.ok is True
