from __future__ import annotations

from inspect import getsource
from types import SimpleNamespace

import pytest

from aicrm_next.channels.channel_entry import identity_external_effect
from aicrm_next.channels.channel_entry import profile_description_backfill as backfill
from aicrm_next.platform.platform_foundation.external_effects import (
    WECOM_EXTERNAL_CONTACT_DETAIL_FETCH,
    WECOM_PROFILE_UPDATE,
    ExternalEffectJob,
    ExternalEffectService,
    InMemoryExternalEffectRepository,
)
from scripts.ops.backfill_wecom_profile_descriptions import (
    CONFIRMATION,
    _candidate_summary,
    _parse_args,
    _assert_execute_authorized,
)


pytestmark = pytest.mark.high_risk


def test_empty_description_backfill_plans_only_live_empty_relation_idempotently() -> None:
    service = ExternalEffectService(InMemoryExternalEffectRepository())
    parent = ExternalEffectJob(
        id=901,
        effect_type=WECOM_EXTERNAL_CONTACT_DETAIL_FETCH,
        business_type=backfill.PROFILE_DESCRIPTION_BACKFILL_DETAIL_BUSINESS_TYPE,
        business_id=backfill.PROFILE_DESCRIPTION_BACKFILL_RUN_ID,
        execution_id="exe-detail-parent",
        trace_id="trace-detail-parent",
    )
    detail = {
        "external_contact": {"external_userid": "wm_backfill_001"},
        "follow_user": [
            {"userid": "owner-empty", "description": ""},
            {"userid": "owner-nonempty", "description": "人工描述"},
        ],
    }

    first = backfill.plan_empty_profile_description_update(
        parent_job=parent,
        provider_detail=detail,
        external_userid="wm_backfill_001",
        owner_userid="owner-empty",
        service=service,
    )
    duplicate = backfill.plan_empty_profile_description_update(
        parent_job=parent,
        provider_detail=detail,
        external_userid="wm_backfill_001",
        owner_userid="owner-empty",
        service=service,
    )
    nonempty = backfill.plan_empty_profile_description_update(
        parent_job=parent,
        provider_detail=detail,
        external_userid="wm_backfill_001",
        owner_userid="owner-nonempty",
        service=service,
    )
    absent = backfill.plan_empty_profile_description_update(
        parent_job=parent,
        provider_detail=detail,
        external_userid="wm_backfill_001",
        owner_userid="owner-absent",
        service=service,
    )
    jobs, total = service.list_jobs(limit=10)

    assert first["status"] == "queued"
    assert first["created"] is True
    assert duplicate["external_effect_job_id"] == first["external_effect_job_id"]
    assert duplicate["created"] is False
    assert nonempty == {"status": "skipped", "reason": "live_description_nonempty"}
    assert absent == {"status": "skipped", "reason": "owner_relationship_missing"}
    assert total == 1
    assert jobs[0].effect_type == WECOM_PROFILE_UPDATE
    assert jobs[0].business_type == backfill.PROFILE_DESCRIPTION_BACKFILL_UPDATE_BUSINESS_TYPE
    assert jobs[0].payload_json == {
        "external_userid": "wm_backfill_001",
        "follow_user_userid": "owner-empty",
        "description": "wm_backfill_001",
    }
    assert jobs[0].priority == 300
    assert jobs[0].fairness_key == "wecom-profile-description-backfill"


def test_backfill_jobs_route_through_existing_provider_result_and_settlement_consumers() -> None:
    detail_job = ExternalEffectJob(
        effect_type=WECOM_EXTERNAL_CONTACT_DETAIL_FETCH,
        business_type=backfill.PROFILE_DESCRIPTION_BACKFILL_DETAIL_BUSINESS_TYPE,
        payload_json={
            "external_userid": "wm_backfill_002",
            "owner_userids": ["owner-a"],
        },
    )
    update_job = ExternalEffectJob(
        effect_type=WECOM_PROFILE_UPDATE,
        business_type=backfill.PROFILE_DESCRIPTION_BACKFILL_UPDATE_BUSINESS_TYPE,
        status="succeeded",
    )

    assert identity_external_effect.IDENTITY_EXTERNAL_CONTACT_DETAIL_CONTINUATION.matches(detail_job, None) is True
    assert identity_external_effect.IDENTITY_EXTERNAL_EFFECT_SETTLEMENT_CONTINUATION.matches(update_job, None) is True
    assert "run_profile_description_backfill_detail" in getsource(identity_external_effect._run)
    assert "settle_profile_description_backfill" in getsource(identity_external_effect._settle_terminal_identity)


def test_successful_profile_update_settlement_projects_description_only_into_empty_active_relation(monkeypatch) -> None:
    executed: list[tuple[object, dict]] = []

    class Result:
        rowcount = 1

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, params):
            executed.append((statement, params))
            return Result()

        def commit(self):
            return None

    monkeypatch.setattr(backfill, "get_session_factory", lambda: lambda: Session())
    job = ExternalEffectJob(
        status="succeeded",
        effect_type=WECOM_PROFILE_UPDATE,
        business_type=backfill.PROFILE_DESCRIPTION_BACKFILL_UPDATE_BUSINESS_TYPE,
        payload_json={
            "external_userid": "wm_backfill_003",
            "follow_user_userid": "owner-b",
            "description": "wm_backfill_003",
        },
    )

    result = backfill.settle_profile_description_backfill(job, None)

    assert result["ok"] is True
    assert result["projected"] is True
    assert executed[0][1] == {
        "description": "wm_backfill_003",
        "external_userid": "wm_backfill_003",
        "owner_userid": "owner-b",
    }
    sql = str(executed[0][0])
    assert "relation_status = 'active'" in sql
    assert "BTRIM(COALESCE(description, '')) = ''" in sql
    assert "CAST(:description AS text)" in getsource(backfill._sync_live_nonempty_descriptions)


def test_enqueue_requires_exact_authorization_and_candidate_summary_redacts_targets(monkeypatch) -> None:
    args = _parse_args(["--action", "enqueue", "--confirmation", CONFIRMATION])
    monkeypatch.delenv("AICRM_PROFILE_DESCRIPTION_BACKFILL_AUTHORIZED", raising=False)
    with pytest.raises(RuntimeError, match="AICRM_PROFILE_DESCRIPTION_BACKFILL_AUTHORIZED"):
        _assert_execute_authorized(args)
    monkeypatch.setenv("AICRM_PROFILE_DESCRIPTION_BACKFILL_AUTHORIZED", "1")
    _assert_execute_authorized(args)

    summary = _candidate_summary(
        [
            {
                "corp_id": "corp-secret",
                "external_userid": "wm-secret",
                "owner_userids": ["owner-secret"],
            }
        ]
    )
    assert summary["candidate_contact_count"] == 1
    assert summary["candidate_relation_count"] == 1
    assert summary["pii_included"] is False
    assert "wm-secret" not in str(summary)


def test_detail_continuation_reports_safe_stage_specific_projection_failure(monkeypatch) -> None:
    def fail_projection(**_kwargs):
        raise RuntimeError("must-not-leak")

    monkeypatch.setattr(backfill, "_sync_live_nonempty_descriptions", fail_projection)
    job = ExternalEffectJob(
        id=902,
        last_attempt_id="attempt-902",
        effect_type=WECOM_EXTERNAL_CONTACT_DETAIL_FETCH,
        business_type=backfill.PROFILE_DESCRIPTION_BACKFILL_DETAIL_BUSINESS_TYPE,
        payload_json={
            "external_userid": "wm_backfill_004",
            "owner_userids": ["owner-c"],
        },
    )
    dispatch_result = SimpleNamespace(
        provider_result={
            "external_contact": {"external_userid": "wm_backfill_004"},
            "follow_user": [{"userid": "owner-c", "description": ""}],
        }
    )

    result = backfill.run_profile_description_backfill_detail(job, dispatch_result)

    assert result == {"ok": False, "error": "profile_backfill_projection_failed_runtimeerror"}
    assert "must-not-leak" not in str(result)
