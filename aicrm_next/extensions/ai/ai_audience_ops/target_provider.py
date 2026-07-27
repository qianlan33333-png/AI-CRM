from __future__ import annotations

from typing import Any

from aicrm_next.shared.typing import JsonDict

from .repository import AudienceRepository, build_audience_repository, _text


class AiAudienceTargetProvider:
    def __init__(self, repository: AudienceRepository | None = None) -> None:
        self._repo = repository or build_audience_repository()

    def rows_for_package(self, package_id: int) -> list[JsonDict]:
        rows = self._repo.list_ai_audience_batch_rows(int(package_id))
        return [_standard_row(row) for row in rows]


def _standard_row(row: dict[str, Any]) -> JsonDict:
    return {
        "id": int(row.get("id") or 0),
        "unionid": _text(row.get("unionid")),
        "external_userid": _text(row.get("external_userid")),
        "customer_name": _text(row.get("customer_name")) or "未命名客户",
        "owner_userid": _text(row.get("owner_userid")),
        "owner_display_name": _text(row.get("owner_display_name")),
        "mobile": "",
        "do_not_disturb": False,
        "is_added_wecom": True,
        "is_mobile_bound": False,
        "activation_bucket": "",
        "class_term_no": "",
        "tags": [],
        "skip_reason": _text(row.get("skip_reason")),
    }
