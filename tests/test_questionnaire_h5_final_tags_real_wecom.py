from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("composed_internal_event_registry")
from fastapi.testclient import TestClient

from aicrm_next.customer_tags.local_projection import (
    get_customer_tag_local_projection_fixture_rows,
    reset_customer_tag_local_projection_fixture_state,
)
from aicrm_next.external_effect_composition import (
    QUESTIONNAIRE_EXTERNAL_EFFECT_CONTINUATION_CONSUMER,
    build_external_effect_continuation_registry,
)
from aicrm_next.identity_contact.dto import IdentityResolution, IdentityResolveResult
from aicrm_next.integration_gateway import wecom_channel_entry_client
from aicrm_next.integration_gateway.wecom_channel_entry_client import WeComApiError
from aicrm_next.main import create_app
from aicrm_next.platform_foundation.external_effects import (
    WECOM_CONTACT_TAG_MARK,
    ExternalEffectService,
    reset_external_effect_fixture_state,
)
from aicrm_next.platform_foundation.external_effects.adapters import (
    ExternalEffectAdapterRegistry,
    WeComContactTagAdapter,
)
from aicrm_next.platform_foundation.external_effects.completion_events import (
    external_effect_continuation_consumer,
)
from aicrm_next.platform_foundation.external_effects.repo import build_external_effect_repository
from aicrm_next.platform_foundation.external_effects.worker import ExternalEffectWorker
from aicrm_next.platform_foundation.internal_events import (
    InternalEventService,
    QUESTIONNAIRE_SUBMITTED_EVENT_TYPE,
    reset_internal_event_fixture_state,
)
from aicrm_next.platform_foundation.internal_events.worker import InternalEventWorker
from aicrm_next.platform_foundation.internal_events.models import (
    InternalEvent,
    InternalEventConsumerRun,
)
from aicrm_next.questionnaire import h5_write
from aicrm_next.questionnaire.external_effect_continuation import QUESTIONNAIRE_CONTACT_TAGS_CONTINUATION
from aicrm_next.questionnaire.h5_write import reset_questionnaire_h5_write_fixture_state
from aicrm_next.questionnaire.repo import reset_questionnaire_fixture_state
from wechat_identity_test_support import authorize_wechat_client


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    identities = {
        "union-real-001": ("wx_real_001", "owner-real-001"),
        "union-missing-external": (None, "owner-real-001"),
        "union-missing-owner": ("wx_missing_owner", None),
        "union-retry-001": ("wx_retry_001", "owner-retry-001"),
    }

    class ExplicitTagIdentityQuery:
        def execute_result(self, request):
            profile = identities.get(str(request.unionid or ""))
            if profile is None:
                return IdentityResolveResult(status="not_found", reason="identity_not_found")
            external_userid, owner_userid = profile
            return IdentityResolveResult(
                status="resolved",
                identity=IdentityResolution(
                    person_id=f"person-{request.unionid}",
                    external_userid=external_userid,
                    mobile=None,
                    unionid=request.unionid,
                    binding_status="bound",
                    owner_userid=owner_userid,
                    follow_user_userid=owner_userid,
                    matched_by="unionid",
                ),
                candidate_count=1,
                matched_fields=["unionid"],
            )

    monkeypatch.setattr(h5_write, "ResolvePersonIdentityQuery", ExplicitTagIdentityQuery)
    reset_questionnaire_fixture_state()
    reset_questionnaire_h5_write_fixture_state()
    reset_internal_event_fixture_state()
    reset_external_effect_fixture_state()
    reset_customer_tag_local_projection_fixture_state()
    monkeypatch.setenv("SECRET_KEY", "questionnaire-durable-wecom-tags")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    return TestClient(create_app(), raise_server_exceptions=False)


def _enable_wecom_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AICRM_WECOM_EXECUTION_MODE", "execute")
    monkeypatch.setenv("AICRM_WECOM_ENABLED_EFFECT_TYPES", WECOM_CONTACT_TAG_MARK)
    monkeypatch.setenv("AICRM_WECOM_DEFAULT_SENDER_USERID", "owner-default")
    monkeypatch.setenv("WECOM_CORP_ID", "corp-r09-tags")
    monkeypatch.setenv("WECOM_CONTACT_SECRET", "secret-r09-tags")
    monkeypatch.setenv("AICRM_WECOM_PROVIDER_TARGET_POLICY", "allowlisted_canary")
    monkeypatch.setenv(
        "AICRM_EXTERNAL_EFFECT_ALLOWED_TARGET_EXTERNAL_USERIDS",
        "wx_real_001,wx_retry_001",
    )
    monkeypatch.setenv(
        "AICRM_EXTERNAL_EFFECT_ALLOWED_OWNER_USERIDS",
        "owner-real-001,owner-retry-001",
    )
    monkeypatch.delenv("AICRM_EXTERNAL_EFFECT_WECOM_EXECUTE", raising=False)
    monkeypatch.delenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES", raising=False)


def _submit(client: TestClient, *, identity: dict, idempotency_key: str):
    authorize_wechat_client(client, identity)
    return client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={
            "answers": {"q_activation": "activated", "q_interest": ["ai_tools"]},
            "identity": identity,
        },
        headers={"Idempotency-Key": idempotency_key},
    )


def _run_tag_consumer(client: TestClient) -> dict:
    return InternalEventWorker(
        consumer_registry=client.app.state.internal_event_consumer_registry,
    ).run_due(
        batch_size=1,
        dry_run=False,
        event_types=[QUESTIONNAIRE_SUBMITTED_EVENT_TYPE],
        consumer_names=["questionnaire_tag_consumer"],
    )


def _authorize_tag_canary() -> None:
    service = ExternalEffectService()
    jobs, total = service.list_jobs({"effect_type": WECOM_CONTACT_TAG_MARK})
    assert total == 1
    authorized = service.authorize_allowlisted_canary(
        jobs[0].id,
        actor="pytest",
        reason="explicit questionnaire canary target confirmation",
        expected_version=jobs[0].row_version,
    )
    assert authorized is not None


def test_h5_submit_never_calls_wecom_and_only_reports_durable_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    class ExplodingProductionWeComAdapter:
        def __init__(self, *args, **kwargs):
            calls.append({"args": args, "kwargs": kwargs})
            raise AssertionError("H5 must not construct a provider adapter")

    monkeypatch.setattr(wecom_channel_entry_client, "ProductionWeComAdapter", ExplodingProductionWeComAdapter)
    client = _client(monkeypatch)
    response = _submit(
        client,
        identity={
            "external_userid": "wx_real_001",
            "follow_user_userid": "owner-real-001",
            "unionid": "union-real-001",
        },
        idempotency_key="questionnaire-r09-no-provider",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["tag_apply"]["status"] == "queued"
    assert body["tag_apply"]["adapter_mode"] == "durable_internal_event"
    assert body["tag_apply"]["wecom_api_called"] is False
    assert body["tag_apply"]["real_external_call_executed"] is False
    assert body["tag_apply"]["local_projection_updated"] is False
    assert body["durable_continuation_queued"] is True
    assert calls == []
    assert ExternalEffectService().list_jobs({"effect_type": WECOM_CONTACT_TAG_MARK})[1] == 0
    assert get_customer_tag_local_projection_fixture_rows() == []


@pytest.mark.parametrize(
    ("identity", "expected_missing"),
    [
        ({"follow_user_userid": "owner-real-001", "unionid": "union-missing-external"}, "external_userid"),
        ({"external_userid": "wx_missing_owner", "unionid": "union-missing-owner"}, "follow_user_userid"),
    ],
)
def test_tag_consumer_waits_retryably_for_canonical_identity(
    monkeypatch: pytest.MonkeyPatch,
    identity: dict,
    expected_missing: str,
) -> None:
    client = _client(monkeypatch)
    response = _submit(
        client,
        identity=identity,
        idempotency_key=f"questionnaire-r09-missing-{expected_missing}",
    )

    result = _run_tag_consumer(client)

    assert response.status_code == 200
    assert response.json()["tag_apply"]["status"] == "queued"
    assert result["counts"]["failed_retryable_count"] == 1, result
    response_summary = result["items"][0]["attempt"]["response_summary_json"]
    assert response_summary[f"{expected_missing}_present"] is False
    assert ExternalEffectService().list_jobs({"effect_type": WECOM_CONTACT_TAG_MARK})[1] == 0
    assert get_customer_tag_local_projection_fixture_rows() == []


def test_tag_consumer_plans_one_job_and_reuses_the_same_lineage(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_wecom_execution(monkeypatch)
    client = _client(monkeypatch)
    response = _submit(
        client,
        identity={
            "external_userid": "wx_real_001",
            "follow_user_userid": "owner-real-001",
            "unionid": "union-real-001",
        },
        idempotency_key="questionnaire-r09-tag-reuse",
    )

    first = _run_tag_consumer(client)
    events, _ = InternalEventService().list_events({"event_type": QUESTIONNAIRE_SUBMITTED_EVENT_TYPE})
    runs, _ = InternalEventService().list_consumer_runs({"event_id": events[0].event_id, "consumer_name": "questionnaire_tag_consumer"})
    from aicrm_next.questionnaire.event_consumers import questionnaire_tag_consumer

    replay = questionnaire_tag_consumer(events[0], runs[0])
    jobs, total = ExternalEffectService().list_jobs({"effect_type": WECOM_CONTACT_TAG_MARK})

    assert response.status_code == 200
    assert first["counts"]["succeeded_count"] == 1, first
    assert replay.status == "succeeded"
    assert replay.response_summary["external_effect_job_reused"] is True
    assert total == 1
    assert jobs[0].target_type == "unionid"
    assert jobs[0].target_id == "union-real-001"
    assert jobs[0].business_type == "questionnaire_submission"
    assert jobs[0].idempotency_key.endswith(f":{WECOM_CONTACT_TAG_MARK}")
    assert get_customer_tag_local_projection_fixture_rows() == []


def test_provider_success_projects_tags_only_after_external_effect_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    class FakeWeComAdapter:
        def mark_external_contact_tags(self, **payload):
            calls.append(payload)
            return {"errcode": 0, "errmsg": "ok"}

    _enable_wecom_execution(monkeypatch)
    client = _client(monkeypatch)
    response = _submit(
        client,
        identity={
            "external_userid": "wx_real_001",
            "follow_user_userid": "owner-real-001",
            "unionid": "union-real-001",
        },
        idempotency_key="questionnaire-r09-project-after-provider",
    )
    planned = _run_tag_consumer(client)
    assert planned["counts"]["succeeded_count"] == 1, planned
    assert get_customer_tag_local_projection_fixture_rows() == []
    _authorize_tag_canary()

    registry = ExternalEffectAdapterRegistry()
    registry._adapters["wecom_tag"] = WeComContactTagAdapter(  # type: ignore[attr-defined]
        adapter_factory=lambda: FakeWeComAdapter()
    )
    external_effect_repo = build_external_effect_repository()
    executed = ExternalEffectWorker(
        external_effect_repo,
        registry,
        continuation_registry=build_external_effect_continuation_registry(),
    ).run_due(
        batch_size=1,
        dry_run=False,
        effect_types=[WECOM_CONTACT_TAG_MARK],
    )

    assert response.status_code == 200
    assert executed["counts"]["succeeded_count"] == 1, executed
    assert executed["items"][0]["post_success_continuation"]["reason"] == "durable_completion_event_pending"
    queued_event = external_effect_repo.list_completion_events()[0]
    completion_result = external_effect_continuation_consumer(
        InternalEvent(
            event_id="iev_questionnaire_tag_completion",
            event_type=queued_event["event_type"],
            aggregate_type="external_effect_job",
            aggregate_id=queued_event["aggregate_id"],
            payload_json=dict(queued_event["payload"]),
        ),
        InternalEventConsumerRun(
            id=1,
            event_id="iev_questionnaire_tag_completion",
            consumer_name=QUESTIONNAIRE_EXTERNAL_EFFECT_CONTINUATION_CONSUMER,
        ),
        repository_factory=lambda: external_effect_repo,
        continuation=QUESTIONNAIRE_CONTACT_TAGS_CONTINUATION,
    )
    assert completion_result.status == "succeeded"
    assert completion_result.response_summary["continuation"]["ok"] is True
    assert calls == [
        {
            "external_userid": "wx_real_001",
            "follow_user_userid": "owner-real-001",
            "add_tags": ["tag_hxc_activated", "tag_interest_ai_tools"],
            "remove_tags": [],
        }
    ]
    rows = get_customer_tag_local_projection_fixture_rows()
    assert {row["tag_id"] for row in rows} == {"tag_hxc_activated", "tag_interest_ai_tools"}
    assert {row["userid"] for row in rows} == {"owner-real-001"}


@pytest.mark.parametrize(
    ("provider_error", "expected_status"),
    [
        (
            WeComApiError(
                "rate limited",
                payload={"errcode": 45009, "errmsg": "rate limited"},
                retry_after_seconds=2,
            ),
            "failed_retryable",
        ),
        (
            WeComApiError("timeout", error_code="timeout", classification="retryable"),
            "unknown_after_dispatch",
        ),
    ],
)
def test_provider_429_and_timeout_keep_durable_recovery_truth_without_projection(
    monkeypatch: pytest.MonkeyPatch,
    provider_error: WeComApiError,
    expected_status: str,
) -> None:
    class FailingWeComAdapter:
        def mark_external_contact_tags(self, **payload):
            raise provider_error

    _enable_wecom_execution(monkeypatch)
    client = _client(monkeypatch)
    _submit(
        client,
        identity={
            "external_userid": "wx_retry_001",
            "follow_user_userid": "owner-retry-001",
            "unionid": "union-retry-001",
        },
        idempotency_key=f"questionnaire-r09-{provider_error.error_code}",
    )
    assert _run_tag_consumer(client)["counts"]["succeeded_count"] == 1
    _authorize_tag_canary()

    registry = ExternalEffectAdapterRegistry()
    registry._adapters["wecom_tag"] = WeComContactTagAdapter(  # type: ignore[attr-defined]
        adapter_factory=lambda: FailingWeComAdapter()
    )
    executed = ExternalEffectWorker(build_external_effect_repository(), registry).run_due(
        batch_size=1,
        dry_run=False,
        effect_types=[WECOM_CONTACT_TAG_MARK],
    )
    jobs, total = ExternalEffectService().list_jobs({"effect_type": WECOM_CONTACT_TAG_MARK})

    expected_count = "unknown_after_dispatch_count" if expected_status == "unknown_after_dispatch" else "failed_count"
    assert executed["counts"][expected_count] == 1, executed
    assert total == 1
    assert jobs[0].status == expected_status
    if expected_status == "failed_retryable":
        assert jobs[0].next_retry_at
    else:
        assert jobs[0].reconciliation_required is True
    assert get_customer_tag_local_projection_fixture_rows() == []

    if expected_status == "unknown_after_dispatch":
        retried = ExternalEffectService().retry(
            jobs[0].id,
            actor="r09-test-operator",
            reason="fixture timeout recovery with explicit duplicate-risk confirmation",
            confirm_duplicate_risk=True,
        )
        assert retried is not None
        assert retried.status == "queued"
