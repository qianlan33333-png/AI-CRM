from __future__ import annotations

from datetime import datetime, timezone

import pytest

import aicrm_next.background_jobs.broadcast_queue_worker as worker

from aicrm_next.background_jobs.broadcast_queue_worker import (
    SafeSkippedBroadcastDispatcher,
    run_broadcast_queue_worker,
)
from aicrm_next.platform_foundation.external_effects import (
    WECOM_MESSAGE_GROUP_SEND,
    WECOM_MESSAGE_PRIVATE_SEND,
)
from aicrm_next.platform_foundation.external_effects.repo_memory import InMemoryExternalEffectRepository
from aicrm_next.platform_foundation.external_effects.service import ExternalEffectService
from tests.test_broadcast_jobs_wecom_private_dispatch import FakeRepo, _job


def _dispatcher():
    effects = InMemoryExternalEffectRepository()
    return effects, SafeSkippedBroadcastDispatcher(ExternalEffectService(effects))


@pytest.fixture(autouse=True)
def _resolve_unionids(monkeypatch):
    monkeypatch.setattr(
        worker,
        "_resolve_private_targets_by_unionid",
        lambda unionids: ([f"wm_{item.removeprefix('union_')}" for item in unionids], []),
    )


def test_private_broadcast_is_only_delegated_to_external_effect():
    effects, dispatcher = _dispatcher()
    repo = FakeRepo([_job(execution_id="exe_broadcast_private_101")])

    summary = run_broadcast_queue_worker(
        repo=repo,
        dispatcher=dispatcher,
        now=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )

    jobs, total = effects.list_jobs({}, limit=10)
    assert total == 1
    assert summary["delegated"] == 1
    assert summary["sent_ok"] == 0
    assert repo.sent == []
    assert jobs[0].effect_type == WECOM_MESSAGE_PRIVATE_SEND
    assert jobs[0].business_type == "broadcast_job"
    assert jobs[0].business_id == "101"
    assert jobs[0].status == "queued"
    assert jobs[0].parent_execution_id == "exe_broadcast_private_101"
    assert effects.list_attempts(jobs[0].id) == []
    assert repo.delegated[0]["external_effect_job_ids"] == [jobs[0].id]
    assert repo.delegated[0]["side_effect_executed"] is False


def test_group_broadcast_is_only_delegated_to_external_effect():
    effects, dispatcher = _dispatcher()
    repo = FakeRepo(
        [
            _job(
                channel="wecom_customer_group",
                content_type="wecom_customer_group",
                target_kind="chat_id",
                target_unionids_json="[]",
                target_count=1,
                payload={
                    "channel": "wecom_customer_group",
                    "sender": "owner-1",
                    "chat_ids": ["chat-1"],
                    "text": {"content": "hello"},
                },
            )
        ]
    )

    summary = run_broadcast_queue_worker(repo=repo, dispatcher=dispatcher)
    jobs, total = effects.list_jobs({}, limit=10)

    assert total == 1
    assert summary["delegated"] == 1
    assert jobs[0].effect_type == WECOM_MESSAGE_GROUP_SEND
    assert jobs[0].lane == "wecom_bulk"
    assert effects.list_attempts(jobs[0].id) == []


def test_restarted_broadcast_delegation_reuses_same_external_effect():
    effects, dispatcher = _dispatcher()
    repo = FakeRepo([_job()])

    first = run_broadcast_queue_worker(repo=repo, dispatcher=dispatcher)
    second = run_broadcast_queue_worker(repo=repo, dispatcher=dispatcher)
    jobs, total = effects.list_jobs({}, limit=10)

    assert first["delegated"] == 1
    assert second["delegated"] == 1
    assert total == 1
    assert len(effects.list_attempts(jobs[0].id)) == 0
