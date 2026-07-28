from __future__ import annotations

from sqlalchemy import text

from aicrm_next.automation.background_jobs.broadcast_effect_repository import (
    BROADCAST_MATERIAL_DEPENDENCY_BUSINESS_TYPE,
    _release_after_material_dependency,
)
from aicrm_next.platform.platform_foundation.command_bus import CommandContext
from aicrm_next.platform.platform_foundation.external_effects import WECOM_MEDIA_UPLOAD, WECOM_MESSAGE_PRIVATE_SEND
from aicrm_next.platform.platform_foundation.external_effects.adapters import WeComPrivateMessageAdapter
from aicrm_next.platform.platform_foundation.external_effects.repo import SQLAlchemyExternalEffectRepository
from aicrm_next.platform.platform_foundation.external_effects.service import ExternalEffectService
from aicrm_next.platform.shared.db_session import get_session_factory


def _context() -> CommandContext:
    return CommandContext(
        actor_id="pytest",
        actor_type="system",
        request_id="broadcast-material-postgres",
        trace_id="broadcast-material-postgres",
        source_route="/pytest/broadcast-material",
    )


def test_media_dependencies_release_one_provider_ready_private_effect(next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    business_id = "9001"
    attachments = [
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
            },
        },
    ]
    with get_session_factory()() as session:
        image_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO image_library (name, file_name, data_base64, mime_type, enabled)
                    VALUES ('broadcast image', 'broadcast.png', 'aW1hZ2U=', 'image/png', TRUE)
                    RETURNING id
                    """
                )
            ).scalar_one()
        )
        mini_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO miniprogram_library (name, appid, pagepath, title, thumb_image_base64, enabled)
                    VALUES ('broadcast mini', 'wx-mini', 'pages/article/article?lesson_id=abc', 'AI 时代的新创业范式', 'Y292ZXI=', TRUE)
                    RETURNING id
                    """
                )
            ).scalar_one()
        )
        file_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO attachment_library (name, file_name, data_base64, mime_type, enabled)
                    VALUES ('broadcast file', 'broadcast.pdf', 'JVBERg==', 'application/pdf', TRUE)
                    RETURNING id
                    """
                )
            ).scalar_one()
        )
        material_keys = {
            "image": f"image:{image_id}:image",
            "miniprogram": f"miniprogram:{mini_id}:image",
            "attachment": f"attachment:{file_id}:attachment",
        }
        attachments[0]["image"]["media_dependency_key"] = material_keys["image"]
        attachments[1]["miniprogram"]["pic_media_dependency_key"] = material_keys["miniprogram"]
        attachments[2]["file"]["media_dependency_key"] = material_keys["attachment"]

        service = ExternalEffectService()
        final = service.plan_effect(
            effect_type=WECOM_MESSAGE_PRIVATE_SEND,
            adapter_name="wecom_private_message",
            operation="send_private_message",
            target_type="external_contact",
            target_id="wm-broadcast-material",
            business_type="broadcast_job",
            business_id=business_id,
            payload={
                "channel": "wecom_private",
                "owner_userid": "HuangYouCan",
                "sender": "HuangYouCan",
                "external_userids": ["wm-broadcast-material"],
                "content_text": "四类素材",
                "attachments": attachments,
            },
            payload_summary={"attachment_count": 4},
            context=_context(),
            status="planned",
            idempotency_key="broadcast-material-final",
            connection=session,
        )
        dependency_ids = []
        for kind, material_id, upload_kind in (
            ("image", image_id, "image"),
            ("miniprogram", mini_id, "image"),
            ("attachment", file_id, "attachment"),
        ):
            material_key = material_keys[kind]
            dependency = service.plan_effect(
                effect_type=WECOM_MEDIA_UPLOAD,
                adapter_name="wecom_media_upload",
                operation="refresh_temporary_media",
                target_type="media_library_material",
                target_id=f"{kind}:{material_id}:{upload_kind}",
                business_type=BROADCAST_MATERIAL_DEPENDENCY_BUSINESS_TYPE,
                business_id=business_id,
                payload={
                    "material_key": material_key,
                    "material_kind": kind,
                    "material_id": material_id,
                    "upload_kind": upload_kind,
                    "force_refresh": False,
                },
                context=_context(),
                idempotency_key=f"broadcast-material:{material_key}",
                connection=session,
            )
            dependency_ids.append(int(dependency["id"]))
        session.execute(text("UPDATE image_library SET thumb_media_id = 'provider-image' WHERE id = :id"), {"id": image_id})
        session.execute(text("UPDATE miniprogram_library SET thumb_media_id = 'provider-mini' WHERE id = :id"), {"id": mini_id})
        session.execute(text("UPDATE attachment_library SET media_id = 'provider-file' WHERE id = :id"), {"id": file_id})
        session.execute(
            text("UPDATE external_effect_job SET status = 'succeeded', completed_at = CURRENT_TIMESTAMP WHERE id = ANY(:ids)"),
            {"ids": dependency_ids},
        )
        session.commit()

    dependency_job = SQLAlchemyExternalEffectRepository(get_session_factory()).get_job(dependency_ids[-1])
    assert dependency_job is not None
    released = _release_after_material_dependency(dependency_job)

    assert released["released"] is True
    final_job = SQLAlchemyExternalEffectRepository(get_session_factory()).get_job(int(final["id"]))
    assert final_job is not None
    assert final_job.status == "queued"
    assert final_job.payload_json["attachments"] == [
        {"msgtype": "image", "image": {"media_id": "provider-image"}},
        {
            "msgtype": "miniprogram",
            "miniprogram": {
                "appid": "wx-mini",
                "page": "pages/article/article?lesson_id=abc",
                "title": "AI 时代的新创业范式",
                "pic_media_id": "provider-mini",
            },
        },
        {"msgtype": "file", "file": {"media_id": "provider-file"}},
        {
            "msgtype": "link",
            "link": {
                "title": "加入学习群",
                "url": "https://work.weixin.qq.com/gm/0123456789abcdef0123456789abcdef",
            },
        },
    ]
    assert "dependency_key" not in str(final_job.payload_json)

    class Provider:
        def __init__(self) -> None:
            self.payloads = []

        def create_private_message_task(self, payload, *, idempotency_key=""):
            self.payloads.append(dict(payload))
            return {
                "ok": True,
                "mode": "production",
                "side_effect_executed": True,
                "exact_target_verified": True,
                "requested_external_userids": list(payload.get("external_userids") or []),
                "wecom_msgid": "msg-broadcast-material",
                "result": {"errcode": 0, "msgid": "msg-broadcast-material"},
            }

    monkeypatch.delenv("AICRM_WECOM_PROVIDER_TARGET_POLICY", raising=False)
    provider = Provider()
    dispatched = WeComPrivateMessageAdapter(adapter_factory=lambda: provider).dispatch(final_job)

    assert dispatched.status == "succeeded"
    assert provider.payloads[0]["attachments"] == final_job.payload_json["attachments"]


def test_terminal_material_dependency_blocks_final_and_cancels_queued_siblings(next_pg_schema) -> None:
    del next_pg_schema
    business_id = "9002"
    service = ExternalEffectService()
    with get_session_factory()() as session:
        final = service.plan_effect(
            effect_type=WECOM_MESSAGE_PRIVATE_SEND,
            adapter_name="wecom_private_message",
            operation="send_private_message",
            target_type="external_contact",
            target_id="wm-broadcast-material-failure",
            business_type="broadcast_job",
            business_id=business_id,
            payload={
                "channel": "wecom_private",
                "owner_userid": "HuangYouCan",
                "external_userids": ["wm-broadcast-material-failure"],
                "attachments": [{"msgtype": "image", "image": {"media_dependency_key": "image:1:image"}}],
            },
            context=_context(),
            status="planned",
            idempotency_key="broadcast-material-failure-final",
            connection=session,
        )
        dependency_ids = []
        for material_id in (1, 2):
            dependency = service.plan_effect(
                effect_type=WECOM_MEDIA_UPLOAD,
                adapter_name="wecom_media_upload",
                operation="refresh_temporary_media",
                target_type="media_library_material",
                target_id=f"image:{material_id}:image",
                business_type=BROADCAST_MATERIAL_DEPENDENCY_BUSINESS_TYPE,
                business_id=business_id,
                payload={
                    "material_key": f"image:{material_id}:image",
                    "material_kind": "image",
                    "material_id": material_id,
                    "upload_kind": "image",
                },
                context=_context(),
                idempotency_key=f"broadcast-material-failure:{material_id}",
                connection=session,
            )
            dependency_ids.append(int(dependency["id"]))
        session.execute(
            text(
                """
                UPDATE external_effect_job
                SET status = 'failed_terminal', last_error_code = 'material_source_missing',
                    completed_at = CURRENT_TIMESTAMP
                WHERE id = :id
                """
            ),
            {"id": dependency_ids[0]},
        )
        session.commit()

    failed_dependency = SQLAlchemyExternalEffectRepository(get_session_factory()).get_job(dependency_ids[0])
    assert failed_dependency is not None
    result = _release_after_material_dependency(failed_dependency)

    assert result["blocked"] is True
    assert result["cancelled_dependency_count"] == 1
    with get_session_factory()() as session:
        rows = {
            int(row["id"]): str(row["status"])
            for row in session.execute(
                text("SELECT id, status FROM external_effect_job WHERE id = ANY(:ids)"),
                {"ids": [int(final["id"]), *dependency_ids]},
            ).mappings()
        }
    assert rows[int(final["id"])] == "blocked"
    assert rows[dependency_ids[0]] == "failed_terminal"
    assert rows[dependency_ids[1]] == "cancelled"
