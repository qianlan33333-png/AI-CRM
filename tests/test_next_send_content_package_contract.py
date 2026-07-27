from __future__ import annotations

import pytest

from aicrm_next.engagement.send_content.application import NormalizeSendContentPackageCommand
from aicrm_next.engagement.send_content.dto import SendContentPackage
from aicrm_next.platform.shared.errors import ContractError


def _normalize(raw: dict, *, text_enabled: bool = True, require_body: bool = False) -> dict:
    return NormalizeSendContentPackageCommand()(
        SendContentPackage.model_validate(raw),
        text_enabled=text_enabled,
        require_body=require_body,
    )


def test_content_text_is_trimmed() -> None:
    assert _normalize({"content_text": "  你好  "})["content_text"] == "你好"


def test_empty_fields_normalize_to_empty_package() -> None:
    assert _normalize({}) == {
        "content_text": "",
        "image_library_ids": [],
        "miniprogram_library_ids": [],
        "attachment_library_ids": [],
        "group_invite_library_ids": [],
    }


def test_ids_are_deduplicated_with_original_order() -> None:
    assert _normalize({"image_library_ids": [12, "12", 34]})["image_library_ids"] == [12, 34]


def test_invalid_id_returns_readable_chinese_error() -> None:
    with pytest.raises(ContractError, match="正整数"):
        _normalize({"image_library_ids": ["abc"]})


def test_image_ids_allow_at_most_three_without_truncating() -> None:
    with pytest.raises(ContractError, match="最多允许 3 个"):
        _normalize({"image_library_ids": [1, 2, 3, 4]})


def test_miniprogram_ids_allow_at_most_one_without_truncating() -> None:
    with pytest.raises(ContractError, match="最多允许 1 个"):
        _normalize({"miniprogram_library_ids": [1, 2]})


def test_attachment_ids_allow_at_most_nine_without_truncating() -> None:
    with pytest.raises(ContractError, match="最多允许 9 个"):
        _normalize({"attachment_library_ids": list(range(1, 11))})


def test_group_invite_ids_allow_at_most_one_without_truncating() -> None:
    with pytest.raises(ContractError, match="最多允许 1 个"):
        _normalize({"group_invite_library_ids": [1, 2]})


def test_text_disabled_forces_content_text_empty() -> None:
    assert _normalize({"content_text": "  必须忽略  "}, text_enabled=False)["content_text"] == ""


def test_require_body_rejects_empty_package() -> None:
    with pytest.raises(ContractError, match="不能为空"):
        _normalize({}, require_body=True)


def test_require_body_false_allows_empty_package() -> None:
    assert _normalize({}, require_body=False) == {
        "content_text": "",
        "image_library_ids": [],
        "miniprogram_library_ids": [],
        "attachment_library_ids": [],
        "group_invite_library_ids": [],
    }
