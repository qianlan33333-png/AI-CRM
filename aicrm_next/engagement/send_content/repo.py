from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol

from aicrm_next.platform.shared.errors import ContractError
from aicrm_next.platform.shared.repository_provider import RepositoryProviderError, assert_repository_allowed
from aicrm_next.platform.shared.runtime import production_data_ready, production_environment, raw_database_url
from aicrm_next.platform.shared.runtime_settings import startup_environment_setting


SEND_CONTENT_BACKEND_ENV = "AICRM_SEND_CONTENT_REPO_BACKEND"
SEND_CONTENT_DATABASE_URL_ENV = "AICRM_SEND_CONTENT_DATABASE_URL"
SEND_CONTENT_SQL_BACKENDS = {"sql", "postgres", "postgresql", "psycopg"}
MATERIAL_TYPES = {"image", "miniprogram", "attachment", "group_invite"}


class SendContentRepository(Protocol):
    source_status: str

    def list_materials(
        self,
        material_type: str,
        *,
        q: str = "",
        enabled_only: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]: ...

    def get_materials_by_ids(self, material_type: str, ids: list[int]) -> list[dict[str, Any]]: ...

    def list_material_asset_usage(
        self,
        material_type: str,
        source_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]: ...


def _fixture_items() -> dict[str, list[dict[str, Any]]]:
    return {
        "image": [
            {
                "type": "image",
                "library_id": 12,
                "title": "AI 分享海报",
                "subtitle": "image/png · 320KB",
                "thumbnail_url": "/api/admin/image-library/12/thumbnail?size=160",
                "enabled": True,
                "metadata": {
                    "file_name": "ai-share-poster.png",
                    "mime_type": "image/png",
                    "category": "海报",
                    "tags": ["fixture", "standard-content"],
                },
            },
            {
                "type": "image",
                "library_id": 13,
                "title": "课程权益截图",
                "subtitle": "image/jpeg · 180KB",
                "thumbnail_url": "/api/admin/image-library/13/thumbnail?size=160",
                "enabled": True,
                "metadata": {
                    "file_name": "course-benefit.jpg",
                    "mime_type": "image/jpeg",
                    "category": "截图",
                    "tags": ["fixture"],
                },
            },
        ],
        "miniprogram": [
            {
                "type": "miniprogram",
                "library_id": 34,
                "title": "黄小璨体验课",
                "subtitle": "wx_fixture · pages/course/index",
                "thumbnail_url": "/api/admin/image-library/12/thumbnail?size=160",
                "enabled": True,
                "metadata": {
                    "appid": "wx_fixture",
                    "pagepath": "pages/course/index",
                    "thumb_image_id": 12,
                    "thumb_media_id": "",
                },
            }
        ],
        "attachment": [
            {
                "type": "attachment",
                "library_id": 56,
                "title": "AI 资料包",
                "subtitle": "application/pdf · 512KB",
                "thumbnail_url": "",
                "enabled": True,
                "metadata": {
                    "file_name": "ai-material.pdf",
                    "mime_type": "application/pdf",
                    "file_size": 524288,
                    "tags": ["fixture"],
                },
            }
        ],
        "group_invite": [
            {
                "type": "group_invite",
                "library_id": 78,
                "title": "加入 AI 同行群",
                "subtitle": "点击卡片直接加入群聊",
                "thumbnail_url": "https://example.com/group-invite.png",
                "enabled": True,
                "metadata": {
                    "description": "进群领取资料",
                    "join_url": "https://work.weixin.qq.com/gm/0123456789abcdef0123456789abcdef",
                    "pic_url": "https://example.com/group-invite.png",
                    "config_id": "fixture-group-invite",
                    "state": "fixture",
                    "binding_status": "ready",
                },
            }
        ],
    }


class InMemorySendContentRepository:
    source_status = "fixture_local_contract"

    def __init__(self, data: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self._data = deepcopy(data if data is not None else _fixture_items())
        for material_type in MATERIAL_TYPES:
            self._data.setdefault(material_type, [])

    def list_materials(
        self,
        material_type: str,
        *,
        q: str = "",
        enabled_only: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        _assert_material_type(material_type)
        limit = _clamp_limit(limit)
        offset = max(0, int(offset or 0))
        query = str(q or "").strip().lower()
        rows = [deepcopy(item) for item in self._data[material_type]]
        if enabled_only:
            rows = [item for item in rows if item.get("enabled", True)]
        if query:
            rows = [item for item in rows if query in _search_blob(item)]
        total = len(rows)
        return {"items": rows[offset : offset + limit], "total": total, "limit": limit, "offset": offset}

    def get_materials_by_ids(self, material_type: str, ids: list[int]) -> list[dict[str, Any]]:
        _assert_material_type(material_type)
        by_id = {int(item["library_id"]): deepcopy(item) for item in self._data[material_type]}
        return [by_id[item_id] for item_id in ids if item_id in by_id]

    def list_material_asset_usage(
        self,
        material_type: str,
        source_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        _assert_material_type(material_type)
        limit = _clamp_limit(limit)
        offset = max(0, int(offset or 0))
        material_asset_id = f"{material_type}:{int(source_id)}"
        rows = [
            item
            for item in _fixture_usage_items()
            if item["material_asset_id"] == material_asset_id
        ]
        return {"items": deepcopy(rows[offset : offset + limit]), "total": len(rows), "limit": limit, "offset": offset}


def _assert_material_type(material_type: str) -> None:
    if material_type not in MATERIAL_TYPES:
        raise ContractError("素材类型必须是 image、miniprogram、attachment 或 group_invite")


def _fixture_usage_items() -> list[dict[str, Any]]:
    return [
        _usage_item(
            material_type="image",
            source_id=12,
            consumer_type="channel_welcome_config",
            source_table="automation_channel",
            source_record_id="fixture-channel-1",
            title="渠道欢迎语",
            status="active",
            field_path="welcome_image_library_ids",
        ),
        _usage_item(
            material_type="image",
            source_id=12,
            consumer_type="cloud_plan_content_payload",
            source_table="cloud_broadcast_plan_recipient_messages",
            source_record_id="fixture-plan-1:message-1",
            title="云群发计划话术",
            status="pending",
            field_path="content_payload_json.image_library_ids",
        ),
        _usage_item(
            material_type="image",
            source_id=12,
            consumer_type="wechat_pay_product_page_slice",
            source_table="wechat_pay_product_page_slices",
            source_record_id="fixture-product-slice-1",
            title="支付商品页切片",
            status="enabled",
            field_path="image_library_id",
        ),
        _usage_item(
            material_type="miniprogram",
            source_id=34,
            consumer_type="group_ops_draft",
            source_table="automation_group_ops_plan_nodes",
            source_record_id="fixture-group-node-1",
            title="群运营草稿节点",
            status="draft",
            field_path="content_package_json.miniprogram_library_ids",
        ),
        _usage_item(
            material_type="attachment",
            source_id=56,
            consumer_type="group_ops_draft",
            source_table="automation_group_ops_plan_nodes",
            source_record_id="fixture-group-node-2",
            title="群运营附件节点",
            status="active",
            field_path="content_package_json.attachment_library_ids",
        ),
        _usage_item(
            material_type="group_invite",
            source_id=78,
            consumer_type="channel_welcome_config",
            source_table="automation_channel",
            source_record_id="fixture-channel-group-invite",
            title="渠道入群欢迎语",
            status="active",
            field_path="welcome_group_invite_library_ids",
        ),
    ]


def _usage_item(
    *,
    material_type: str,
    source_id: int,
    consumer_type: str,
    source_table: str,
    source_record_id: str,
    title: str,
    status: str,
    field_path: str,
    owner_userid: str = "",
    used_at: str = "",
) -> dict[str, Any]:
    material_asset_id = f"{material_type}:{int(source_id)}"
    return {
        "usage_id": f"{consumer_type}:{source_table}:{source_record_id}:{field_path}",
        "material_asset_id": material_asset_id,
        "asset_type": material_type,
        "source_id": int(source_id),
        "consumer_type": consumer_type,
        "source_table": source_table,
        "source_record_id": str(source_record_id),
        "title": str(title or ""),
        "status": str(status or ""),
        "owner_userid": str(owner_userid or ""),
        "field_path": str(field_path or ""),
        "used_at": str(used_at or ""),
        "metadata": {},
    }


def _search_blob(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return " ".join(
        [
            str(item.get("title") or ""),
            str(item.get("subtitle") or ""),
            str(metadata.get("file_name") or ""),
            str(metadata.get("appid") or ""),
            str(metadata.get("pagepath") or ""),
            " ".join(str(tag) for tag in metadata.get("tags") or []),
        ]
    ).lower()


def _clamp_limit(limit: int) -> int:
    return max(1, min(int(limit or 50), 100))


def _send_content_backend() -> str:
    return startup_environment_setting(SEND_CONTENT_BACKEND_ENV, "fixture").strip().lower()


def _send_content_database_url() -> str:
    return str(
        startup_environment_setting(SEND_CONTENT_DATABASE_URL_ENV)
        or raw_database_url()
    ).strip()


def build_send_content_repository() -> SendContentRepository:
    backend = _send_content_backend()
    if production_data_ready() or backend in SEND_CONTENT_SQL_BACKENDS:
        database_url = _send_content_database_url()
        if not database_url:
            raise RepositoryProviderError("send content production repository unavailable: DATABASE_URL is required")
        from .postgres_repo import PostgresSendContentRepository

        return assert_repository_allowed(
            PostgresSendContentRepository(database_url),
            capability_owner="send_content",
        )
    if production_environment():
        raise RepositoryProviderError("send content production repository unavailable: production data is not ready")
    return assert_repository_allowed(
        InMemorySendContentRepository(),
        capability_owner="send_content",
    )
