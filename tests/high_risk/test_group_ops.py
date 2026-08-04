from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aicrm_next.automation.automation_engine.group_ops.durable_effects_repository import InMemoryGroupOpsEffectGraphRepository
from aicrm_next.automation.automation_engine.group_ops.repo import InMemoryGroupOpsRepository
from aicrm_next.automation.automation_engine.group_ops.scheduler import GroupOpsDueScheduler
from aicrm_next.platform.platform_foundation.external_effects import InMemoryExternalEffectRepository


pytestmark = pytest.mark.high_risk


def test_due_scheduler_reuses_the_same_effect_graph_on_retry() -> None:
    group_repository = InMemoryGroupOpsRepository()
    effect_repository = InMemoryExternalEffectRepository()
    graph_repository = InMemoryGroupOpsEffectGraphRepository(effect_repository)
    scheduler = GroupOpsDueScheduler(repo=group_repository, effect_graph_repo=graph_repository)
    now = datetime.now(timezone.utc) + timedelta(days=2)
    first = scheduler.run_due(now=now, operator="current-test-scheduler")
    second = scheduler.run_due(now=now, operator="current-test-scheduler")
    jobs, total = effect_repository.list_jobs({}, limit=20)
    assert first["errors"] == []
    assert first["group_ops_external_effect_jobs"] == 1
    assert second["group_ops_reused_external_effect_jobs"] == 1
    assert second["group_ops_skipped_duplicate"] == 1
    assert total == 1
    assert jobs[0].side_effect_executed is False
