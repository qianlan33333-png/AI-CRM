from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from aicrm_next.platform_foundation.external_effects import (
    ExternalEffectService,
    WECOM_MEDIA_UPLOAD,
    WECOM_MESSAGE_GROUP_SEND,
    reset_external_effect_fixture_state,
)


@pytest.fixture(autouse=True)
def reset_external_effects():
    reset_external_effect_fixture_state()


class FakeGroupOpsRepo:
    def __init__(
        self,
        *,
        plans: list[dict[str, Any]] | None = None,
        groups: dict[int, list[dict[str, Any]]] | None = None,
        nodes: dict[int, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.plans = plans or []
        self.groups = groups or {}
        self.nodes = nodes or {}

    def list_plans(self, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        rows = [
            plan
            for plan in self.plans
            if (not filters.get("plan_type") or plan.get("plan_type") == filters.get("plan_type"))
            and (not filters.get("status") or plan.get("status") == filters.get("status"))
        ]
        return list(rows), len(rows)

    def list_bound_groups(self, plan_id: int) -> list[dict[str, Any]]:
        return list(self.groups.get(int(plan_id), []))

    def list_nodes(self, plan_id: int) -> list[dict[str, Any]]:
        return list(self.nodes.get(int(plan_id), []))


def _plan(**overrides):
    return {
        "id": 1,
        "plan_type": "standard",
        "status": "active",
        "owner_userid": "owner_001",
        "created_at": "2026-05-28T09:00:00+08:00",
        **overrides,
    }


def _group(chat_id="wrOgAAA001", **overrides):
    return {
        "chat_id": chat_id,
        "status": "active",
        "created_at": "2026-05-28T09:10:00+08:00",
        **overrides,
    }


def _node(**overrides):
    return {
        "id": 10,
        "plan_id": 1,
        "day_index": 1,
        "scheduled_time": "10:00",
        "trigger_time_label": "10:00",
        "action_title": "Welcome",
        "text_content": "hello group",
        "attachments": [],
        "status": "active",
        **overrides,
    }


def _run(repo, now=None):
    from aicrm_next.automation_engine.group_ops.scheduler import run_group_ops_due_scheduler

    return run_group_ops_due_scheduler(
        repo=repo,
        now=now or datetime(2026, 5, 28, 10, 1, tzinfo=timezone.utc),
        operator="pytest-scheduler",
    )


def _wecom_group_jobs():
    return ExternalEffectService().list_jobs({"effect_type": WECOM_MESSAGE_GROUP_SEND}, limit=20)[0]


def _wecom_media_jobs():
    return ExternalEffectService().list_jobs({"effect_type": WECOM_MEDIA_UPLOAD}, limit=20)[0]


def test_active_standard_plan_due_node_enqueues_external_effect_job():
    repo = FakeGroupOpsRepo(plans=[_plan()], groups={1: [_group()]}, nodes={1: [_node()]})

    summary = _run(repo, now=datetime(2026, 5, 28, 2, 1, tzinfo=timezone.utc))

    jobs = _wecom_group_jobs()
    assert summary["group_ops_enqueued_jobs"] == 0
    assert summary["group_ops_external_effect_jobs"] == 1
    assert len(jobs) == 1
    assert jobs[0].scheduled_at == "2026-05-28T02:00:00Z"
    assert jobs[0].payload_json["chat_ids"] == ["wrOgAAA001"]
    assert jobs[0].payload_json["content_payload"]["channel"] == "wecom_customer_group"
    assert jobs[0].payload_json["content_payload"]["sender"] == "owner_001"
    assert jobs[0].payload_json["content_payload"]["text"]["content"] == "hello group"
    assert "attachments" in jobs[0].payload_json["content_payload"]


def test_group_ops_scheduler_uses_external_effect_payload_contract():
    from aicrm_next.automation_engine.group_ops.scheduler import run_group_ops_due_scheduler

    repo = FakeGroupOpsRepo(
        plans=[_plan(owner_userid="owner_live")],
        groups={1: [_group("chat_001"), _group("chat_002")]},
        nodes={
            1: [
                _node(
                    text_content="hello exact groups",
                    attachments=[{"msgtype": "image", "image": {"media_id": "img_001"}}],
                )
            ]
        },
    )

    summary = run_group_ops_due_scheduler(
        repo=repo,
        now=datetime(2026, 5, 28, 2, 1, tzinfo=timezone.utc),
        operator="pytest-scheduler",
    )

    jobs = _wecom_group_jobs()
    assert summary["group_ops_enqueued_jobs"] == 0
    assert summary["group_ops_external_effect_jobs"] == 1
    assert jobs[0].payload_json["chat_ids"] == ["chat_001", "chat_002"]
    assert jobs[0].payload_json["content_payload"]["channel"] == "wecom_customer_group"
    assert jobs[0].payload_json["content_payload"]["sender"] == "owner_live"
    assert jobs[0].payload_json["content_payload"]["text"]["content"] == "hello exact groups"
    assert jobs[0].payload_json["content_payload"]["attachments"] == [{"msgtype": "image", "image": {"media_id": "img_001"}}]


def test_group_ops_due_at_uses_business_timezone():
    repo = FakeGroupOpsRepo(
        plans=[_plan(created_at="2026-05-29T05:05:00+00:00")],
        groups={1: [_group(created_at="2026-05-29T05:05:00+00:00")]},
        nodes={1: [_node(scheduled_time="13:00", trigger_time_label="13:00")]},
    )

    summary = _run(repo, now=datetime(2026, 5, 29, 5, 10, tzinfo=timezone.utc))

    jobs = _wecom_group_jobs()
    assert summary["group_ops_enqueued_jobs"] == 0
    assert summary["group_ops_external_effect_jobs"] == 1
    assert jobs[0].scheduled_at == "2026-05-29T05:00:00Z"


def test_group_ops_future_node_uses_business_timezone():
    repo = FakeGroupOpsRepo(
        plans=[_plan(created_at="2026-05-29T05:05:00+00:00")],
        groups={1: [_group(created_at="2026-05-29T05:05:00+00:00")]},
        nodes={1: [_node(scheduled_time="14:00", trigger_time_label="14:00")]},
    )

    summary = _run(repo, now=datetime(2026, 5, 29, 5, 10, tzinfo=timezone.utc))

    assert summary["group_ops_enqueued_jobs"] == 0
    assert summary["group_ops_skipped_future"] == 1

def test_group_ops_day_index_uses_business_date():
    repo = FakeGroupOpsRepo(
        plans=[_plan(created_at="2026-05-29T05:05:00+00:00")],
        groups={1: [_group(created_at="2026-05-29T05:05:00+00:00")]},
        nodes={1: [_node(day_index=2, scheduled_time="13:00", trigger_time_label="13:00")]},
    )

    summary = _run(repo, now=datetime(2026, 5, 30, 5, 10, tzinfo=timezone.utc))

    jobs = _wecom_group_jobs()
    assert summary["group_ops_enqueued_jobs"] == 0
    assert summary["group_ops_external_effect_jobs"] == 1
    assert jobs[0].scheduled_at == "2026-05-30T05:00:00Z"


def test_future_due_node_does_not_enqueue():
    repo = FakeGroupOpsRepo(plans=[_plan()], groups={1: [_group()]}, nodes={1: [_node(scheduled_time="20:00")]})

    summary = _run(repo, now=datetime(2026, 5, 28, 2, 1, tzinfo=timezone.utc))

    assert summary["group_ops_enqueued_jobs"] == 0
    assert summary["group_ops_skipped_future"] == 1

def test_disabled_plan_does_not_enqueue():
    repo = FakeGroupOpsRepo(plans=[_plan(status="disabled")], groups={1: [_group()]}, nodes={1: [_node()]})

    summary = _run(repo, now=datetime(2026, 5, 28, 2, 1, tzinfo=timezone.utc))

    assert summary["group_ops_scanned_plans"] == 0

def test_draft_or_disabled_node_does_not_enqueue():
    repo = FakeGroupOpsRepo(
        plans=[_plan()],
        groups={1: [_group()]},
        nodes={1: [_node(id=10, status="draft"), _node(id=11, status="disabled")]},
    )

    summary = _run(repo, now=datetime(2026, 5, 28, 2, 1, tzinfo=timezone.utc))

    assert summary["group_ops_enqueued_jobs"] == 0

def test_plan_without_bound_groups_does_not_enqueue():
    repo = FakeGroupOpsRepo(plans=[_plan()], groups={1: []}, nodes={1: [_node()]})

    summary = _run(repo, now=datetime(2026, 5, 28, 2, 1, tzinfo=timezone.utc))

    assert summary["group_ops_enqueued_jobs"] == 0

def test_scheduler_is_idempotent_on_repeated_runs():
    repo = FakeGroupOpsRepo(plans=[_plan()], groups={1: [_group()]}, nodes={1: [_node()]})

    first = _run(repo, now=datetime(2026, 5, 28, 2, 1, tzinfo=timezone.utc))
    second = _run(repo, now=datetime(2026, 5, 28, 2, 1, tzinfo=timezone.utc))

    assert first["group_ops_enqueued_jobs"] == 0
    assert first["group_ops_external_effect_jobs"] == 1
    assert second["group_ops_enqueued_jobs"] == 0
    assert second["group_ops_external_effect_jobs"] == 0
    assert second["group_ops_skipped_duplicate"] == 1
    assert len(_wecom_group_jobs()) == 1


def test_node_revision_creates_new_graph_and_cancels_old_pre_provider_effect():
    repo = FakeGroupOpsRepo(
        plans=[_plan(updated_at="2026-05-28T01:00:00Z")],
        groups={1: [_group(updated_at="2026-05-28T01:00:00Z")]},
        nodes={1: [_node(text_content="old content", updated_at="2026-05-28T01:00:00Z")]},
    )
    now = datetime(2026, 5, 28, 2, 1, tzinfo=timezone.utc)

    first = _run(repo, now=now)
    repo.nodes[1] = [_node(text_content="new content", updated_at="2026-05-28T01:05:00Z")]
    second = _run(repo, now=now)
    jobs = _wecom_group_jobs()

    assert first["group_ops_external_effect_jobs"] == 1
    assert second["group_ops_external_effect_jobs"] == 1
    status_by_content = {
        job.payload_json["content_payload"]["text"]["content"]: job.status
        for job in jobs
    }
    assert status_by_content == {"old content": "cancelled", "new content": "queued"}


def test_group_binding_revision_replaces_old_target_set_without_reusing_graph():
    repo = FakeGroupOpsRepo(
        plans=[_plan(updated_at="2026-05-28T01:00:00Z")],
        groups={
            1: [
                _group("chat_001", updated_at="2026-05-28T01:00:00Z"),
                _group("chat_002", updated_at="2026-05-28T01:00:00Z"),
            ]
        },
        nodes={1: [_node(updated_at="2026-05-28T01:00:00Z")]},
    )
    now = datetime(2026, 5, 28, 2, 1, tzinfo=timezone.utc)

    _run(repo, now=now)
    repo.groups[1] = [_group("chat_001", updated_at="2026-05-28T01:05:00Z")]
    revised = _run(repo, now=now)
    jobs = _wecom_group_jobs()

    assert revised["group_ops_external_effect_jobs"] == 1
    status_by_targets = {
        tuple(job.payload_json["chat_ids"]): job.status
        for job in jobs
    }
    assert status_by_targets == {
        ("chat_001", "chat_002"): "cancelled",
        ("chat_001",): "queued",
    }

def test_group_ops_scheduler_idempotent_after_timezone_fix():
    repo = FakeGroupOpsRepo(
        plans=[_plan(created_at="2026-05-29T05:05:00+00:00")],
        groups={1: [_group(created_at="2026-05-29T05:05:00+00:00")]},
        nodes={1: [_node(scheduled_time="13:00", trigger_time_label="13:00")]},
    )

    first = _run(repo, now=datetime(2026, 5, 29, 5, 10, tzinfo=timezone.utc))
    second = _run(repo, now=datetime(2026, 5, 29, 5, 10, tzinfo=timezone.utc))

    assert first["group_ops_enqueued_jobs"] == 0
    assert first["group_ops_external_effect_jobs"] == 1
    assert second["group_ops_enqueued_jobs"] == 0
    assert second["group_ops_external_effect_jobs"] == 0
    assert second["group_ops_skipped_duplicate"] == 1
    assert len(_wecom_group_jobs()) == 1

def test_groups_with_same_due_at_merge_into_one_job():
    repo = FakeGroupOpsRepo(
        plans=[_plan()],
        groups={1: [_group("wrOgAAA001"), _group("wrOgAAA002")]},
        nodes={1: [_node()]},
    )

    summary = _run(repo, now=datetime(2026, 5, 28, 2, 1, tzinfo=timezone.utc))

    jobs = _wecom_group_jobs()
    assert summary["group_ops_enqueued_jobs"] == 0
    assert summary["group_ops_external_effect_jobs"] == 1
    assert jobs[0].payload_json["chat_ids"] == ["wrOgAAA001", "wrOgAAA002"]
    assert jobs[0].payload_json["content_payload"]["chat_ids"] == ["wrOgAAA001", "wrOgAAA002"]


def test_groups_with_different_due_at_do_not_merge():
    repo = FakeGroupOpsRepo(
        plans=[_plan()],
        groups={
            1: [
                _group("wrOgAAA001", created_at="2026-05-27T09:10:00+08:00"),
                _group("wrOgAAA002", created_at="2026-05-28T09:10:00+08:00"),
            ]
        },
        nodes={1: [_node()]},
    )

    summary = _run(repo, now=datetime(2026, 5, 28, 2, 1, tzinfo=timezone.utc))

    jobs = sorted(_wecom_group_jobs(), key=lambda job: job.id)
    assert summary["group_ops_enqueued_jobs"] == 0
    assert summary["group_ops_external_effect_jobs"] == 2
    assert [job.payload_json["chat_ids"] for job in jobs] == [["wrOgAAA001"], ["wrOgAAA002"]]


def test_group_ops_content_package_text_enqueues():
    repo = FakeGroupOpsRepo(
        plans=[_plan()],
        groups={1: [_group()]},
        nodes={
            1: [
                _node(
                    text_content="",
                    content_package_json={
                        "content_text": "package hello group",
                        "image_library_ids": [],
                        "miniprogram_library_ids": [],
                        "attachment_library_ids": [],
                    },
                )
            ]
        },
    )

    summary = _run(repo, now=datetime(2026, 5, 28, 2, 1, tzinfo=timezone.utc))

    jobs = _wecom_group_jobs()
    assert summary["group_ops_enqueued_jobs"] == 0
    assert summary["group_ops_external_effect_jobs"] == 1
    assert jobs[0].payload_json["content_payload"]["text"]["content"] == "package hello group"


def test_group_ops_content_package_text_and_durable_media_dependencies_enqueue(monkeypatch):
    from aicrm_next.automation_engine.group_ops import scheduler

    monkeypatch.setattr(scheduler, "production_data_ready", lambda: True)
    repo = FakeGroupOpsRepo(
        plans=[_plan()],
        groups={1: [_group()]},
        nodes={
            1: [
                _node(
                    text_content="",
                    attachments=[],
                    content_package_json={
                        "content_text": "package hello group",
                        "image_library_ids": [1],
                        "miniprogram_library_ids": [],
                        "attachment_library_ids": [2],
                    },
                )
            ]
        },
    )

    summary = _run(repo, now=datetime(2026, 5, 28, 2, 1, tzinfo=timezone.utc))

    jobs = _wecom_group_jobs()
    assert summary["group_ops_enqueued_jobs"] == 0
    assert summary["group_ops_external_effect_jobs"] == 1
    content_payload = jobs[0].payload_json["content_payload"]
    assert content_payload["text"]["content"] == "package hello group"
    assert content_payload["sender"] == "owner_001"
    assert content_payload["channel"] == "wecom_customer_group"
    assert content_payload["attachments"] == [
        {"msgtype": "image", "image": {"media_dependency_key": "image-library:1"}},
        {"msgtype": "file", "file": {"media_dependency_key": "attachment-library:2"}},
    ]
    assert len(_wecom_media_jobs()) == 2


def test_group_ops_media_only_package_plans_upload_before_final_effect(monkeypatch):
    from aicrm_next.automation_engine.group_ops import scheduler

    monkeypatch.setattr(scheduler, "production_data_ready", lambda: True)
    repo = FakeGroupOpsRepo(
        plans=[_plan()],
        groups={1: [_group()]},
        nodes={
            1: [
                _node(
                    text_content="",
                    attachments=[],
                    content_package_json={
                        "content_text": "",
                        "image_library_ids": [404],
                        "miniprogram_library_ids": [],
                        "attachment_library_ids": [],
                    },
                )
            ]
        },
    )

    summary = _run(repo, now=datetime(2026, 5, 28, 2, 1, tzinfo=timezone.utc))

    jobs = _wecom_group_jobs()
    assert summary["group_ops_external_effect_jobs"] == 1
    assert summary["errors"] == []
    assert jobs[0].status == "planned"
    assert jobs[0].payload_json["content_payload"]["attachments"] == [
        {"msgtype": "image", "image": {"media_dependency_key": "image-library:404"}},
    ]
    assert len(_wecom_media_jobs()) == 1


def test_group_ops_text_and_attachment_enqueues():
    repo = FakeGroupOpsRepo(
        plans=[_plan()],
        groups={1: [_group()]},
        nodes={1: [_node(attachments=[{"msgtype": "file", "file": {"media_id": "file-media-001"}}])]},
    )

    summary = _run(repo, now=datetime(2026, 5, 28, 2, 1, tzinfo=timezone.utc))

    jobs = _wecom_group_jobs()
    assert summary["group_ops_enqueued_jobs"] == 0
    assert summary["group_ops_external_effect_jobs"] == 1
    assert jobs[0].payload_json["content_payload"]["text"]["content"] == "hello group"
    assert jobs[0].payload_json["content_payload"]["attachments"] == [{"msgtype": "file", "file": {"media_id": "file-media-001"}}]


def test_group_ops_bad_node_does_not_block_good_node(monkeypatch):
    from aicrm_next.automation_engine.group_ops import scheduler

    def fail_resolve(content_package):
        image_ids = content_package.get("image_library_ids") or []
        if not image_ids:
            return [], []
        image_id = int(image_ids[0] or 0)
        raise RuntimeError(f"image_library_resolve_failed:id={image_id}:missing image {image_id}")

    monkeypatch.setattr(scheduler, "resolve_group_ops_content_package_materials", fail_resolve)
    monkeypatch.setattr(scheduler, "production_data_ready", lambda: False)
    repo = FakeGroupOpsRepo(
        plans=[_plan()],
        groups={1: [_group()]},
        nodes={
            1: [
                _node(
                    id=9,
                    text_content="",
                    content_package_json={
                        "content_text": "",
                        "image_library_ids": [404],
                        "miniprogram_library_ids": [],
                        "attachment_library_ids": [],
                    },
                ),
                _node(id=10, text_content="good node"),
            ]
        },
    )

    summary = _run(repo, now=datetime(2026, 5, 28, 2, 1, tzinfo=timezone.utc))

    jobs = _wecom_group_jobs()
    assert summary["group_ops_enqueued_jobs"] == 0
    assert summary["group_ops_external_effect_jobs"] == 1
    assert jobs[0].payload_json["content_payload"]["text"]["content"] == "good node"
    assert summary["errors"] == [
        {
            "scope": "group_ops_node",
            "plan_id": 1,
            "node_id": 9,
            "error": "image_library_resolve_failed:id=404:missing image 404",
        }
    ]


def test_group_ops_unresolvable_content_package_records_node_error(monkeypatch):
    from aicrm_next.automation_engine.group_ops import scheduler

    def fake_resolve(content_package):
        if not (content_package.get("image_library_ids") or []):
            return [], []
        raise RuntimeError("image_library_resolve_failed:id=404:empty_media_id")

    monkeypatch.setattr(
        scheduler,
        "resolve_group_ops_content_package_materials",
        fake_resolve,
    )
    monkeypatch.setattr(scheduler, "production_data_ready", lambda: False)
    repo = FakeGroupOpsRepo(
        plans=[_plan()],
        groups={1: [_group()]},
        nodes={
            1: [
                _node(
                    id=9,
                    text_content="",
                    content_package_json={
                        "content_text": "",
                        "image_library_ids": [404],
                        "miniprogram_library_ids": [],
                        "attachment_library_ids": [],
                    },
                ),
                _node(id=10, text_content="good node"),
            ]
        },
    )

    summary = _run(repo, now=datetime(2026, 5, 28, 2, 1, tzinfo=timezone.utc))

    jobs = _wecom_group_jobs()
    assert summary["group_ops_enqueued_jobs"] == 0
    assert summary["group_ops_external_effect_jobs"] == 1
    assert jobs[0].payload_json["content_payload"]["text"]["content"] == "good node"
    assert summary["errors"][0]["scope"] == "group_ops_node"
    assert summary["errors"][0]["node_id"] == 9
    assert "image_library_resolve_failed:id=404" in summary["errors"][0]["error"]


def test_scheduler_has_no_check_then_plan_duplicate_checker():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "aicrm_next/automation_engine/group_ops/scheduler.py").read_text(encoding="utf-8")

    assert "wecom_ability" + "_service" not in source
    assert "broadcast_jobs.service" not in source
    assert "legacy_flask_facade" not in source
    assert "duplicate_checker" not in source
