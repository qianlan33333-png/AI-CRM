from __future__ import annotations

import pytest

from aicrm_next.extensions.growth.cloud_orchestrator.campaign_step_media_port import build_campaign_step_media_reference_port


class _Executor:
    def __init__(self, *, rowcount: int = 0, rows: list[dict] | None = None) -> None:
        self.rowcount = rowcount
        self.rows = list(rows or [])
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def execute(self, sql: str, params: tuple[str, ...]) -> "_Executor":
        self.calls.append((" ".join(sql.split()), params))
        return self

    def fetchall(self) -> list[dict]:
        return list(self.rows)


def test_campaign_step_media_cleanup_uses_owner_sql_and_normalized_id() -> None:
    executor = _Executor(rowcount=2)

    changed = build_campaign_step_media_reference_port().clear_image_references_dbapi(
        executor,
        image_library_id=" 42 ",
    )

    assert changed == 2
    assert len(executor.calls) == 1
    assert executor.calls[0][0].startswith("UPDATE campaign_steps")
    assert executor.calls[0][1] == ("42", "42")


def test_campaign_step_media_cleanup_rejects_empty_image_id() -> None:
    executor = _Executor(rowcount=1)

    with pytest.raises(ValueError, match="image_library_id is required"):
        build_campaign_step_media_reference_port().clear_image_references_dbapi(
            executor,
            image_library_id=" ",
        )

    assert executor.calls == []


def test_campaign_step_media_reference_query_uses_owner_sql_and_normalized_id() -> None:
    executor = _Executor(rows=[{"id": 9, "campaign_id": 3, "campaign_segment_id": 4, "step_index": 1}])

    rows = build_campaign_step_media_reference_port().list_image_references_dbapi(
        executor,
        image_library_id=" 42 ",
    )

    assert rows == [{"id": 9, "campaign_id": 3, "campaign_segment_id": 4, "step_index": 1}]
    assert executor.calls[0][0].startswith("SELECT id, campaign_id")
    assert executor.calls[0][1] == ("42",)
