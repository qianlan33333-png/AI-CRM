from __future__ import annotations

from datetime import datetime, timezone

from aicrm_next.extensions.growth.cloud_orchestrator import run_due
from aicrm_next.platform_foundation.command_bus import Command, CommandContext
from aicrm_next.platform_foundation.external_effects import AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK, WECOM_MESSAGE_PRIVATE_SEND
from aicrm_next.platform_foundation.external_effects.repo import reset_external_effect_fixture_state


class FakeCampaignReadRepository:
    def list_campaigns(self, *, limit: int, offset: int):
        return ([{"id": 77, "campaign_code": "camp_due", "owner_userid": "owner_001", "trace_id": "trace-camp"}], 1)

    def list_members(self, campaign_code: str, *, status: str, limit: int, offset: int):
        assert status == "pending"
        return {
            "members": [
                {"member_id": 501, "unionid": "union_due_0", "external_contact_id": "wm_due_0", "status": "pending", "current_step_index": -1, "next_due_at": "2026-07-04T08:00:00+00:00"},
                {"member_id": 502, "unionid": "union_future", "external_contact_id": "wm_future", "status": "pending", "current_step_index": -1, "next_due_at": "2026-07-05T08:00:00+00:00"},
                {"member_id": 503, "unionid": "union_due_1", "external_contact_id": "wm_due_1", "status": "pending", "current_step_index": 0, "next_due_at": "2026-07-04T08:00:00+00:00"},
                {"member_id": 504, "unionid": "union_done", "external_contact_id": "wm_done", "status": "pending", "current_step_index": 1, "next_due_at": "2026-07-04T08:00:00+00:00"},
            ]
        }

    def list_steps(self, campaign_code: str):
        return {
            "steps": [
                {"step_index": 0, "content_text": "step 0", "content_payload_json": {"content_text": "step 0"}},
                {"step_index": 1, "content_text": "step 1", "content_payload_json": {"content_text": "step 1"}},
            ]
        }


class PagedCampaignReadRepository(FakeCampaignReadRepository):
    def __init__(self) -> None:
        self.offsets: list[int] = []
        self.members = [
            {"member_id": 601, "unionid": "union_future", "external_contact_id": "wm_future", "status": "pending", "current_step_index": -1, "next_due_at": "2026-07-05T08:00:00+00:00"},
            {"member_id": 602, "unionid": "union_due_late_page", "external_contact_id": "wm_due_late_page", "status": "pending", "current_step_index": -1, "next_due_at": "2026-07-04T08:00:00+00:00"},
        ]

    def list_members(self, campaign_code: str, *, status: str, limit: int, offset: int):
        self.offsets.append(offset)
        rows = self.members[offset : offset + limit]
        return {"members": rows, "rows": rows, "total": len(self.members), "limit": limit, "offset": offset}


def test_due_candidates_filter_next_due_at_and_pick_next_step(monkeypatch) -> None:
    monkeypatch.setattr(run_due, "_repo", lambda: FakeCampaignReadRepository())

    candidates, diagnostics = run_due._due_candidates(10, now=datetime(2026, 7, 4, 9, 0, tzinfo=timezone.utc))

    assert diagnostics["candidate_generation_status"] == "ready"
    assert [(item["member_id"], item["next_step_index"]) for item in candidates] == [(501, 0), (503, 1)]
    assert candidates[1]["next_step"]["content_text"] == "step 1"


def test_due_candidates_pages_until_due_members_are_found(monkeypatch) -> None:
    repository = PagedCampaignReadRepository()
    monkeypatch.setattr(run_due, "_repo", lambda: repository)

    candidates, diagnostics = run_due._due_candidates(1, now=datetime(2026, 7, 4, 9, 0, tzinfo=timezone.utc))

    assert diagnostics["candidate_generation_status"] == "ready"
    assert [item["member_id"] for item in candidates] == [602]
    assert repository.offsets == [0, 1]


def test_run_due_external_effect_idempotency_is_stable_per_member_step() -> None:
    candidate = {
        "campaign_code": "camp_due",
        "campaign_id": 77,
        "member_id": 501,
        "unionid": "union_due_0",
        "external_contact_id": "wm_due_0",
        "next_step_index": 0,
    }
    command_a = Command(command_name="test", command_id="cmd_a", idempotency_key="request-a", context=CommandContext(trace_id="trace-a"))
    command_b = Command(command_name="test", command_id="cmd_b", idempotency_key="request-b", context=CommandContext(trace_id="trace-b"))

    _payload_a, meta_a = run_due._loopback_payload_for_candidate(command=command_a, candidate=candidate, loopback_mode=False)
    _payload_b, meta_b = run_due._loopback_payload_for_candidate(command=command_b, candidate=candidate, loopback_mode=False)

    assert meta_a["idempotency_key"] == meta_b["idempotency_key"]
    assert meta_a["idempotency_key"] == f"cloud_campaign_run_due:{AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK}:camp_due:501:0"
    assert "cmd_" not in meta_a["idempotency_key"]
    assert "request-" not in meta_a["idempotency_key"]


def test_wecom_private_run_due_jobs_require_approval(monkeypatch) -> None:
    reset_external_effect_fixture_state()
    monkeypatch.setenv(run_due.AI_ASSIST_EXTERNAL_EFFECT_SEND_MODE_KEY, "wecom_private")
    command = Command(command_name="test", command_id="cmd_a", idempotency_key="request-a", context=CommandContext(trace_id="trace-a"))
    candidate = {
        "campaign_code": "camp_due",
        "campaign_id": 77,
        "owner_userid": "owner_001",
        "member_id": 501,
        "unionid": "union_due_0",
        "external_contact_id": "wm_due_0",
        "next_step_index": 0,
        "next_step": {"step_index": 0, "content_text": "hello", "content_payload_json": {"content_text": "hello"}},
    }

    jobs, errors = run_due._plan_external_effect_jobs(command=command, candidates=[candidate])

    assert errors == []
    assert len(jobs) == 1
    assert jobs[0]["effect_type"] == WECOM_MESSAGE_PRIVATE_SEND
    assert jobs[0]["requires_approval"] is True
    assert jobs[0]["execution_mode"] == "execute"
    assert jobs[0]["status"] == "planned"
