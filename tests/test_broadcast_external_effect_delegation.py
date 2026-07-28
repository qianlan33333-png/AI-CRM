from __future__ import annotations

from datetime import datetime, timezone

import pytest

import aicrm_next.automation.background_jobs.broadcast_queue_worker as worker

from aicrm_next.automation.background_jobs.broadcast_queue_worker import (
    SafeSkippedBroadcastDispatcher,
    run_broadcast_queue_worker,
)
from aicrm_next.platform.platform_foundation.external_effects import (
    WECOM_MESSAGE_GROUP_SEND,
    WECOM_MESSAGE_PRIVATE_SEND,
)
from aicrm_next.platform.platform_foundation.external_effects.repo_memory import InMemoryExternalEffectRepository
from aicrm_next.platform.platform_foundation.external_effects.service import ExternalEffectService
from aicrm_next.platform.platform_foundation.external_effects.adapters import WeComPrivateMessageAdapter
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


def test_private_broadcast_materializes_all_content_package_materials_into_external_effect(monkeypatch):
    effects, dispatcher = _dispatcher()
    expected = [
        {"msgtype": "image", "image": {"media_id": "image-media-12"}},
        {
            "msgtype": "miniprogram",
            "miniprogram": {
                "appid": "wx-mini",
                "page": "pages/article/article?lesson_id=abc",
                "title": "AI 时代的新创业范式",
                "pic_media_id": "mini-cover-media-34",
            },
        },
        {"msgtype": "file", "file": {"media_id": "file-media-56"}},
        {
            "msgtype": "link",
            "link": {
                "title": "加入学习群",
                "url": "https://work.weixin.qq.com/gm/0123456789abcdef0123456789abcdef",
                "desc": "扫码后进入",
            },
        },
    ]
    monkeypatch.setattr(worker, "_content_package_attachment_resolver", lambda package: {"attachments": expected, "uploads": []})
    repo = FakeRepo(
        [
            _job(
                payload={
                    "content_payload_json": {
                        "image_library_ids": [12],
                        "miniprogram_library_ids": [34],
                        "attachment_library_ids": [56],
                        "group_invite_library_ids": [78],
                    }
                }
            )
        ]
    )

    summary = run_broadcast_queue_worker(repo=repo, dispatcher=dispatcher)
    jobs, total = effects.list_jobs({}, limit=10)

    assert total == 1
    assert summary["delegated"] == 1
    assert jobs[0].payload_json["attachments"] == expected
    assert jobs[0].payload_summary_json["attachment_count"] == 4
    assert repo.delegated[0]["side_effect_executed"] is False

    class Provider:
        def __init__(self):
            self.payloads = []

        def create_private_message_task(self, payload, *, idempotency_key=""):
            self.payloads.append(dict(payload))
            return {
                "ok": True,
                "mode": "production",
                "side_effect_executed": True,
                "exact_target_verified": True,
                "requested_external_userids": list(payload.get("external_userids") or []),
                "wecom_msgid": "msg-all-materials",
                "result": {"errcode": 0, "msgid": "msg-all-materials"},
            }

    provider = Provider()
    dispatched = WeComPrivateMessageAdapter(adapter_factory=lambda: provider).dispatch(jobs[0])

    assert dispatched.status == "succeeded"
    assert provider.payloads == [
        {
            "sender": "HuangYouCan",
            "external_userids": ["wm_test"],
            "text": {"content": "hello private"},
            "attachments": expected,
        }
    ]


def test_private_attachment_bridge_converts_image_ids_and_preserves_other_shapes(monkeypatch):
    package = {
        "image_library_ids": [12],
        "miniprogram_library_ids": [34],
        "attachment_library_ids": [56],
        "group_invite_library_ids": [78],
    }
    nested = [
        {"msgtype": "miniprogram", "miniprogram": {"appid": "wx-mini", "page": "pages/a", "title": "Mini", "pic_media_id": "mini-media"}},
        {"msgtype": "file", "file": {"media_id": "file-media"}},
        {"msgtype": "link", "link": {"title": "Join", "url": "https://work.weixin.qq.com/gm/example"}},
    ]
    monkeypatch.setattr(
        worker,
        "_content_package_attachment_resolver",
        lambda actual: {
            "attachments": [{"msgtype": "image", "image": {"media_id": "image-media"}}, *nested],
            "uploads": [],
        },
    )

    resolved = worker._resolve_private_attachments(package)

    assert resolved == [
        {"msgtype": "image", "image": {"media_id": "image-media"}},
        *nested,
    ]


def test_dynamic_miniprogram_card_uses_composed_media_port(monkeypatch):
    expected = {
        "msgtype": "miniprogram",
        "miniprogram": {
            "appid": "wx-dynamic",
            "page": "pages/article/article?rid=rid-1",
            "title": "Dynamic Mini",
            "pic_media_id": "dynamic-cover-media",
        },
    }

    class MediaPort:
        def resolve_attachment(self, card):
            assert card["cover_image_id"] == 99
            return expected

    monkeypatch.setenv("AICRM_DYNAMIC_MINIPROGRAM_CARD_V1_ENABLED", "true")
    monkeypatch.setattr(
        worker,
        "_content_package_attachment_resolver",
        lambda package: {"attachments": [MediaPort().resolve_attachment(package["dynamic_miniprogram_card"])], "uploads": []},
    )

    hydrated = worker._with_content_package_attachments(
        {
            "content_payload_json": {
                "dynamic_miniprogram_card": {
                    "appid": "wx-dynamic",
                    "pagepath": "pages/article/article",
                    "title": "Dynamic Mini",
                    "cover_image_id": 99,
                }
            }
        }
    )

    assert hydrated["attachments"] == [expected]


def test_private_broadcast_allows_attachment_only_content_package(monkeypatch):
    effects, dispatcher = _dispatcher()
    expected = [
        {
            "msgtype": "miniprogram",
            "miniprogram": {
                "appid": "wx-mini",
                "page": "pages/article/article?lesson_id=abc",
                "title": "AI 时代的新创业范式",
                "pic_media_id": "mini-cover-media-34",
            },
        }
    ]
    monkeypatch.setattr(worker, "_content_package_attachment_resolver", lambda package: {"attachments": expected, "uploads": []})
    repo = FakeRepo(
        [
            _job(
                payload={
                    "rendered_content": {"content_text": ""},
                    "content_payload_json": {"miniprogram_library_ids": [34]},
                }
            )
        ]
    )

    summary = run_broadcast_queue_worker(repo=repo, dispatcher=dispatcher)
    jobs, total = effects.list_jobs({}, limit=10)

    assert total == 1
    assert summary["delegated"] == 1
    assert jobs[0].payload_json["content_text"] == ""
    assert jobs[0].payload_json["attachments"] == expected


def test_private_broadcast_material_resolution_failure_blocks_effect_creation(monkeypatch):
    effects, dispatcher = _dispatcher()
    monkeypatch.setattr(
        worker,
        "_content_package_attachment_resolver",
        lambda package: (_ for _ in ()).throw(RuntimeError("miniprogram_resolve_failed:id=34:disabled")),
    )
    repo = FakeRepo([_job(payload={"content_payload_json": {"miniprogram_library_ids": [34]}})])

    summary = run_broadcast_queue_worker(repo=repo, dispatcher=dispatcher)
    _, total = effects.list_jobs({}, limit=10)

    assert total == 0
    assert summary["sent_failed"] == 1
    assert repo.failed[0]["failure_type"] == "material_resolve_failed"
    assert repo.failed[0]["side_effect_executed"] is False


def test_private_broadcast_rejects_more_than_nine_direct_attachments():
    effects, dispatcher = _dispatcher()
    repo = FakeRepo(
        [
            _job(
                payload={
                    "attachments": [
                        {"msgtype": "file", "file": {"media_id": f"file-{index}"}}
                        for index in range(10)
                    ]
                }
            )
        ]
    )

    summary = run_broadcast_queue_worker(repo=repo, dispatcher=dispatcher)
    _, total = effects.list_jobs({}, limit=20)

    assert total == 0
    assert summary["sent_failed"] == 1
    assert repo.failed[0]["failure_type"] == "material_resolve_failed"
    assert repo.failed[0]["error"] == "private_message_attachments_exceed_limit"


def test_private_broadcast_plans_media_dependencies_before_releasing_final_effect(monkeypatch):
    effects, dispatcher = _dispatcher()
    attachments = [
        {"msgtype": "image", "image": {"media_dependency_key": "image:12:image"}},
        {
            "msgtype": "miniprogram",
            "miniprogram": {
                "appid": "wx-mini",
                "page": "pages/a",
                "title": "Mini",
                "pic_media_dependency_key": "miniprogram:34:image",
            },
        },
        {"msgtype": "file", "file": {"media_dependency_key": "attachment:56:attachment"}},
        {"msgtype": "link", "link": {"title": "Join", "url": "https://work.weixin.qq.com/gm/example"}},
    ]
    uploads = [
        {"material_key": "image:12:image", "material_kind": "image", "material_id": 12, "upload_kind": "image"},
        {"material_key": "miniprogram:34:image", "material_kind": "miniprogram", "material_id": 34, "upload_kind": "image"},
        {
            "material_key": "attachment:56:attachment",
            "material_kind": "attachment",
            "material_id": 56,
            "upload_kind": "attachment",
        },
    ]
    monkeypatch.setattr(
        worker,
        "_content_package_attachment_resolver",
        lambda package: {"attachments": attachments, "uploads": uploads},
    )
    repo = FakeRepo([_job(payload={"content_payload_json": {"image_library_ids": [12]}})])

    summary = run_broadcast_queue_worker(repo=repo, dispatcher=dispatcher)
    jobs, total = effects.list_jobs({}, limit=10)

    assert total == 4
    assert summary["delegated"] == 1
    final = next(job for job in jobs if job.business_type == "broadcast_job")
    dependencies = [job for job in jobs if job.business_type == "broadcast_material_dependency"]
    assert final.status == "planned"
    assert final.payload_json["attachments"] == attachments
    assert len(dependencies) == 3
    assert {job.effect_type for job in dependencies} == {"wecom.media.upload"}
    assert {job.status for job in dependencies} == {"queued"}
    assert repo.delegated[0]["external_effect_job_ids"][0] == final.id


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
