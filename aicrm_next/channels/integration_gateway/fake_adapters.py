from __future__ import annotations

from aicrm_next.shared.typing import JsonDict


class FakeWeComDispatchAdapter:
    def create_private_message_task(
        self,
        *,
        sender_userid: str,
        external_userids: list[str],
        content: str,
        images: list[dict] | None = None,
        attachments: list[dict] | None = None,
    ) -> JsonDict:
        return {
            "task_id": f"fake_wecom_task_{sender_userid or 'unassigned'}_{len(external_userids)}",
            "status": "created",
            "status_label": "已创建任务",
            "error_message": "",
            "dispatch_adapter": "fake_wecom",
            "sender_userid": sender_userid,
            "external_userids": list(external_userids),
            "target_count": len(external_userids),
            "content_preview": content[:80],
            "image_count": len(images or []),
            "attachment_count": len(attachments or []),
        }
