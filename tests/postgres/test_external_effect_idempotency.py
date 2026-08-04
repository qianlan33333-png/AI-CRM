from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from aicrm_next.platform.platform_foundation.external_effects.models import ExternalEffectCreateRequest
from aicrm_next.platform.platform_foundation.external_effects.repo import SQLAlchemyExternalEffectRepository
from aicrm_next.platform.shared.db_session import get_session_factory, reset_engine_cache_for_tests


pytestmark = pytest.mark.postgres


def test_repository_concurrent_create_returns_one_durable_job(migrated_database_url: str) -> None:
    reset_engine_cache_for_tests()
    repository = SQLAlchemyExternalEffectRepository(get_session_factory(migrated_database_url))
    key = "current-test:" + uuid4().hex
    request = ExternalEffectCreateRequest(
        effect_type="test.loopback",
        adapter_name="test_receiver",
        operation="record",
        target_type="test_probe",
        target_id=key,
        business_type="current_test",
        business_id=key,
        source_module="tests.postgres",
        idempotency_key=key,
        payload={"probe": True},
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = list(executor.map(lambda _index: repository.create_job(request), range(2)))
    assert jobs[0].id == jobs[1].id
    assert sorted(job.created_on_plan for job in jobs) == [False, True]
    persisted = repository.get_job(jobs[0].id)
    assert persisted is not None
    assert persisted.idempotency_key == key
