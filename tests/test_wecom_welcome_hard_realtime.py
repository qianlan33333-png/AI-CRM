from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aicrm_next.channels.channel_entry.realtime_contract import (
    WECOM_WELCOME_EFFECT_LANE,
    WECOM_WELCOME_INGRESS_LANE,
    welcome_provider_deadline,
)
from aicrm_next.channels.channel_entry.inbox import ingest_wecom_callback
from aicrm_next.channels.channel_entry.application import _welcome_callback_received_at
from aicrm_next.channels.channel_entry.schemas import ProcessChannelEntryCommand
from aicrm_next.platform.platform_foundation.external_effects.adapters import (
    ExternalEffectAdapterRegistry,
    WeComWelcomeMessageAdapter,
)
from aicrm_next.platform.platform_foundation.external_effects.models import (
    ExternalEffectDispatchResult,
    ExternalEffectJob,
    WECOM_WELCOME_MESSAGE_SEND,
)
from aicrm_next.platform.platform_foundation.external_effects.repo import InMemoryExternalEffectRepository
from aicrm_next.platform.platform_foundation.external_effects.service import ExternalEffectService
from aicrm_next.platform.platform_foundation.external_effects.worker import ExternalEffectWorker
from aicrm_next.platform.platform_foundation.webhook_inbox.repository import InMemoryWebhookInboxRepository


class _RecordingAdapter:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def dispatch(self, job):
        self.calls.append(int(job.id))
        return ExternalEffectDispatchResult(
            status="succeeded",
            adapter_mode="test_fake",
            request_summary={"job_id": int(job.id)},
            response_summary={"provider_result_received": True},
            real_external_call_executed=True,
            provider_result_received=True,
        )


def _welcome_job(
    repository: InMemoryExternalEffectRepository,
    *,
    deadline: datetime,
) -> dict:
    return ExternalEffectService(repository).plan_effect(
        effect_type=WECOM_WELCOME_MESSAGE_SEND,
        adapter_name="welcome_recording",
        operation="send",
        target_type="wecom_external_contact",
        target_id="wm-hard-realtime",
        payload={
            "welcome_code": "welcome-code",
            "provider_deadline_at": deadline.isoformat(),
        },
        payload_summary={"provider_deadline_enforced": True},
        idempotency_key=f"welcome-hard-realtime:{deadline.isoformat()}",
        lane=WECOM_WELCOME_EFFECT_LANE,
        max_attempts=1,
    )


def _enable_welcome_execution(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_WECOM_EXECUTION_MODE", "execute")
    monkeypatch.setenv("AICRM_WECOM_ENABLED_EFFECT_TYPES", WECOM_WELCOME_MESSAGE_SEND)
    monkeypatch.delenv("AICRM_NEXT_WECOM_REAL_CALLS_ENABLED", raising=False)
    monkeypatch.delenv("AICRM_EXTERNAL_EFFECT_WECOM_EXECUTE", raising=False)


def test_welcome_callback_uses_reserved_ingress_lane() -> None:
    repository = InMemoryWebhookInboxRepository()

    ingest_wecom_callback(
        query={},
        headers={},
        body=b"",
        event_data={
            "ToUserName": "corp",
            "Event": "change_external_contact",
            "ChangeType": "add_external_contact",
            "ExternalUserID": "wm-welcome",
            "UserID": "owner",
            "CreateTime": "1",
            "State": "scene",
            "WelcomeCode": "code",
        },
        plain_xml="",
        route="/wecom/external-contact/callback",
        repository=repository,
    )
    ingest_wecom_callback(
        query={},
        headers={},
        body=b"",
        event_data={
            "ToUserName": "corp",
            "Event": "change_external_contact",
            "ChangeType": "add_external_contact",
            "ExternalUserID": "wm-welcome-no-state",
            "UserID": "owner",
            "CreateTime": "2",
            "WelcomeCode": "code-without-state",
        },
        plain_xml="",
        route="/wecom/external-contact/callback",
        repository=repository,
    )
    ingest_wecom_callback(
        query={},
        headers={},
        body=b"",
        event_data={
            "ToUserName": "corp",
            "Event": "change_external_contact",
            "ChangeType": "del_external_contact",
            "ExternalUserID": "wm-ordinary",
            "UserID": "owner",
            "CreateTime": "3",
        },
        plain_xml="",
        route="/wecom/external-contact/callback",
        repository=repository,
    )

    assert repository.rows[0]["lane"] == WECOM_WELCOME_INGRESS_LANE
    assert repository.rows[1]["lane"] == WECOM_WELCOME_INGRESS_LANE
    assert repository.rows[2]["lane"] == "webhook_inbox"


def test_welcome_deadline_keeps_two_seconds_for_provider_round_trip() -> None:
    received_at = datetime(2026, 7, 24, 3, 30, tzinfo=timezone.utc)

    deadline = welcome_provider_deadline(received_at)

    assert deadline == received_at + timedelta(seconds=18)


def test_historical_repair_never_replays_welcome_code() -> None:
    command = ProcessChannelEntryCommand(
        event_action="repair_channel_entry",
        event_log_id=1,
        callback_received_at=datetime.now(timezone.utc),
        payload_json={
            "CreateTime": str(int(datetime.now(timezone.utc).timestamp())),
            "WelcomeCode": "still-present-in-history",
        },
    )

    assert _welcome_callback_received_at(command) is None


def test_expired_welcome_never_crosses_provider_boundary(monkeypatch) -> None:
    _enable_welcome_execution(monkeypatch)
    repository = InMemoryExternalEffectRepository()
    adapter = _RecordingAdapter()
    registry = ExternalEffectAdapterRegistry({"welcome_recording": adapter})
    job = _welcome_job(repository, deadline=datetime.now(timezone.utc) - timedelta(seconds=1))

    result = ExternalEffectWorker(repository, registry).run_due(batch_size=1, dry_run=False)
    current = repository.get_job(int(job["id"]))

    assert current is not None
    assert current.status == "expired"
    assert current.provider_call_started_at == ""
    assert current.last_error_code == "provider_deadline_elapsed"
    assert adapter.calls == []
    assert result["counts"]["expired_count"] == 1


def test_welcome_adapter_rechecks_deadline_before_http_call() -> None:
    provider_calls: list[dict] = []

    class Provider:
        def send_welcome_msg(self, payload):
            provider_calls.append(dict(payload))
            return {"errcode": 0}

    result = WeComWelcomeMessageAdapter(adapter_factory=Provider).dispatch(
        ExternalEffectJob(
            id=1,
            effect_type=WECOM_WELCOME_MESSAGE_SEND,
            execution_mode="execute",
            target_type="wecom_external_contact",
            target_id="wm-hard-realtime",
            payload_json={
                "external_userid": "wm-hard-realtime",
                "follow_user_userid": "owner",
                "welcome_code": "code",
                "text": {"content": "hello"},
                "provider_deadline_at": (
                    datetime.now(timezone.utc) - timedelta(milliseconds=1)
                ).isoformat(),
            },
        )
    )

    assert result.status == "failed_terminal"
    assert result.error_code == "provider_deadline_elapsed"
    assert result.real_external_call_executed is False
    assert provider_calls == []


def test_unexpired_welcome_dispatches_once(monkeypatch) -> None:
    _enable_welcome_execution(monkeypatch)
    repository = InMemoryExternalEffectRepository()
    adapter = _RecordingAdapter()
    registry = ExternalEffectAdapterRegistry({"welcome_recording": adapter})
    job = _welcome_job(repository, deadline=datetime.now(timezone.utc) + timedelta(seconds=10))

    result = ExternalEffectWorker(repository, registry).run_due(batch_size=1, dry_run=False)
    current = repository.get_job(int(job["id"]))

    assert current is not None
    assert current.status == "succeeded"
    assert adapter.calls == [int(job["id"])]
    assert result["counts"]["succeeded_count"] == 1


def test_welcome_media_post_commit_hook_runs_only_after_success_is_durable(monkeypatch) -> None:
    _enable_welcome_execution(monkeypatch)
    repository = InMemoryExternalEffectRepository()
    adapter = _RecordingAdapter()
    registry = ExternalEffectAdapterRegistry({"welcome_recording": adapter})
    job = _welcome_job(repository, deadline=datetime.now(timezone.utc) + timedelta(seconds=10))
    seen: list[tuple[int, str]] = []

    def hook(completed_job, _dispatch_result):
        canonical = repository.get_job(int(completed_job.id))
        seen.append((int(completed_job.id), str((canonical or completed_job).status)))
        return {"applicable": True, "ok": True, "released": True}

    result = ExternalEffectWorker(
        repository,
        registry,
        critical_post_commit_hook=hook,
    ).run_due(batch_size=1, dry_run=False)

    assert seen == [(int(job["id"]), "succeeded")]
    assert result["items"][0]["post_success_continuation"]["released"] is True
