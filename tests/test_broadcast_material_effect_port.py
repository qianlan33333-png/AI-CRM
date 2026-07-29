from __future__ import annotations

import pytest

from aicrm_next.engagement.media_library.broadcast_effect_port import RepositoryBroadcastMaterialPlanPort
from aicrm_next.platform.shared.errors import ContractError


class Repository:
    def __init__(self) -> None:
        self.items = {
            ("image", "12"): {"id": 12, "enabled": True, "data_base64": "aW1hZ2U="},
            ("image", "99"): {"id": 99, "enabled": True, "data_base64": "Y292ZXI="},
            ("miniprogram", "34"): {
                "id": 34,
                "enabled": True,
                "appid": "wx-mini",
                "pagepath": "pages/article/article?lesson_id=abc",
                "title": "AI 时代的新创业范式",
                "thumb_image_id": 99,
            },
            ("attachment", "56"): {"id": 56, "enabled": True, "data_base64": "JVBERg=="},
            ("group_invite", "78"): {
                "id": 78,
                "enabled": True,
                "binding_status": "ready",
                "title": "加入学习群",
                "description": "扫码后进入",
                "join_url": "https://work.weixin.qq.com/gm/0123456789abcdef0123456789abcdef",
            },
        }

    def get_item(self, kind, item_id, *, include_data=True):
        del include_data
        item = self.items.get((kind, str(item_id)))
        return dict(item) if item else None


def test_broadcast_material_plan_is_provider_free_and_covers_all_supported_kinds() -> None:
    plan = RepositoryBroadcastMaterialPlanPort(Repository()).plan(
        {
            "image_library_ids": [12],
            "miniprogram_library_ids": [34],
            "attachment_library_ids": [56],
            "group_invite_library_ids": [78],
        }
    )

    assert plan["attachments"] == [
        {"msgtype": "image", "image": {"media_dependency_key": "image:12:image"}},
        {
            "msgtype": "miniprogram",
            "miniprogram": {
                "appid": "wx-mini",
                "page": "pages/article/article?lesson_id=abc",
                "title": "AI 时代的新创业范式",
                "pic_media_dependency_key": "miniprogram:34:image",
            },
        },
        {"msgtype": "file", "file": {"media_dependency_key": "attachment:56:attachment"}},
        {
            "msgtype": "link",
            "link": {
                "title": "加入学习群",
                "url": "https://work.weixin.qq.com/gm/0123456789abcdef0123456789abcdef",
                "desc": "扫码后进入",
            },
        },
    ]
    assert plan["uploads"] == [
        {"material_key": "image:12:image", "material_kind": "image", "material_id": 12, "upload_kind": "image"},
        {"material_key": "miniprogram:34:image", "material_kind": "miniprogram", "material_id": 34, "upload_kind": "image"},
        {
            "material_key": "attachment:56:attachment",
            "material_kind": "attachment",
            "material_id": 56,
            "upload_kind": "attachment",
        },
    ]


def test_dynamic_card_uses_image_dependency_without_provider_resolution() -> None:
    plan = RepositoryBroadcastMaterialPlanPort(Repository()).plan(
        {
            "dynamic_miniprogram_card": {
                "appid": "wx-dynamic",
                "pagepath": "pages/article/article?rid=rid-1",
                "title": "Dynamic Mini",
                "cover_image_id": 99,
            }
        }
    )

    assert plan == {
        "attachments": [
            {
                "msgtype": "miniprogram",
                "miniprogram": {
                    "appid": "wx-dynamic",
                    "page": "pages/article/article?rid=rid-1",
                    "title": "Dynamic Mini",
                    "pic_media_dependency_key": "image:99:image",
                },
            }
        ],
        "uploads": [
            {"material_key": "image:99:image", "material_kind": "image", "material_id": 99, "upload_kind": "image"}
        ],
    }


def test_material_without_durable_source_fails_before_effect_planning() -> None:
    repository = Repository()
    repository.items[("attachment", "56")]["data_base64"] = ""

    with pytest.raises(ContractError, match="attachment_material_source_missing:id=56"):
        RepositoryBroadcastMaterialPlanPort(repository).plan({"attachment_library_ids": [56]})


def test_miniprogram_title_over_wecom_utf8_limit_fails_before_effect_planning() -> None:
    repository = Repository()
    repository.items[("miniprogram", "34")]["title"] = "中" * 22

    with pytest.raises(ValueError, match="title exceeds 64 UTF-8 bytes"):
        RepositoryBroadcastMaterialPlanPort(repository).plan({"miniprogram_library_ids": [34]})
