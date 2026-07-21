from __future__ import annotations

import pytest

from tests.group_ops_test_helpers import group_ops_repo  # noqa: F401, F811


def test_group_ops_plan_actions_disable_enable_and_archive(group_ops_repo):  # noqa: F811
    from aicrm_next.automation_engine.group_ops.application import (
        ArchiveGroupOpsPlanCommand,
        DisableGroupOpsPlanCommand,
        EnableGroupOpsPlanCommand,
    )

    disabled = DisableGroupOpsPlanCommand(repo=group_ops_repo)(1)
    assert disabled["item"]["status"] == "disabled"

    enabled = EnableGroupOpsPlanCommand(repo=group_ops_repo)(1)
    assert enabled["item"]["status"] == "active"

    disabled_again = DisableGroupOpsPlanCommand(repo=group_ops_repo)(1)
    assert disabled_again["item"]["status"] == "disabled"

    archived = ArchiveGroupOpsPlanCommand(repo=group_ops_repo)(1)
    assert archived["archived"] is True
    assert archived["item"]["status"] == "disabled"
    assert group_ops_repo.get_plan(1) is None


def test_disable_plan_invalidates_pre_materialized_effects_before_state_change(group_ops_repo):  # noqa: F811
    from aicrm_next.automation_engine.group_ops.application import DisableGroupOpsPlanCommand

    calls: list[dict] = []

    class _EffectGraphs:
        def cancel_plan(self, plan_id, *, actor, reason, node_id=None):
            calls.append(
                {
                    "plan_id": plan_id,
                    "actor": actor,
                    "reason": reason,
                    "node_id": node_id,
                    "plan_status_during_cancel": group_ops_repo.get_plan(plan_id)["status"],
                }
            )
            return {"ok": True, "matched_graph_count": 1, "cancelled_job_ids": [101]}

    response = DisableGroupOpsPlanCommand(
        repo=group_ops_repo,
        effect_graph_repo=_EffectGraphs(),
    )(1, operator="operator-1")

    assert calls == [
        {
            "plan_id": 1,
            "actor": "operator-1",
            "reason": "group_ops_plan_disabled",
            "node_id": None,
            "plan_status_during_cancel": "active",
        }
    ]
    assert response["item"]["status"] == "disabled"
    assert response["effect_invalidation"]["cancelled_job_ids"] == [101]


def test_failed_plan_update_does_not_invalidate_live_effects(group_ops_repo, monkeypatch):  # noqa: F811
    from aicrm_next.automation_engine.group_ops.application import UpdateGroupOpsPlanCommand
    from aicrm_next.automation_engine.group_ops.dto import GroupOpsPlanUpdateRequest
    from aicrm_next.shared.errors import ContractError

    cancelled_plan_ids: list[int] = []

    class _EffectGraphs:
        def cancel_plan(self, plan_id, *, actor, reason, node_id=None):
            del actor, reason, node_id
            cancelled_plan_ids.append(int(plan_id))
            return {"ok": True}

    def _fail_update(_plan_id, _payload):
        raise ContractError("duplicate plan code")

    original = group_ops_repo.get_plan(1)
    monkeypatch.setattr(group_ops_repo, "update_plan", _fail_update)

    with pytest.raises(ContractError, match="duplicate plan code"):
        UpdateGroupOpsPlanCommand(
            repo=group_ops_repo,
            effect_graph_repo=_EffectGraphs(),
        )(1, GroupOpsPlanUpdateRequest(plan_code="group_plan_002"))

    assert cancelled_plan_ids == []
    assert group_ops_repo.get_plan(1) == original


def test_domain_reuses_unified_attachment_validation():
    from aicrm_next.automation_engine.group_ops.domain import normalize_message_content
    from aicrm_next.shared.errors import ContractError

    normalized = normalize_message_content(
        text="课程入口",
        attachments=[
            {
                "msgtype": "miniprogram",
                "miniprogram": {
                    "appid": "wx123",
                    "page": "/pages/course/today",
                    "title": "课程入口",
                    "pic_media_id": "MEDIA_ID",
                },
            }
        ],
        sender="owner_001",
    )
    assert normalized["sender"] == "owner_001"
    assert normalized["attachments"][0]["miniprogram"]["pic_media_id"] == "MEDIA_ID"

    with pytest.raises(ContractError, match="pic_media_id"):
        normalize_message_content(
            text="",
            attachments=[
                {
                    "msgtype": "miniprogram",
                    "miniprogram": {"appid": "wx123", "page": "/pages/course/today", "title": "课程入口"},
                }
            ],
        )
    with pytest.raises(ContractError, match="content"):
        normalize_message_content(text="", attachments=[])


def test_group_ops_native_message_content_text_only():
    from aicrm_next.automation_engine.group_ops.domain import normalize_message_content

    normalized = normalize_message_content(text="hello", attachments=[])

    assert normalized["text"]["content"] == "hello"
    assert "attachments" not in normalized or normalized["attachments"] == []


def test_group_ops_native_message_content_file_attachment():
    from aicrm_next.automation_engine.group_ops.domain import normalize_message_content

    normalized = normalize_message_content(
        text="",
        attachments=[{"msgtype": "file", "file": {"media_id": "file-media-001"}}],
    )

    assert normalized["attachments"] == [{"msgtype": "file", "file": {"media_id": "file-media-001"}}]


def test_group_ops_native_message_content_miniprogram_aliases():
    from aicrm_next.automation_engine.group_ops.domain import normalize_message_content

    normalized = normalize_message_content(
        text="课程入口",
        attachments=[
            {
                "msgtype": "miniprogram",
                "miniprogram": {
                    "appid": "wx123",
                    "pagepath": "/pages/course/today",
                    "title": "课程入口",
                    "thumb_media_id": "thumb-media-001",
                },
            }
        ],
    )

    assert normalized["attachments"] == [
        {
            "msgtype": "miniprogram",
            "miniprogram": {
                "appid": "wx123",
                "page": "/pages/course/today",
                "title": "课程入口",
                "pic_media_id": "thumb-media-001",
            },
        }
    ]


def test_group_ops_native_message_content_missing_miniprogram_fields():
    from aicrm_next.automation_engine.group_ops.domain import normalize_message_content
    from aicrm_next.shared.errors import ContractError

    base = {"appid": "wx123", "page": "/pages/course/today", "title": "课程入口", "pic_media_id": "pic-media-001"}
    for field, expected in (
        ("appid", "appid"),
        ("page", "page"),
        ("title", "title"),
        ("pic_media_id", "pic_media_id"),
    ):
        payload = dict(base)
        payload.pop(field)
        with pytest.raises(ContractError, match=expected):
            normalize_message_content(text="", attachments=[{"msgtype": "miniprogram", "miniprogram": payload}])


def test_group_ops_native_message_content_image_media_ids():
    from aicrm_next.automation_engine.group_ops.domain import normalize_message_content

    normalized = normalize_message_content(text="", image_media_ids=["img1", "img2", "img3"])

    assert normalized["attachments"] == [
        {"msgtype": "image", "image": {"media_id": "img1"}},
        {"msgtype": "image", "image": {"media_id": "img2"}},
        {"msgtype": "image", "image": {"media_id": "img3"}},
    ]


def test_group_ops_native_message_content_rejects_too_many_images():
    from aicrm_next.automation_engine.group_ops.domain import normalize_message_content
    from aicrm_next.shared.errors import ContractError

    with pytest.raises(ContractError, match="at most 3 images"):
        normalize_message_content(text="", image_media_ids=["img1", "img2", "img3", "img4"])


def test_group_ops_native_message_content_rejects_too_many_total_attachments():
    from aicrm_next.automation_engine.group_ops.domain import normalize_message_content
    from aicrm_next.shared.errors import ContractError

    attachments = [{"msgtype": "file", "file": {"media_id": f"file-{index}"}} for index in range(9)]

    with pytest.raises(ContractError, match="at most 9 attachments"):
        normalize_message_content(text="", attachments=attachments, image_media_ids=["img1"])


def test_group_ops_native_message_content_rejects_unsupported_attachment_msgtype():
    from aicrm_next.automation_engine.group_ops.domain import normalize_message_content
    from aicrm_next.shared.errors import ContractError

    with pytest.raises(ContractError, match="attachments msgtype is not supported"):
        normalize_message_content(text="", attachments=[{"msgtype": "video", "video": {"media_id": "video-1"}}])


def test_group_ops_native_message_content_accepts_group_invite_link():
    from aicrm_next.automation_engine.group_ops.domain import normalize_message_content

    normalized = normalize_message_content(
        text="",
        attachments=[{
            "msgtype": "link",
            "link": {
                "title": "点击加入体验群",
                "url": "https://work.weixin.qq.com/gm/0123456789abcdef0123456789abcdef",
                "desc": "进群领取资料",
            },
        }],
    )

    assert normalized["attachments"][0]["msgtype"] == "link"
    assert normalized["attachments"][0]["link"]["title"] == "点击加入体验群"


def test_group_ops_node_payload_draft_allows_empty_content():
    from aicrm_next.automation_engine.group_ops.domain import normalize_node_payload

    normalized = normalize_node_payload(
        {
            "day_index": 1,
            "scheduled_time": "10:00",
            "action_title": "Draft node",
            "status": "draft",
            "text_content": "",
            "attachments": [],
        }
    )

    assert normalized["attachments"] == []
    assert normalized["status"] == "draft"


def test_group_ops_node_payload_active_empty_content_fails():
    from aicrm_next.automation_engine.group_ops.domain import normalize_node_payload
    from aicrm_next.shared.errors import ContractError

    with pytest.raises(ContractError, match="content"):
        normalize_node_payload(
            {
                "day_index": 1,
                "scheduled_time": "10:00",
                "action_title": "Active node",
                "status": "active",
                "text_content": "",
                "attachments": [],
            }
        )


def test_build_node_group_message_content_with_resolved_materials():
    from aicrm_next.automation_engine.group_ops.domain import build_node_group_message_content

    content = build_node_group_message_content(
        node={"text_content": "hello group", "attachments": []},
        sender="owner_001",
        resolved_attachments=[
            {"msgtype": "file", "file": {"media_id": "file-media-001"}},
            {
                "msgtype": "miniprogram",
                "miniprogram": {
                    "appid": "wx123",
                    "page": "/pages/course/today",
                    "title": "课程入口",
                    "pic_media_id": "pic-media-001",
                },
            },
        ],
        resolved_image_media_ids=["img1"],
    )

    assert content["text"]["content"] == "hello group"
    assert content["sender"] == "owner_001"
    assert content["attachments"] == [
        {"msgtype": "file", "file": {"media_id": "file-media-001"}},
        {
            "msgtype": "miniprogram",
            "miniprogram": {
                "appid": "wx123",
                "page": "/pages/course/today",
                "title": "课程入口",
                "pic_media_id": "pic-media-001",
            },
        },
        {"msgtype": "image", "image": {"media_id": "img1"}},
    ]


def test_webhook_endpoint_key_is_non_secret_and_unique():
    from aicrm_next.automation_engine.group_ops.domain import generate_webhook_key

    first = generate_webhook_key("核心 功能/激活")
    second = generate_webhook_key("核心 功能/激活")

    assert first.startswith("核心-功能激活-")
    assert first != second


def test_repository_guardrail_uses_sql_repository_in_production_mode(monkeypatch):
    from aicrm_next.automation_engine.group_ops.postgres_repo import PostgresGroupOpsRepository
    from aicrm_next.automation_engine.group_ops.repo import build_group_ops_repository

    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://group_ops:group_ops@127.0.0.1:1/aicrm")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.delenv("AICRM_GROUP_OPS_DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ALLOW_FIXTURE_REPO_IN_PROD", raising=False)

    repo = build_group_ops_repository()

    assert isinstance(repo, PostgresGroupOpsRepository)
    assert repo.source_status == "postgres_group_ops_repository"
