from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import text

import aicrm_next.background_jobs.broadcast_queue_worker as worker
from aicrm_next.background_jobs.broadcast_queue_worker import PostgresBroadcastQueueRepository, SafeSkippedBroadcastDispatcher, run_broadcast_queue_worker
from aicrm_next.shared.db_session import get_session_factory


class FakeRepo:
    def __init__(self, jobs: list[dict[str, Any]]) -> None:
        self.jobs = jobs
        self.delegated: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.simulated: list[dict[str, Any]] = []
        self.failed: list[dict[str, Any]] = []
        self.unknown: list[dict[str, Any]] = []
        self.claim_token = ""

    def claim_due_jobs(self, *, limit: int, now: datetime, claim_token: str, lease_seconds: int) -> list[dict[str, Any]]:
        self.claim_token = claim_token
        return self.jobs[:limit]

    def begin_dispatch(self, job_id: int, *, claim_token: str, now: datetime) -> dict[str, Any] | None:
        return next(({**job, "status": "dispatching"} for job in self.jobs if int(job["id"]) == job_id), None)

    def finalize_dispatch(self, job_id: int, *, claim_token: str, outcome: dict[str, Any]) -> dict[str, Any]:
        record = {"job_id": job_id, "claim_token": claim_token, "outbound_task_id": None, **outcome}
        if outcome["status"] == "sent":
            self.sent.append(record)
        elif outcome["status"] == "delegated":
            self.delegated.append(record)
        elif outcome["status"] == "simulated":
            self.simulated.append(record)
        else:
            self.failed.append(record)
        return record

    def mark_unknown_after_dispatch(
        self,
        job_id: int,
        *,
        claim_token: str,
        error: str,
        side_effect_executed: bool,
        provider_result_received: bool,
    ) -> dict[str, Any]:
        record = {
            "job_id": job_id,
            "claim_token": claim_token,
            "status": "unknown_after_dispatch",
            "error": error,
            "side_effect_executed": side_effect_executed,
            "provider_result_received": provider_result_received,
        }
        self.unknown.append(record)
        return record


class Adapter:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result

    def create_private_message_task(self, payload: dict[str, Any], *, idempotency_key: str = "") -> dict[str, Any]:
        self.payload = payload
        self.idempotency_key = idempotency_key
        result = dict(self.result)
        result.setdefault(
            "side_effect_executed",
            bool(result.get("ok")) and str(result.get("mode") or "").lower() != "fake",
        )
        return result


class RecordingWeComClient:
    def __init__(self) -> None:
        self.payload: dict[str, Any] | None = None

    def create_group_message_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payload = payload
        return {"errcode": 0, "msgid": "msg-recorded"}


@pytest.fixture(autouse=True)
def _resolve_unionid_targets(monkeypatch) -> None:
    def fake_resolver(unionids: list[str]) -> tuple[list[str], list[str]]:
        return [f"wm_{str(item).removeprefix('union_')}" for item in unionids], []

    monkeypatch.setattr(worker, "_resolve_private_targets_by_unionid", fake_resolver)


def _job(**overrides: Any) -> dict[str, Any]:
    payload = {
        "channel": "wecom_private",
        "sender_userid": "HuangYouCan",
        "target_unionids": ["union_test"],
        "rendered_content": {"content_text": "hello private"},
    }
    payload.update(overrides.pop("payload", {}))
    job = {
        "id": 101,
        "source_type": "campaign",
        "source_table": "campaign_members",
        "source_id": "campaign:1:member:3",
        "idempotency_key": "campaign:1:member:3",
        "trace_id": "campaign:1:member:3",
        "channel": "wecom_private",
        "content_type": "private_message",
        "target_kind": "unionid",
        "target_unionids_json": json.dumps(["union_test"]),
        "target_count": 1,
        "content_payload": payload,
    }
    job.update(overrides)
    return job


@pytest.mark.skip(reason="broadcast provider dispatch retired; External Effect owns execution")
def test_wecom_private_job_is_dispatched_and_marked_sent(monkeypatch) -> None:
    adapter = Adapter({"ok": True, "wecom_msgid": "msg-1", "result": {"msgid": "msg-1"}})
    monkeypatch.setattr("aicrm_next.integration_gateway.wecom_private_adapter.build_wecom_private_message_adapter", lambda: adapter)
    repo = FakeRepo([_job()])

    summary = run_broadcast_queue_worker(repo=repo, dispatcher=SafeSkippedBroadcastDispatcher(), now=datetime(2026, 6, 1, tzinfo=timezone.utc))

    assert summary["sent_ok"] == 1
    assert {key: repo.sent[0][key] for key in ("job_id", "sent_count", "failed_count")} == {
        "job_id": 101,
        "sent_count": 1,
        "failed_count": 0,
    }
    assert repo.sent[0]["claim_token"]
    assert adapter.payload["sender"] == "HuangYouCan"
    assert adapter.payload["external_userids"] == ["wm_test"]


@pytest.mark.skip(reason="broadcast provider dispatch retired; External Effect owns execution")
def test_wecom_private_fake_success_is_simulated_and_never_projected_as_sent(monkeypatch) -> None:
    adapter = Adapter(
        {
            "ok": True,
            "mode": "fake",
            "side_effect_executed": False,
            "wecom_msgid": "fake-msg-1",
            "result": {"msgid": "fake-msg-1"},
        }
    )
    monkeypatch.setattr("aicrm_next.integration_gateway.wecom_private_adapter.build_wecom_private_message_adapter", lambda: adapter)
    repo = FakeRepo([_job()])

    summary = run_broadcast_queue_worker(repo=repo, dispatcher=SafeSkippedBroadcastDispatcher())

    assert summary["simulated"] == 1
    assert summary["sent_ok"] == 0
    assert repo.sent == []
    assert repo.simulated[0]["status"] == "simulated"
    assert summary["results"][0]["side_effect_executed"] is False


@pytest.mark.skip(reason="broadcast provider dispatch retired; External Effect owns execution")
def test_wecom_group_fake_success_is_simulated_and_never_marked_sent(monkeypatch) -> None:
    class FakeGroupAdapter:
        def create_group_message_task(self, payload: dict[str, Any], *, idempotency_key: str = "") -> dict[str, Any]:
            return {
                "ok": True,
                "mode": "fake",
                "side_effect_executed": False,
                "exact_target_verified": True,
                "requested_chat_ids": list(payload.get("chat_ids") or []),
                "wecom_msgid": "fake-group-msg",
            }

    monkeypatch.setenv("AICRM_WECOM_EXECUTION_MODE", "execute")
    monkeypatch.setattr(
        "aicrm_next.integration_gateway.wecom_group_adapter.build_wecom_group_message_adapter",
        lambda: FakeGroupAdapter(),
    )
    repo = FakeRepo(
        [
            _job(
                channel="wecom_customer_group",
                content_type="wecom_customer_group",
                target_kind="chat_id",
                target_unionids_json="[]",
                target_count=1,
                payload={"channel": "wecom_customer_group", "chat_ids": ["chat-1"], "text": {"content": "hello"}},
            )
        ]
    )

    summary = run_broadcast_queue_worker(repo=repo, dispatcher=SafeSkippedBroadcastDispatcher())

    assert summary["simulated"] == 1
    assert summary["sent_ok"] == 0
    assert repo.sent == []
    assert repo.simulated[0]["status"] == "simulated"


@pytest.mark.skip(reason="execution policy is enforced by the External Effect owner")
def test_wecom_private_global_execution_mode_disabled_blocks_before_adapter(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_WECOM_EXECUTION_MODE", "disabled")

    def fail_adapter():
        raise AssertionError("adapter should not be built when global WeCom execution is disabled")

    monkeypatch.setattr("aicrm_next.integration_gateway.wecom_private_adapter.build_wecom_private_message_adapter", fail_adapter)
    repo = FakeRepo([_job()])

    summary = run_broadcast_queue_worker(repo=repo, dispatcher=SafeSkippedBroadcastDispatcher())

    assert summary["sent_failed"] == 1
    assert repo.failed[0]["failure_type"] == "wecom_execution_disabled"
    assert "AICRM_WECOM_EXECUTION_MODE=disabled" in repo.failed[0]["error"]


@pytest.mark.skip(reason="broadcast provider dispatch retired; External Effect owns execution")
def test_cloud_plan_recipient_message_uses_bound_sender_and_hydrates_text(monkeypatch) -> None:
    adapter = Adapter({"ok": True, "wecom_msgid": "msg-cloud", "result": {"msgid": "msg-cloud"}})
    monkeypatch.setattr("aicrm_next.integration_gateway.wecom_private_adapter.build_wecom_private_message_adapter", lambda: adapter)
    monkeypatch.setattr(worker, "runtime_setting", lambda key, default="": "HuangYouCan" if key == "AICRM_EXTERNAL_EFFECT_ALLOWED_OWNER_USERIDS" else default)
    monkeypatch.setattr(
        worker,
        "_load_cloud_plan_recipient_message",
        lambda payload: {"cloud_plan_message_id": 77, "content_text": "agent generated hello", "content_payload_json": {}, "attachments": []},
    )
    repo = FakeRepo(
        [
            _job(
                source_type="cloud_plan",
                source_table="cloud_broadcast_plan_recipients",
                source_id="agent_plan:2",
                idempotency_key="cloud_plan_recipient:agent_plan:2",
                channel="wecom_private",
                content_type="cloud_plan",
                payload={
                    "plan_id": "agent_plan",
                    "recipient_id": 2,
                    "external_userid": "wm_test",
                    "message_mode": "recipient_messages",
                    "owner_userid": "WangWei",
                    "rendered_content": {},
                },
            )
        ]
    )

    summary = run_broadcast_queue_worker(repo=repo, dispatcher=SafeSkippedBroadcastDispatcher(), now=datetime(2026, 6, 1, tzinfo=timezone.utc))

    assert summary["sent_ok"] == 1
    assert adapter.payload["sender"] == "HuangYouCan"
    assert adapter.payload["text"] == {"content": "agent generated hello"}
    assert repo.sent[0]["request_payload"]["content_preview"] == "agent generated hello"


def test_cloud_plan_message_loader_does_not_require_external_userid_column(monkeypatch) -> None:
    class Cursor:
        def execute(self, sql: str, params: tuple[Any, ...]) -> "Cursor":
            assert "external_userid" not in sql
            assert params == ("agent_plan", 2)
            return self

        def fetchone(self) -> dict[str, Any]:
            return {
                "id": 77,
                "recipient_id": 2,
                "content_text": "agent generated hello",
                "content_payload_json": {"miniprogram_library_ids": [1325]},
                "attachments_json": [],
            }

    @contextmanager
    def fake_connect():
        yield Cursor()

    monkeypatch.setattr(worker, "connect", fake_connect)

    message = worker._load_cloud_plan_recipient_message(
        {
            "plan_id": "agent_plan",
            "recipient_id": 2,
            "external_userid": "stale-column-value",
            "message_mode": "recipient_messages",
        }
    )

    assert message == {
        "cloud_plan_message_id": 77,
        "content_text": "agent generated hello",
        "content_payload_json": {"miniprogram_library_ids": [1325]},
        "attachments": [],
    }


def test_cloud_plan_retryable_failure_keeps_all_projections_retryable(next_pg_schema) -> None:
    del next_pg_schema
    plan_id = "plan_failure_sync_1"
    with get_session_factory()() as session:
        session.execute(
            text(
                """
                INSERT INTO cloud_broadcast_plans (plan_id, trace_id, session_id, operator, intent)
                VALUES (:plan_id, :plan_id, 'test-session', 'tester', 'failure sync')
                """
            ),
            {"plan_id": plan_id},
        )
        recipient = (
            session.execute(
                text(
                    """
                INSERT INTO cloud_broadcast_plan_recipients (
                    plan_id, unionid, owner_userid, display_name,
                    planned_message_count, approval_status, send_status
                )
                VALUES (
                    :plan_id, 'union_failed', 'HuangYouCan', '失败客户',
                    1, 'approved', 'queued'
                )
                RETURNING id
                """
                ),
                {"plan_id": plan_id},
            )
            .mappings()
            .one()
        )
        recipient_id = int(recipient["id"])
        session.execute(
            text(
                """
                INSERT INTO cloud_broadcast_plan_recipient_messages (
                    plan_id, recipient_id, unionid, content_text, status
                )
                VALUES (:plan_id, :recipient_id, 'union_failed', 'hello', 'queued')
                """
            ),
            {"plan_id": plan_id, "recipient_id": recipient_id},
        )
        job = (
            session.execute(
                text(
                    """
                INSERT INTO broadcast_jobs (
                    source_type, source_id, source_table, status,
                    business_domain, idempotency_key, channel, target_kind,
                    target_unionids_json, target_count, content_type, content_payload
                )
                VALUES (
                    'cloud_plan', :source_id, 'cloud_broadcast_plan_recipients', 'claimed',
                    'ai_assistant', :idempotency_key, 'wecom_private', 'unionid',
                    '["union_failed"]'::jsonb, 1, 'cloud_plan', CAST(:payload AS jsonb)
                )
                RETURNING id
                """
                ),
                {
                    "source_id": f"{plan_id}:{recipient_id}",
                    "idempotency_key": f"cloud_plan_recipient:{plan_id}:{recipient_id}",
                    "payload": json.dumps(
                        {
                            "plan_id": plan_id,
                            "recipient_id": recipient_id,
                            "unionid": "union_failed",
                            "message_mode": "recipient_messages",
                        },
                        ensure_ascii=False,
                    ),
                },
            )
            .mappings()
            .one()
        )
        job_id = int(job["id"])
        session.execute(
            text("UPDATE cloud_broadcast_plan_recipients SET broadcast_job_id = :job_id WHERE id = :recipient_id"),
            {"job_id": job_id, "recipient_id": recipient_id},
        )
        session.commit()

    repo = PostgresBroadcastQueueRepository()
    claim_token = "failure-sync-owner"
    with get_session_factory()() as session:
        session.execute(
            text("UPDATE broadcast_jobs SET claim_token = :claim_token, lease_expires_at = CURRENT_TIMESTAMP + INTERVAL '5 minutes' WHERE id = :job_id"),
            {"claim_token": claim_token, "job_id": job_id},
        )
        session.commit()
    assert repo.begin_dispatch(job_id, claim_token=claim_token, now=datetime.now(timezone.utc)) is not None
    repo.finalize_dispatch(
        job_id,
        claim_token=claim_token,
        outcome={
            "status": "failed_retryable",
            "error": "not external contact",
            "failure_type": "wecom_api_error",
            "side_effect_executed": True,
            "provider_result_received": True,
            "request_payload": {},
            "response_payload": {"errcode": 40096},
        },
    )

    with get_session_factory()() as session:
        job_row = session.execute(text("SELECT status, failure_type, last_error FROM broadcast_jobs WHERE id = :job_id"), {"job_id": job_id}).mappings().one()
        recipient_row = (
            session.execute(
                text("SELECT send_status, last_error FROM cloud_broadcast_plan_recipients WHERE id = :recipient_id"),
                {"recipient_id": recipient_id},
            )
            .mappings()
            .one()
        )
        message_row = (
            session.execute(
                text("SELECT status, last_error FROM cloud_broadcast_plan_recipient_messages WHERE recipient_id = :recipient_id"),
                {"recipient_id": recipient_id},
            )
            .mappings()
            .one()
        )

    assert job_row["status"] == "failed_retryable"
    assert job_row["failure_type"] == "wecom_api_error"
    assert "not external contact" in job_row["last_error"]
    assert recipient_row["send_status"] == "failed_retryable"
    assert "not external contact" in recipient_row["last_error"]
    assert message_row["status"] == "failed_retryable"
    assert "not external contact" in message_row["last_error"]


def test_cloud_plan_simulation_updates_all_projections_without_sent_timestamp(next_pg_schema) -> None:
    del next_pg_schema
    plan_id = "plan_simulation_sync_1"
    claim_token = "claim-simulation-1"
    with get_session_factory()() as session:
        session.execute(
            text(
                """
                INSERT INTO cloud_broadcast_plans (plan_id, trace_id, session_id, operator, intent)
                VALUES (:plan_id, :plan_id, 'test-session', 'tester', 'simulation sync')
                """
            ),
            {"plan_id": plan_id},
        )
        recipient_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO cloud_broadcast_plan_recipients (
                        plan_id, unionid, owner_userid, display_name,
                        planned_message_count, approval_status, send_status
                    )
                    VALUES (:plan_id, 'union_simulated', 'HuangYouCan', '模拟客户', 1, 'approved', 'queued')
                    RETURNING id
                    """
                ),
                {"plan_id": plan_id},
            ).scalar_one()
        )
        session.execute(
            text(
                """
                INSERT INTO cloud_broadcast_plan_recipient_messages (
                    plan_id, recipient_id, unionid, content_text, status
                )
                VALUES (:plan_id, :recipient_id, 'union_simulated', 'hello', 'queued')
                """
            ),
            {"plan_id": plan_id, "recipient_id": recipient_id},
        )
        job_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO broadcast_jobs (
                        source_type, source_id, source_table, status,
                        business_domain, idempotency_key, channel, target_kind,
                        target_unionids_json, target_count, content_type, content_payload,
                        claim_token, lease_expires_at
                    )
                    VALUES (
                        'cloud_plan', :source_id, 'cloud_broadcast_plan_recipients', 'claimed',
                        'ai_assistant', :idempotency_key, 'wecom_private', 'unionid',
                        '["union_simulated"]'::jsonb, 1, 'cloud_plan', '{}'::jsonb,
                        :claim_token, CURRENT_TIMESTAMP + INTERVAL '5 minutes'
                    )
                    RETURNING id
                    """
                ),
                {
                    "source_id": f"{plan_id}:{recipient_id}",
                    "idempotency_key": f"cloud_plan_recipient:{plan_id}:{recipient_id}",
                    "claim_token": claim_token,
                },
            ).scalar_one()
        )
        session.execute(
            text("UPDATE cloud_broadcast_plan_recipients SET broadcast_job_id = :job_id WHERE id = :recipient_id"),
            {"job_id": job_id, "recipient_id": recipient_id},
        )
        session.commit()

    repo = PostgresBroadcastQueueRepository()
    assert repo.begin_dispatch(job_id, claim_token=claim_token, now=datetime.now(timezone.utc)) is not None
    repo.finalize_dispatch(
        job_id,
        claim_token=claim_token,
        outcome={
            "status": "simulated",
            "side_effect_executed": False,
            "provider_result_received": False,
            "request_payload": {},
            "response_payload": {"mode": "fake"},
        },
    )

    with get_session_factory()() as session:
        job_row = (
            session.execute(
                text("SELECT status, sent_count, sent_at, claim_token FROM broadcast_jobs WHERE id = :job_id"),
                {"job_id": job_id},
            )
            .mappings()
            .one()
        )
        recipient_row = (
            session.execute(
                text("SELECT send_status FROM cloud_broadcast_plan_recipients WHERE id = :recipient_id"),
                {"recipient_id": recipient_id},
            )
            .mappings()
            .one()
        )
        message_row = (
            session.execute(
                text("SELECT status, sent_at FROM cloud_broadcast_plan_recipient_messages WHERE recipient_id = :recipient_id"),
                {"recipient_id": recipient_id},
            )
            .mappings()
            .one()
        )

    assert dict(job_row) == {"status": "simulated", "sent_count": 0, "sent_at": None, "claim_token": ""}
    assert recipient_row["send_status"] == "simulated"
    assert dict(message_row) == {"status": "simulated", "sent_at": None}


def test_wecom_private_adapter_canonicalizes_miniprogram_attachment(monkeypatch) -> None:
    from aicrm_next.integration_gateway.wecom_private_adapter import WeComPrivateMessageAdapter

    monkeypatch.setenv("AICRM_ENABLE_REAL_WECOM_PRIVATE_MESSAGE", "1")
    client = RecordingWeComClient()
    adapter = WeComPrivateMessageAdapter(mode="production", client_factory=lambda: client)

    result = adapter.create_private_message_task(
        {
            "sender": "HuangYouCan",
            "external_userids": ["wm_test"],
            "attachments": [
                {
                    "msgtype": "miniprogram",
                    "miniprogram": {
                        "appid": "wx_app_001",
                        "pagepath": "pages/article/article?lesson_id=abc",
                        "title": "Mini Card",
                        "thumb_media_id": "media_thumb_001",
                    },
                }
            ],
        },
        idempotency_key="adapter-canonical",
    )

    assert result["ok"] is True
    assert client.payload is not None
    assert client.payload["attachments"] == [
        {
            "msgtype": "miniprogram",
            "miniprogram": {
                "appid": "wx_app_001",
                "page": "pages/article/article?lesson_id=abc",
                "title": "Mini Card",
                "pic_media_id": "media_thumb_001",
            },
        }
    ]


@pytest.mark.skip(reason="broadcast provider dispatch retired; External Effect owns execution")
def test_campaign_private_message_job_is_dispatched(monkeypatch) -> None:
    adapter = Adapter({"ok": True, "wecom_msgid": "msg-campaign", "result": {"msgid": "msg-campaign"}})
    monkeypatch.setattr("aicrm_next.integration_gateway.wecom_private_adapter.build_wecom_private_message_adapter", lambda: adapter)
    repo = FakeRepo(
        [
            _job(
                source_type="campaign",
                source_table="campaign_members",
                source_id="2745:2745:0",
                idempotency_key="campaign_member_step:2745:2745:0",
                channel="",
                target_kind="",
                content_type="private_message",
                payload={
                    "channel": "",
                    "sender_userid": "",
                    "owner_userid": "",
                    "rendered_content": {},
                    "campaign": {"owner_userid": "HuangYouCan"},
                    "step": {"content_text": "campaign private hello"},
                    "members": [{"external_contact_id": "wm_test"}],
                },
            )
        ]
    )

    summary = run_broadcast_queue_worker(repo=repo, dispatcher=SafeSkippedBroadcastDispatcher())

    assert summary["sent_ok"] == 1
    assert {key: repo.sent[0][key] for key in ("job_id", "sent_count", "failed_count")} == {
        "job_id": 101,
        "sent_count": 1,
        "failed_count": 0,
    }
    assert repo.sent[0]["claim_token"]
    assert adapter.payload["sender"] == "HuangYouCan"
    assert adapter.payload["external_userids"] == ["wm_test"]
    assert adapter.payload["text"]["content"] == "campaign private hello"


@pytest.mark.skip(reason="broadcast provider dispatch retired; External Effect owns execution")
def test_campaign_private_message_materializes_miniprogram_attachment(monkeypatch) -> None:
    adapter = Adapter({"ok": True, "wecom_msgid": "msg-campaign-mini", "result": {"msgid": "msg-campaign-mini"}})
    attachment = {
        "msgtype": "miniprogram",
        "miniprogram": {
            "appid": "wx_app_001",
            "pagepath": "pages/article/article?lesson_id=abc",
            "title": "Mini Card",
            "thumb_media_id": "media_thumb_001",
        },
    }
    monkeypatch.setattr("aicrm_next.integration_gateway.wecom_private_adapter.build_wecom_private_message_adapter", lambda: adapter)
    monkeypatch.setattr(worker, "_resolve_private_attachments", lambda content_package: [attachment])
    repo = FakeRepo(
        [
            _job(
                source_type="campaign",
                source_table="campaign_members",
                source_id="2745:2745:0",
                idempotency_key="campaign_member_step:2745:2745:0",
                channel="",
                target_kind="",
                content_type="private_message",
                payload={
                    "channel": "",
                    "sender_userid": "",
                    "owner_userid": "",
                    "rendered_content": {},
                    "campaign": {"owner_userid": "HuangYouCan"},
                    "step": {
                        "content_text": "campaign private hello",
                        "content_payload_json": {"miniprogram_library_ids": [17]},
                    },
                    "members": [{"external_contact_id": "wm_test"}],
                },
            )
        ]
    )

    summary = run_broadcast_queue_worker(repo=repo, dispatcher=SafeSkippedBroadcastDispatcher())

    assert summary["sent_ok"] == 1
    expected_attachment = {
        "msgtype": "miniprogram",
        "miniprogram": {
            "appid": "wx_app_001",
            "page": "pages/article/article?lesson_id=abc",
            "title": "Mini Card",
            "pic_media_id": "media_thumb_001",
        },
    }
    assert adapter.payload["attachments"] == [expected_attachment]
    assert repo.sent[0]["request_payload"]["attachments"] == [expected_attachment]


@pytest.mark.skip(reason="broadcast provider dispatch retired; External Effect owns execution")
def test_campaign_private_message_allows_attachment_only_step(monkeypatch) -> None:
    adapter = Adapter({"ok": True, "wecom_msgid": "msg-campaign-mini-only", "result": {"msgid": "msg-campaign-mini-only"}})
    attachment = {
        "msgtype": "miniprogram",
        "miniprogram": {
            "appid": "wx_app_001",
            "pagepath": "pages/article/article?lesson_id=abc",
            "title": "Mini Card",
            "thumb_media_id": "media_thumb_001",
        },
    }
    monkeypatch.setattr("aicrm_next.integration_gateway.wecom_private_adapter.build_wecom_private_message_adapter", lambda: adapter)
    monkeypatch.setattr(worker, "_resolve_private_attachments", lambda content_package: [attachment])
    repo = FakeRepo(
        [
            _job(
                source_type="campaign",
                source_table="campaign_members",
                source_id="2745:2745:0",
                idempotency_key="campaign_member_step:2745:2745:0",
                channel="",
                target_kind="",
                content_type="private_message",
                payload={
                    "channel": "",
                    "sender_userid": "",
                    "owner_userid": "",
                    "rendered_content": {},
                    "campaign": {"owner_userid": "HuangYouCan"},
                    "step": {
                        "content_text": "",
                        "content_payload_json": {"miniprogram_library_ids": [17]},
                    },
                    "members": [{"external_contact_id": "wm_test"}],
                },
            )
        ]
    )

    summary = run_broadcast_queue_worker(repo=repo, dispatcher=SafeSkippedBroadcastDispatcher())

    assert summary["sent_ok"] == 1
    assert adapter.payload["attachments"] == [
        {
            "msgtype": "miniprogram",
            "miniprogram": {
                "appid": "wx_app_001",
                "page": "pages/article/article?lesson_id=abc",
                "title": "Mini Card",
                "pic_media_id": "media_thumb_001",
            },
        }
    ]
    assert "text" not in adapter.payload
    assert repo.sent[0]["request_payload"]["attachments"] == adapter.payload["attachments"]
    assert "text" not in repo.sent[0]["request_payload"]


@pytest.mark.skip(reason="broadcast provider dispatch retired; External Effect owns execution")
def test_campaign_private_message_job_with_complete_fields_is_dispatched(monkeypatch) -> None:
    adapter = Adapter({"ok": True, "wecom_msgid": "msg-campaign-complete", "result": {"msgid": "msg-campaign-complete"}})
    monkeypatch.setattr("aicrm_next.integration_gateway.wecom_private_adapter.build_wecom_private_message_adapter", lambda: adapter)
    repo = FakeRepo(
        [
            _job(
                source_type="campaign",
                source_table="campaign_members",
                content_type="private_message",
                channel="wecom_private",
                target_kind="unionid",
                payload={
                    "channel": "wecom_private",
                    "target_kind": "unionid",
                    "rendered_content": {},
                    "campaign": {"owner_userid": "HuangYouCan"},
                    "step": {"content_text": "campaign complete fields"},
                },
            )
        ]
    )

    summary = run_broadcast_queue_worker(repo=repo, dispatcher=SafeSkippedBroadcastDispatcher())

    assert summary["sent_ok"] == 1
    assert adapter.payload["text"]["content"] == "campaign complete fields"


@pytest.mark.skip(reason="material resolution belongs to durable External Effect dependencies")
def test_campaign_private_message_material_resolve_failure_is_not_sent(monkeypatch) -> None:
    adapter = Adapter({"ok": True, "wecom_msgid": "msg-should-not-send", "result": {"msgid": "msg-should-not-send"}})
    monkeypatch.setattr("aicrm_next.integration_gateway.wecom_private_adapter.build_wecom_private_message_adapter", lambda: adapter)
    monkeypatch.setattr(
        worker,
        "_resolve_private_attachments",
        lambda content_package: (_ for _ in ()).throw(RuntimeError("miniprogram_resolve_failed:id=17:missing_thumb_media_id")),
    )
    repo = FakeRepo(
        [
            _job(
                source_type="campaign",
                source_table="campaign_members",
                source_id="2745:2745:0",
                channel="",
                target_kind="",
                content_type="private_message",
                payload={
                    "channel": "",
                    "sender_userid": "",
                    "rendered_content": {},
                    "campaign": {"owner_userid": "HuangYouCan"},
                    "step": {
                        "content_text": "campaign private hello",
                        "content_payload_json": {"miniprogram_library_ids": [17]},
                    },
                },
            )
        ]
    )

    summary = run_broadcast_queue_worker(repo=repo, dispatcher=SafeSkippedBroadcastDispatcher())

    assert summary["sent_failed"] == 1
    assert repo.failed[0]["failure_type"] == "material_resolve_failed"
    assert repo.failed[0]["error"] == "miniprogram_resolve_failed:id=17:missing_thumb_media_id"
    assert not hasattr(adapter, "payload")


def test_wecom_private_sender_missing_is_validation_failed() -> None:
    repo = FakeRepo([_job(payload={"sender_userid": ""})])

    summary = run_broadcast_queue_worker(repo=repo, dispatcher=SafeSkippedBroadcastDispatcher())

    assert summary["sent_failed"] == 1
    assert repo.failed[0]["failure_type"] == "validation_failed"
    assert repo.failed[0]["error"] == "sender_userid_missing"


def test_wecom_private_target_missing_is_validation_failed() -> None:
    repo = FakeRepo([_job(target_unionids_json="[]", target_count=0, payload={"target_unionids": []})])

    run_broadcast_queue_worker(repo=repo, dispatcher=SafeSkippedBroadcastDispatcher())

    assert repo.failed[0]["failure_type"] == "validation_failed"
    assert repo.failed[0]["error"] == "target_unionids_missing"


def test_wecom_private_target_count_mismatch_is_validation_failed() -> None:
    repo = FakeRepo([_job(target_count=2)])

    run_broadcast_queue_worker(repo=repo, dispatcher=SafeSkippedBroadcastDispatcher())

    assert repo.failed[0]["failure_type"] == "validation_failed"
    assert repo.failed[0]["error"] == "target_count_mismatch"


def test_wecom_private_content_or_attachment_missing_is_validation_failed() -> None:
    repo = FakeRepo([_job(payload={"rendered_content": {"content_text": ""}})])

    run_broadcast_queue_worker(repo=repo, dispatcher=SafeSkippedBroadcastDispatcher())

    assert repo.failed[0]["failure_type"] == "validation_failed"
    assert repo.failed[0]["error"] == "content_text_or_attachment_missing"


@pytest.mark.skip(reason="provider failures are recorded by External Effect attempts")
def test_wecom_private_before_external_call_failure(monkeypatch) -> None:
    adapter = Adapter({"ok": False, "error_code": "before_external_call", "error_message": "disabled"})
    monkeypatch.setattr("aicrm_next.integration_gateway.wecom_private_adapter.build_wecom_private_message_adapter", lambda: adapter)
    repo = FakeRepo([_job()])

    run_broadcast_queue_worker(repo=repo, dispatcher=SafeSkippedBroadcastDispatcher())

    assert repo.failed[0]["failure_type"] == "before_external_call"
    assert repo.failed[0]["error"] == "disabled"


@pytest.mark.skip(reason="provider failures are recorded by External Effect attempts")
def test_wecom_private_external_known_failure(monkeypatch) -> None:
    adapter = Adapter({"ok": False, "error_code": "external_call_failed_known", "error_message": "invalid external_userid"})
    monkeypatch.setattr("aicrm_next.integration_gateway.wecom_private_adapter.build_wecom_private_message_adapter", lambda: adapter)
    repo = FakeRepo([_job()])

    run_broadcast_queue_worker(repo=repo, dispatcher=SafeSkippedBroadcastDispatcher())

    assert repo.failed[0]["failure_type"] == "external_call_failed_known"
    assert repo.failed[0]["error"] == "invalid external_userid"


@pytest.mark.skip(reason="provider failures are recorded by External Effect attempts")
def test_wecom_private_no_longer_returns_dispatcher_missing(monkeypatch) -> None:
    adapter = Adapter({"ok": False, "error_code": "external_call_unknown", "error_message": "timeout"})
    monkeypatch.setattr("aicrm_next.integration_gateway.wecom_private_adapter.build_wecom_private_message_adapter", lambda: adapter)
    repo = FakeRepo([_job()])

    summary = run_broadcast_queue_worker(repo=repo, dispatcher=SafeSkippedBroadcastDispatcher())

    assert summary["results"][0]["reason"] != "next_native_dispatcher_missing"
    assert repo.failed[0]["failure_type"] == "external_call_unknown"
