from __future__ import annotations

import pytest

from aicrm_next.automation.automation_engine.group_ops.domain import (
    PLAN_STATUSES,
    clamp_limit,
    group_manageable_by_userid,
    mask_sensitive_payload,
    normalize_plan_payload,
    normalize_recipients,
    normalize_scheduled_time,
    scheduled_time_options,
)
from aicrm_next.platform.platform_foundation.background_jobs.catalog import JobCatalog, validate_job_catalog
from aicrm_next.platform.shared.errors import ContractError


pytestmark = pytest.mark.unit


def test_group_ops_plan_normalization_sets_current_defaults() -> None:
    plan = normalize_plan_payload({"name": "入群第 1 天", "owner_userid": "owner-1", "type": "webhook_receiver"})
    assert plan["plan_name"] == "入群第 1 天"
    assert plan["plan_type"] == "webhook"
    assert plan["status"] in PLAN_STATUSES
    assert plan["default_action_type"] == "enqueue"


def test_group_ops_schedule_is_bounded_to_business_half_hours() -> None:
    options = scheduled_time_options()
    assert options[0] == "08:00"
    assert options[-1] == "23:30"
    assert normalize_scheduled_time("09:30") == "09:30"
    with pytest.raises(ContractError):
        normalize_scheduled_time("07:30")
    assert clamp_limit(999, maximum=200) == 200


def test_group_owner_or_admin_can_manage_group() -> None:
    group = {"owner_userid": "owner-1", "admin_userids": '["admin-1", "admin-2"]'}
    assert group_manageable_by_userid(group, "owner-1")
    assert group_manageable_by_userid(group, "admin-2")
    assert not group_manageable_by_userid(group, "outsider")


def test_recipient_deduplication_and_sensitive_masking() -> None:
    recipients = normalize_recipients([
        {"external_user_id": "ext-1"},
        {"externalUserId": "ext-1"},
        {"groupId": "chat-1"},
    ])
    assert len(recipients) == 2
    masked = mask_sensitive_payload({"token": "secret", "nested": {"external_userid": "external-123456"}})
    assert masked["token"] == "[redacted]"
    assert "[redacted]" in masked["nested"]["external_userid"]


def test_job_catalog_dispatches_only_for_declared_runtime_owner() -> None:
    assert validate_job_catalog() == []
    catalog = JobCatalog()
    catalog.register_handler("internal_event.dispatch", lambda payload: {"ok": True, "payload": payload})
    result = catalog.dispatch("internal_event.dispatch", {"event": "crm.identity.updated"}, runtime_role="internal_worker")
    assert result["ok"] is True
    assert result["real_external_call_executed"] is False
    with pytest.raises(PermissionError):
        catalog.dispatch("internal_event.dispatch", {}, runtime_role="external_worker")
