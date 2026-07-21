from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.platform_foundation.command_bus import CommandContext
from aicrm_next.platform_foundation.external_effects import (
    ExternalEffectService,
    WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
    reset_external_effect_fixture_state,
)
from aicrm_next.platform_foundation.external_effects.repo import build_external_effect_repository
from tests.admin_auth_test_helpers import install_admin_action_tokens


RUN_DUE_ROUTE = "/api/admin/external-effects/run-due"
PREVIEW_DUE_ROUTE = "/api/admin/external-effects/run-due/preview"
RETRY_JOB_ROUTE = "/api/admin/external-effects/jobs/{job_id}/retry"
CANCEL_JOB_ROUTE = "/api/admin/external-effects/jobs/{job_id}/cancel"


RAW_MOBILE = "13800138000"
RAW_EXTERNAL_USERID = "wmbNXyCwAADDlsLthbXRPgVw3bhDfgdw"
RAW_WEBHOOK_URL = "https://hooks.example.com/aicrm?token=webhook-token-secret"
RAW_WEBHOOK_TOKEN = "webhook-token-secret"
RAW_PROVIDER_TRANSACTION_ID = "4200003177202606155609674404"
RAW_OPENID = "openid_sensitive_fixture"
RAW_UNIONID = "unionid_sensitive_fixture"
RAW_SECRET = "super-secret-value"
RAW_AUTHORIZATION = "Bearer authorization-secret"
RECEIPT_EVENT_ID = "event-admin-redaction-0001"


SENSITIVE_LITERALS = [
    RAW_MOBILE,
    RAW_EXTERNAL_USERID,
    RAW_WEBHOOK_URL,
    RAW_WEBHOOK_TOKEN,
    RAW_PROVIDER_TRANSACTION_ID,
    RAW_OPENID,
    RAW_UNIONID,
    RAW_SECRET,
    RAW_AUTHORIZATION,
    "authorization-secret",
]


def _assert_no_sensitive_literals(text: str) -> None:
    for literal in SENSITIVE_LITERALS:
        assert literal not in text


def _seed_sensitive_external_effect(*, status: str = "blocked") -> int:
    reset_external_effect_fixture_state()
    service = ExternalEffectService()
    context = CommandContext(
        actor_id=RAW_EXTERNAL_USERID,
        actor_type="system",
        request_id=f"req-{RAW_MOBILE}",
        trace_id=f"trace-{RAW_PROVIDER_TRANSACTION_ID}",
        source_route="/tests/external-effects/admin-redaction",
    )
    created = service.plan_effect(
        effect_type=WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
        adapter_name="outbound_webhook",
        operation="post",
        target_type="questionnaire_submission",
        target_id=RAW_EXTERNAL_USERID,
        business_type="questionnaire",
        business_id=RAW_MOBILE,
        payload={
            "webhook_url": RAW_WEBHOOK_URL,
            "webhook_token": RAW_WEBHOOK_TOKEN,
            "mobile": RAW_MOBILE,
            "external_userid": RAW_EXTERNAL_USERID,
            "openid": RAW_OPENID,
            "unionid": RAW_UNIONID,
            "secret": RAW_SECRET,
            "authorization": RAW_AUTHORIZATION,
            "provider_transaction_id": RAW_PROVIDER_TRANSACTION_ID,
            "body": {
                "answers": [RAW_MOBILE, RAW_EXTERNAL_USERID],
                "buyer": {
                    "phone": RAW_MOBILE,
                    "openid": RAW_OPENID,
                    "unionid": RAW_UNIONID,
                },
                "transaction": {"transaction_id": RAW_PROVIDER_TRANSACTION_ID},
            },
        },
        payload_summary={
            "webhook_url": RAW_WEBHOOK_URL,
            "mobile": RAW_MOBILE,
            "external_userid": RAW_EXTERNAL_USERID,
            "provider_transaction_id": RAW_PROVIDER_TRANSACTION_ID,
            "token": RAW_WEBHOOK_TOKEN,
        },
        context=context,
        source_module="tests.external_effects_admin_redaction",
        source_command_id=f"cmd-{RAW_EXTERNAL_USERID}",
        idempotency_key=f"admin-redaction:{RAW_PROVIDER_TRANSACTION_ID}",
        status=status,
    )
    repo = build_external_effect_repository()
    job = repo.get_job(int(created["id"]))
    assert job is not None
    repo.record_attempt(
        job=job,
        status="blocked",
        adapter_mode="disabled",
        request_summary={
            "endpoint": RAW_WEBHOOK_URL,
            "Authorization": RAW_AUTHORIZATION,
            "external_userid": RAW_EXTERNAL_USERID,
            "mobile": RAW_MOBILE,
            "transaction_id": RAW_PROVIDER_TRANSACTION_ID,
        },
        response_summary={
            "body": f"received from {RAW_OPENID} / {RAW_UNIONID}",
            "access_token": RAW_WEBHOOK_TOKEN,
            "provider_transaction_id": RAW_PROVIDER_TRANSACTION_ID,
        },
        error_code="blocked_by_policy",
        error_message=f"blocked call to {RAW_WEBHOOK_URL} for {RAW_MOBILE}",
    )
    repo.create_test_receipt(
        event_id=RECEIPT_EVENT_ID,
        job=job,
        request_method="POST",
        request_path="/api/external-effects/test-receiver",
        headers_summary={"authorization": RAW_AUTHORIZATION},
        payload_summary={"mobile": RAW_MOBILE, "external_userid": RAW_EXTERNAL_USERID},
        payload_hash="safe-payload-hash",
        body_json={
            "webhook_url": RAW_WEBHOOK_URL,
            "mobile": RAW_MOBILE,
            "external_userid": RAW_EXTERNAL_USERID,
            "openid": RAW_OPENID,
            "unionid": RAW_UNIONID,
            "transaction_id": RAW_PROVIDER_TRANSACTION_ID,
            "secret": RAW_SECRET,
        },
        signature_valid=False,
        response_status=200,
    )
    return int(created["id"])


def test_external_effect_admin_jobs_list_detail_attempts_receipts_and_diagnostics_are_redacted(next_client: TestClient) -> None:
    job_id = _seed_sensitive_external_effect()

    listed = next_client.get(
        "/api/admin/external-effects/jobs",
        params={
            "effect_type": WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
            "target_id": RAW_EXTERNAL_USERID,
            "job_id": job_id,
        },
    )
    detail = next_client.get(f"/api/admin/external-effects/jobs/{job_id}")
    troubleshooting = next_client.get(
        "/api/admin/external-effects/troubleshooting/jobs",
        params={"problem_only": "false", "target_id": RAW_EXTERNAL_USERID},
    )
    troubleshooting_detail = next_client.get(f"/api/admin/external-effects/troubleshooting/jobs/{job_id}")
    receipts = next_client.get("/api/admin/external-effects/test-receipts", params={"event_id": RECEIPT_EVENT_ID})
    receipt_id = receipts.json()["items"][0]["receipt_id"]
    receipt_detail = next_client.get(f"/api/admin/external-effects/test-receipts/{receipt_id}")
    diagnostics = next_client.get("/api/admin/external-effects/diagnostics", params={"target_id": RAW_EXTERNAL_USERID})

    for response in (listed, detail, troubleshooting, troubleshooting_detail, receipts, receipt_detail, diagnostics):
        assert response.status_code == 200
        _assert_no_sensitive_literals(response.text)

    listed_body = listed.json()
    assert "payload_json" not in listed_body["items"][0]
    assert "payload_json" not in listed_body["selected_job"]
    assert "lease_token" not in listed_body["items"][0]
    assert "lease_token" not in listed_body["selected_job"]
    assert listed_body["items"][0]["payload_json_redacted"] is True
    assert listed_body["items"][0]["side_effect_executed"] is False
    assert listed_body["items"][0]["provider_result_received"] is False
    assert listed_body["items"][0]["reconciliation_required"] is False
    assert listed_body["items"][0]["result_summary_json"] == {}
    assert listed_body["items"][0]["payload_summary_json"]["mobile"] == "[redacted]"
    assert listed_body["attempts"][0]["request_summary_json"]["endpoint"] == "[redacted]"
    assert listed_body["attempts"][0]["response_summary_json"]["body"] == "[redacted]"

    detail_body = detail.json()
    assert detail_body["job"]["payload_json"]["mobile"] == "[redacted]"
    assert detail_body["job"]["payload_json"]["webhook_url"] == "[redacted]"
    assert detail_body["job"]["payload_json"]["body"]["buyer"]["openid"] == "[redacted]"
    assert detail_body["job"]["payload_json"]["provider_transaction_id"] == "[redacted]"
    assert detail_body["job"]["target_id"] == "[pii]"
    assert detail_body["job"]["business_id"] == "[pii]"
    assert detail_body["attempts"][0]["error_message"] == "[redacted]"

    troubleshooting_body = troubleshooting.json()
    assert "payload_json" not in troubleshooting_body["items"][0]
    assert troubleshooting_detail.json()["attempts"][0]["request_summary_json"]["Authorization"] == "[redacted]"
    assert receipts.json()["items"][0]["event_id"] == RECEIPT_EVENT_ID
    assert receipts.json()["items"][0]["body_json"]["external_userid"] == "[redacted]"
    assert receipt_detail.json()["receipt"]["body_json"]["transaction_id"] == "[redacted]"
    assert diagnostics.json()["filters"]["target_id"] == "[pii]"


def test_external_effect_retry_and_cancel_validation_responses_are_redacted(next_client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    tokens = install_admin_action_tokens(
        next_client,
        ("POST", RETRY_JOB_ROUTE),
        ("POST", CANCEL_JOB_ROUTE),
    )
    job_id = _seed_sensitive_external_effect()

    retried = next_client.post(
        f"/api/admin/external-effects/jobs/{job_id}/retry",
        json={"admin_action_token": tokens[("POST", RETRY_JOB_ROUTE)]},
    )
    cancelled = next_client.post(
        f"/api/admin/external-effects/jobs/{job_id}/cancel",
        json={"admin_action_token": tokens[("POST", CANCEL_JOB_ROUTE)]},
    )

    assert retried.status_code == 422
    assert cancelled.status_code == 422
    _assert_no_sensitive_literals(retried.text)
    _assert_no_sensitive_literals(cancelled.text)
    assert set(retried.json()["missing_fields"]) == {"actor", "reason", "expected_version"}
    assert set(cancelled.json()["missing_fields"]) == {"actor", "reason", "expected_version"}


def test_external_effect_run_due_preview_and_dry_run_responses_are_redacted(next_client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    tokens = install_admin_action_tokens(
        next_client,
        ("POST", PREVIEW_DUE_ROUTE),
        ("POST", RUN_DUE_ROUTE),
    )
    _seed_sensitive_external_effect(status="queued")

    preview = next_client.post(
        "/api/admin/external-effects/run-due/preview",
        headers={"X-Admin-Action-Token": tokens[("POST", PREVIEW_DUE_ROUTE)]},
        json={"batch_size": 1, "effect_types": [WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH]},
    )
    dry_run = next_client.post(
        "/api/admin/external-effects/run-due",
        headers={"X-Admin-Action-Token": tokens[("POST", RUN_DUE_ROUTE)]},
        json={"batch_size": 1, "dry_run": True, "effect_types": [WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH]},
    )

    for response in (preview, dry_run):
        assert response.status_code == 200
        _assert_no_sensitive_literals(response.text)
        body = response.json()
        assert body["dry_run"] is True
        assert body["real_external_call_executed"] is False
        assert body["items"][0]["payload_json"]["mobile"] == "[redacted]"
        assert body["items"][0]["payload_json"]["webhook_url"] == "[redacted]"
        assert body["items"][0]["target_id"] == "[pii]"
