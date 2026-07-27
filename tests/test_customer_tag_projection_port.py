from __future__ import annotations

import pytest

from aicrm_next.crm.customer_tags.projection_port import build_customer_tag_projection_port


class _Executor:
    def __init__(self, rowcounts) -> None:
        self.rowcounts = list(rowcounts)
        self.rowcount = 0
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), tuple(params)))
        self.rowcount = int(self.rowcounts.pop(0)) if self.rowcounts else 0
        return self


def test_channel_snapshot_uses_owner_port_and_preserves_update_then_insert() -> None:
    executor = _Executor([1, 0, 1])

    result = build_customer_tag_projection_port().save_channel_snapshot_dbapi(
        executor,
        unionid="union-101",
        owner_userid="owner-101",
        tag_ids=["tag-updated", " tag-inserted "],
        tag_names={"tag-updated": "已存在", " tag-inserted ": "新标签"},
    )

    assert result == {"updated_count": 1, "inserted_count": 1}
    assert len(executor.calls) == 3
    assert executor.calls[0][0].startswith("UPDATE contact_tags")
    assert executor.calls[0][1] == ("已存在", "union-101", "owner-101", "tag-updated")
    assert executor.calls[1][0].startswith("UPDATE contact_tags")
    assert executor.calls[2][0].startswith("INSERT INTO contact_tags")
    assert executor.calls[2][1] == ("union-101", "owner-101", "tag-inserted", "新标签")


def test_channel_snapshot_rejects_alias_without_canonical_unionid() -> None:
    executor = _Executor([])

    with pytest.raises(ValueError, match="unionid is required"):
        build_customer_tag_projection_port().save_channel_snapshot_dbapi(
            executor,
            unionid="",
            owner_userid="owner-102",
            tag_ids=["tag-102"],
            tag_names={"tag-102": "标签"},
        )

    assert executor.calls == []
