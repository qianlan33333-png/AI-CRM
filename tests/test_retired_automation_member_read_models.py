from __future__ import annotations

from pathlib import Path
from typing import Any

from aicrm_next.insights.admin_read_model.projections import funnel_payload


ROOT = Path(__file__).resolve().parents[1]


def test_operation_member_sources_do_not_read_retired_automation_member() -> None:
    source = (ROOT / "aicrm_next" / "common_operation_members.py").read_text(encoding="utf-8")

    assert "automation_member_owner_field" not in source
    assert "FROM automation_member" not in source


def test_admin_funnel_counts_use_ai_audience_and_platform_queues() -> None:
    source = (ROOT / "aicrm_next" / "insights" / "admin_read_model" / "projections.py").read_text(encoding="utf-8")

    assert "repo.count(\"automation_member\")" not in source
    assert "repo.count(\"automation_operation_task\")" not in source
    assert "repo.count(\"automation_workflow_execution\")" not in source
    assert "AI 人群包成员" in source
    assert "repo.count(\"ai_audience_member_current\")" in source
    assert "repo.count(\"internal_event\")" in source
    assert "repo.count(\"external_effect_job\")" in source


def test_funnel_payload_labels_retained_capabilities_instead_of_old_member_pool() -> None:
    class FakeRepo:
        source_status = "test"
        is_production = True

        def __init__(self) -> None:
            self.counted: list[str] = []

        def count(self, table: str) -> int:
            self.counted.append(table)
            return len(self.counted)

        def rows(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
            return []

    repo = FakeRepo()
    payload = funnel_payload(repo)  # type: ignore[arg-type]

    labels = [card["label"] for card in payload["cards"]]
    assert labels == ["客户总数", "问卷提交", "订单数", "AI 人群包成员", "内部事件", "外推任务"]
    assert "automation_member" not in repo.counted
    assert "automation_operation_task" not in repo.counted
    assert "automation_workflow_execution" not in repo.counted
    assert "ai_audience_member_current" in repo.counted
    assert "internal_event" in repo.counted
    assert "external_effect_job" in repo.counted
