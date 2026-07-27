from __future__ import annotations

import pytest

from aicrm_next.automation.automation_engine.group_ops import domain, message_content
from aicrm_next.channels.integration_gateway import wecom_group_adapter, wecom_private_adapter
from aicrm_next.platform.shared import wecom_payload_contract


def test_existing_group_ops_imports_reexport_shared_contract() -> None:
    assert domain.normalize_group_admin_userids is wecom_payload_contract.normalize_group_admin_userids
    assert message_content.normalize_miniprogram_attachment_payload is wecom_payload_contract.normalize_miniprogram_attachment_payload
    assert wecom_group_adapter.normalize_group_admin_userids is wecom_payload_contract.normalize_group_admin_userids
    assert (
        wecom_private_adapter.normalize_miniprogram_attachment_payload
        is wecom_payload_contract.normalize_miniprogram_attachment_payload
    )


def test_group_admin_userid_normalization_preserves_order_and_uniqueness() -> None:
    assert wecom_payload_contract.normalize_group_admin_userids(
        '[{"userid":"owner-a"}, "owner-b", "owner-a", ""]'
    ) == ["owner-a", "owner-b"]


def test_miniprogram_attachment_normalization_preserves_aliases_and_errors() -> None:
    assert wecom_payload_contract.normalize_miniprogram_attachment_payload(
        {
            "appid": "wx-app",
            "pagepath": "/pages/detail",
            "title": "Detail",
            "thumb_media_id": "media-1",
        }
    ) == {
        "appid": "wx-app",
        "page": "/pages/detail",
        "title": "Detail",
        "pic_media_id": "media-1",
    }
    with pytest.raises(ValueError, match="must include appid"):
        wecom_payload_contract.normalize_miniprogram_attachment_payload({})
