from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("composed_internal_event_registry")

from fastapi.testclient import TestClient

from aicrm_next.identity_contact.application import BindMobileToExternalContactCommand
from aicrm_next.identity_contact.dto import BindMobileToExternalContactRequest
from aicrm_next.main import create_app
from aicrm_next.platform_foundation.external_effects import ExternalEffectService, reset_external_effect_fixture_state
from aicrm_next.platform_foundation.internal_events import InternalEventService, reset_internal_event_fixture_state
from aicrm_next.platform_foundation.internal_events.customer_identity import CUSTOMER_PHONE_BOUND_EVENT_TYPE
from aicrm_next.platform_foundation.internal_events.worker import InternalEventWorker
from aicrm_next.sidebar_write.application import reset_sidebar_write_fixture_state
from tests.sidebar_auth_test_helpers import install_sidebar_auth

PHONE_BOUND_CONSUMERS = frozenset({
    "automation_phone_bound_consumer",
    "customer_identity_ai_assist_notify_consumer",
    "customer_identity_projection_consumer",
    "customer_read_model_dirty_consumer",
    "customer_summary_consumer",
})


def _reset() -> None:
    reset_internal_event_fixture_state()
    reset_external_effect_fixture_state()
    reset_sidebar_write_fixture_state()


def _enable_phone_bound_events(monkeypatch, *, enabled: bool = True, allowed: str = CUSTOMER_PHONE_BOUND_EVENT_TYPE) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_CUSTOMER_IDENTITY_ENABLED", "1" if enabled else "0")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", allowed)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_SHADOW_ONLY", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE", "0")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES", "")


def _bind(*, key: str = "001", external_userid: str | None = None, mobile: str | None = None) -> dict:
    return BindMobileToExternalContactCommand()(
        BindMobileToExternalContactRequest(
            external_userid=external_userid or f"wm_phone_bound_slice_{key}",
            mobile=mobile or f"1380013{int(key):04d}",
            owner_userid="ZhaoYanFang",
            bind_by_userid="identity-test",
            customer_name="Phone Bound Test",
        )
    )


def _event():
    events, total = InternalEventService().list_events({"event_type": CUSTOMER_PHONE_BOUND_EVENT_TYPE})
    assert total == 1
    return events[0]


def _run_consumer(event_id: str, consumer_name: str) -> dict:
    return InternalEventWorker().dispatch_one_consumer(
        event_id,
        consumer_name,
        dry_run=False,
        force=False,
        reason="customer_phone_bound_slice_unit_test",
    )


def _sidebar_bind(
    client: TestClient,
    *,
    external_userid: str,
    mobile: str,
    idempotency_key: str,
) -> dict:
    headers = install_sidebar_auth(
        client,
        viewer_userid="LiuXiao" if external_userid == "wx_ext_002" else "ZhaoYanFang",
        external_userid=external_userid,
    )
    headers["Idempotency-Key"] = idempotency_key
    response = client.post(
        "/api/sidebar/bind-mobile",
        json={"external_userid": external_userid, "mobile": mobile},
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()


def test_customer_phone_bound_flag_off_does_not_emit(monkeypatch) -> None:
    _enable_phone_bound_events(monkeypatch, enabled=False)
    _reset()

    result = _bind(key="101")
    events, total = InternalEventService().list_events({"event_type": CUSTOMER_PHONE_BOUND_EVENT_TYPE})

    assert result["ok"] is True
    assert result["internal_event_status"] == "skipped"
    assert result["internal_event_reason"] == "customer_identity_internal_events_disabled"
    assert result["internal_event_id"] == ""
    assert events == []
    assert total == 0


def test_sidebar_bind_mobile_flag_off_does_not_emit(monkeypatch) -> None:
    _enable_phone_bound_events(monkeypatch, enabled=False)
    _reset()
    client = TestClient(create_app())

    result = _sidebar_bind(
        client,
        external_userid="wx_ext_002",
        mobile="13800138123",
        idempotency_key="sidebar-phone-bound-off",
    )
    events, total = InternalEventService().list_events({"event_type": CUSTOMER_PHONE_BOUND_EVENT_TYPE})

    assert result["ok"] is True
    assert result["internal_event_status"] == "skipped"
    assert result["internal_event_reason"] == "customer_identity_internal_events_disabled"
    assert result["internal_event_id"] == ""
    assert result["internal_event_consumer_run_count"] == 0
    assert events == []
    assert total == 0


def test_customer_phone_bound_emits_single_event_and_expected_consumers(monkeypatch) -> None:
    _enable_phone_bound_events(monkeypatch)
    _reset()

    result = _bind(key="201", external_userid="wm_phone_bound_slice_201", mobile="13800132001")
    duplicate = _bind(key="201", external_userid="wm_phone_bound_slice_201", mobile="13800132001")
    event = _event()
    runs, run_total = InternalEventService().list_consumer_runs({"event_id": event.event_id})

    assert result["internal_event_status"] == "emitted"
    assert duplicate["internal_event_id"] == event.event_id
    assert event.event_type == CUSTOMER_PHONE_BOUND_EVENT_TYPE
    assert event.aggregate_type == "customer"
    assert event.aggregate_id == "fixture_person_2001"
    assert event.subject_type == "customer"
    assert event.subject_id == "wm_p..._201"
    assert event.idempotency_key == "customer.phone_bound:person:fixture_person_2001:54c4975872e95c35"
    assert event.payload_json["binding"]["external_userid"] == "wm_phone_bound_slice_201"
    assert event.payload_json["binding"]["mobile"] == "13800132001"
    assert event.payload_summary_json["external_userid_present"] is True
    assert event.payload_summary_json["person_id_present"] is True
    assert event.payload_summary_json["mobile_masked"] == "138****2001"
    assert "13800132001" not in str(event.payload_summary_json)
    assert "wm_phone_bound_slice_201" not in str(event.payload_summary_json)
    assert run_total == len(PHONE_BOUND_CONSUMERS)
    assert {run.consumer_name for run in runs} == PHONE_BOUND_CONSUMERS
    assert all(run.status == "pending" for run in runs)
    assert all(run.attempt_count == 0 for run in runs)


def test_sidebar_bind_mobile_emits_phone_bound_event_and_expected_consumers(monkeypatch) -> None:
    _enable_phone_bound_events(monkeypatch)
    _reset()
    client = TestClient(create_app())

    result = _sidebar_bind(
        client,
        external_userid="wx_ext_002",
        mobile="13800138124",
        idempotency_key="sidebar-phone-bound-on",
    )
    event = _event()
    runs, run_total = InternalEventService().list_consumer_runs({"event_id": event.event_id})

    assert result["ok"] is True
    assert result["internal_event_status"] == "emitted"
    assert result["internal_event_id"] == event.event_id
    assert result["internal_event_consumer_run_count"] == len(PHONE_BOUND_CONSUMERS)
    assert result["binding"]["binding_status"] == "bound"
    assert event.event_type == CUSTOMER_PHONE_BOUND_EVENT_TYPE
    assert event.aggregate_type == "customer"
    assert event.aggregate_id == "wx_ext_002"
    assert event.source_module == "sidebar_write.application"
    assert event.source_route == "/api/sidebar/bind-mobile"
    assert event.payload_json["binding"]["external_userid"] == "wx_ext_002"
    assert event.payload_json["binding"]["mobile"] == "13800138124"
    assert event.payload_json["binding"]["matched_by"] == "sidebar_bind_mobile"
    assert event.payload_summary_json["external_userid_present"] is True
    assert event.payload_summary_json["mobile_masked"] == "138****8124"
    assert "13800138124" not in str(event.payload_summary_json)
    assert "wx_ext_002" not in str(event.payload_summary_json)
    assert run_total == len(PHONE_BOUND_CONSUMERS)
    assert {run.consumer_name for run in runs} == PHONE_BOUND_CONSUMERS
    assert all(run.status == "pending" for run in runs)
    assert all(run.attempt_count == 0 for run in runs)


def test_sidebar_bind_mobile_duplicate_does_not_duplicate_phone_bound_event(monkeypatch) -> None:
    _enable_phone_bound_events(monkeypatch)
    _reset()
    client = TestClient(create_app())

    first = _sidebar_bind(
        client,
        external_userid="wx_ext_002",
        mobile="13800138125",
        idempotency_key="sidebar-phone-bound-idem-1",
    )
    second = _sidebar_bind(
        client,
        external_userid="wx_ext_002",
        mobile="13800138125",
        idempotency_key="sidebar-phone-bound-idem-2",
    )
    events, total = InternalEventService().list_events({"event_type": CUSTOMER_PHONE_BOUND_EVENT_TYPE})
    runs, run_total = InternalEventService().list_consumer_runs({"event_id": first["internal_event_id"]})

    assert total == 1
    assert len(events) == 1
    assert first["internal_event_id"] == second["internal_event_id"] == events[0].event_id
    assert run_total == len(PHONE_BOUND_CONSUMERS)
    assert {run.consumer_name for run in runs} == PHONE_BOUND_CONSUMERS


def test_customer_phone_bound_fallback_idempotency_does_not_expose_external_userid(monkeypatch) -> None:
    class NoPersonRepo:
        def bind_mobile_to_external_contact(self, **kwargs):
            return {
                "ok": True,
                "source_status": "test_no_person_repo",
                "external_userid": kwargs["external_userid"],
                "mobile": kwargs["mobile"],
                "person_id": "",
                "binding_status": "bound",
            }

    _enable_phone_bound_events(monkeypatch)
    _reset()
    result = BindMobileToExternalContactCommand(NoPersonRepo())(
        BindMobileToExternalContactRequest(external_userid="wm_raw_external_user_for_hash", mobile="13800132011")
    )
    event = _event()

    assert result["internal_event_status"] == "emitted"
    assert event.aggregate_id == "wm_raw_external_user_for_hash"
    assert "wm_raw_external_user_for_hash" not in event.idempotency_key
    assert "external_userid:" in event.idempotency_key


def test_customer_phone_bound_skipped_binding_result_does_not_emit(monkeypatch) -> None:
    class SkippedRepo:
        def bind_mobile_to_external_contact(self, **kwargs):
            return {
                "ok": True,
                "source_status": "test_skipped_repo",
                "external_userid": kwargs["external_userid"],
                "mobile": kwargs["mobile"],
                "person_id": "",
                "binding_status": "unresolved",
            }

    _enable_phone_bound_events(monkeypatch)
    _reset()
    result = BindMobileToExternalContactCommand(SkippedRepo())(
        BindMobileToExternalContactRequest(external_userid="wm_phone_bound_unresolved", mobile="13800132012")
    )
    events, total = InternalEventService().list_events({"event_type": CUSTOMER_PHONE_BOUND_EVENT_TYPE})

    assert result["internal_event_status"] == "skipped"
    assert result["internal_event_reason"] == "customer_phone_binding_not_successful"
    assert events == []
    assert total == 0


def test_customer_phone_bound_api_redacts_payload_summary_and_hides_payload_json(monkeypatch) -> None:
    _enable_phone_bound_events(monkeypatch)
    _reset()
    client = TestClient(create_app())
    _bind(key="202", external_userid="wm_phone_bound_slice_202", mobile="13800132002")
    event = _event()

    list_payload = client.get("/api/admin/internal-events", params={"event_type": CUSTOMER_PHONE_BOUND_EVENT_TYPE}).json()
    detail_payload = client.get(f"/api/admin/internal-events/{event.event_id}").json()

    assert list_payload["ok"] is True
    assert list_payload["items"][0]["payload_summary_json"]["mobile_masked"] == "[redacted]"
    assert "payload_json" not in list_payload["items"][0]
    assert detail_payload["event"]["payload_summary_json"]["mobile_masked"] == "[redacted]"
    assert "payload_json" not in detail_payload
    assert "13800132002" not in str(list_payload)
    assert "13800132002" not in str(detail_payload)
    assert "wm_phone_bound_slice_202" not in str(list_payload)
    assert "wm_phone_bound_slice_202" not in str(detail_payload)


def test_sidebar_bind_mobile_api_redacts_payload_summary_and_hides_payload_json(monkeypatch) -> None:
    _enable_phone_bound_events(monkeypatch)
    _reset()
    client = TestClient(create_app())
    _sidebar_bind(
        client,
        external_userid="wx_ext_002",
        mobile="13800138126",
        idempotency_key="sidebar-phone-bound-redaction",
    )
    event = _event()

    list_payload = client.get("/api/admin/internal-events", params={"event_type": CUSTOMER_PHONE_BOUND_EVENT_TYPE}).json()
    detail_payload = client.get(f"/api/admin/internal-events/{event.event_id}").json()

    assert list_payload["ok"] is True
    assert list_payload["items"][0]["payload_summary_json"]["mobile_masked"] == "[redacted]"
    assert "payload_json" not in list_payload["items"][0]
    assert detail_payload["event"]["payload_summary_json"]["mobile_masked"] == "[redacted]"
    assert "payload_json" not in detail_payload
    assert "13800138126" not in str(list_payload)
    assert "13800138126" not in str(detail_payload)
    assert "wx_ext_002" not in str(list_payload["items"][0]["payload_summary_json"])
    assert "wx_ext_002" not in str(detail_payload["event"]["payload_summary_json"])


def test_customer_identity_projection_consumer_succeeds_and_other_consumers_skip(monkeypatch) -> None:
    _enable_phone_bound_events(monkeypatch)
    _reset()
    _bind(key="301")
    event = _event()

    projection = _run_consumer(event.event_id, "customer_identity_projection_consumer")
    summary = _run_consumer(event.event_id, "customer_summary_consumer")
    automation = _run_consumer(event.event_id, "automation_phone_bound_consumer")
    ai_assist = _run_consumer(event.event_id, "customer_identity_ai_assist_notify_consumer")
    _jobs, job_total = ExternalEffectService().list_jobs({})

    assert projection["consumer_run"]["status"] == "succeeded"
    assert projection["attempt"]["response_summary_json"]["real_external_call_executed"] is False
    assert projection["consumer_run"]["result_summary_json"]["customer_identity_projection"] == "phone_bound_confirmed"
    assert summary["consumer_run"]["status"] == "skipped"
    assert summary["attempt"]["response_summary_json"]["reason"] == "customer_summary_not_configured"
    assert automation["consumer_run"]["status"] == "skipped"
    assert automation["attempt"]["response_summary_json"]["reason"] == "automation_phone_bound_not_configured"
    assert ai_assist["consumer_run"]["status"] == "skipped"
    assert ai_assist["attempt"]["response_summary_json"]["reason"] == "customer_identity_ai_assist_notify_not_configured"
    assert job_total == 0


def test_worker_pair_allowlist_blocks_phone_bound_auto_execute_but_single_consumer_still_works(monkeypatch) -> None:
    _enable_phone_bound_events(monkeypatch, allowed="payment.succeeded,customer.phone_bound")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS", "order_projection_consumer,customer_identity_projection_consumer")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS", "payment.succeeded:order_projection_consumer")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE_MAX_BATCH_SIZE", "1")
    _reset()
    _bind(key="401")
    event = _event()
    worker = InternalEventWorker()

    preview = worker.preview_due(
        batch_size=1,
        event_types=[CUSTOMER_PHONE_BOUND_EVENT_TYPE],
        consumer_names=["customer_identity_projection_consumer", "customer_summary_consumer"],
    )
    execute = worker.run_due(
        batch_size=1,
        dry_run=False,
        event_types=[CUSTOMER_PHONE_BOUND_EVENT_TYPE],
        consumer_names=["customer_identity_projection_consumer", "customer_summary_consumer"],
    )
    manual = worker.dispatch_one_consumer(
        event.event_id,
        "customer_identity_projection_consumer",
        dry_run=False,
        force=False,
        reason="customer_phone_bound_manual_single_consumer_test",
    )
    runs, _ = InternalEventService().list_consumer_runs({"event_id": event.event_id})

    assert preview["counts"]["candidate_count"] == 0
    assert preview["event_consumers"] == []
    assert execute["counts"]["processed_count"] == 0
    assert execute["event_consumers"] == []
    assert manual["consumer_run"]["status"] == "succeeded"
    assert next(run for run in runs if run.consumer_name == "customer_summary_consumer").status == "pending"


def test_sidebar_phone_bound_pair_allowlist_blocks_auto_execute(monkeypatch) -> None:
    _enable_phone_bound_events(monkeypatch, allowed="payment.succeeded,customer.phone_bound")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS", "order_projection_consumer,customer_identity_projection_consumer")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS", "payment.succeeded:order_projection_consumer")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE_MAX_BATCH_SIZE", "1")
    _reset()
    client = TestClient(create_app())
    _sidebar_bind(
        client,
        external_userid="wx_ext_002",
        mobile="13800138127",
        idempotency_key="sidebar-phone-bound-pair-allowlist",
    )
    event = _event()
    worker = InternalEventWorker()

    preview = worker.preview_due(
        batch_size=1,
        event_types=[CUSTOMER_PHONE_BOUND_EVENT_TYPE],
        consumer_names=["customer_identity_projection_consumer", "customer_summary_consumer"],
    )
    execute = worker.run_due(
        batch_size=1,
        dry_run=False,
        event_types=[CUSTOMER_PHONE_BOUND_EVENT_TYPE],
        consumer_names=["customer_identity_projection_consumer", "customer_summary_consumer"],
    )
    runs, _ = InternalEventService().list_consumer_runs({"event_id": event.event_id})

    assert preview["counts"]["candidate_count"] == 0
    assert preview["event_consumers"] == []
    assert execute["counts"]["processed_count"] == 0
    assert execute["event_consumers"] == []
    assert all(run.status == "pending" for run in runs)
    assert all(run.attempt_count == 0 for run in runs)


def test_diagnostics_exposes_customer_identity_flag(monkeypatch) -> None:
    _enable_phone_bound_events(monkeypatch)
    _reset()

    response = TestClient(create_app()).get("/api/admin/internal-events/diagnostics")

    assert response.status_code == 200
    assert response.json()["customer_identity_internal_events_enabled"] is True
