from __future__ import annotations

import json
from typing import Any

from fastapi.responses import JSONResponse

from aicrm_next.extensions.ai.ai_assist import external_campaigns as service
from aicrm_next.extensions.ai.ai_assist.external_campaigns_repo import PostgresExternalCampaignRepository
from aicrm_next.platform_foundation.external_effects import AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK, ExternalEffectService, reset_external_effect_fixture_state


def _json_response_payload(response: Any) -> dict[str, Any]:
    assert isinstance(response, JSONResponse)
    return json.loads(response.body.decode("utf-8"))


class FakeExternalCampaignRepository:
    def __init__(self) -> None:
        self.pool_rows: dict[str, dict[str, Any]] = {}
        self.identity_by_external: dict[str, dict[str, Any]] = {}
        self.identity_by_unionid: dict[str, dict[str, Any]] = {}
        self.dnd_reasons: dict[str, list[dict[str, Any]]] = {}
        self.broadcast_jobs_by_idempotency_key: dict[str, dict[str, Any]] = {}
        self.member_rows: dict[str, dict[str, Any]] = {}
        self.contact_rows: dict[str, dict[str, Any]] = {}
        self.backfill_rows: dict[str, dict[str, Any]] = {}
        self.campaigns_by_code: dict[str, dict[str, Any]] = {}
        self.campaigns_by_id: dict[int, dict[str, Any]] = {}
        self.overview: dict[str, Any] | None = None
        self.calls: list[str] = []
        self.write_calls: list[str] = []
        self.commits = 0
        self.rollbacks = 0
        self.real_outbound_send_called = False

    def table_columns(self, table_name: str) -> set[str]:
        return {
            "id",
            "unionid",
            "owner_staff_id",
            "current_step_index",
            "trace_id",
            "master_customer_id",
            "source_type",
        }

    def fetch_send_target_by_unionid(self, unionid: str) -> dict[str, Any] | None:
        self.calls.append("fetch_send_target_by_unionid")
        row = self.identity_by_unionid.get(unionid)
        return dict(row) if row else None

    def fetch_send_target_by_external_userid(self, external_userid: str) -> dict[str, Any] | None:
        self.calls.append("fetch_send_target_by_external_userid")
        row = self.identity_by_external.get(external_userid)
        return dict(row) if row else None

    def fetch_do_not_disturb_reasons(self, unionid: str) -> list[dict[str, Any]]:
        self.calls.append("fetch_do_not_disturb_reasons")
        return [dict(item) for item in self.dnd_reasons.get(unionid, [])]

    def fetch_contact_row(self, external_userid: str) -> dict[str, Any]:
        self.calls.append("fetch_contact_row")
        return dict(self.contact_rows.get(external_userid) or {})

    def get_broadcast_job_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any] | None:
        self.calls.append("get_broadcast_job_by_idempotency_key")
        job = self.broadcast_jobs_by_idempotency_key.get(idempotency_key)
        return dict(job) if job else None

    def create_broadcast_job(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("create_broadcast_job")
        self.write_calls.append("create_broadcast_job")
        existing = self.get_broadcast_job_by_idempotency_key(kwargs["idempotency_key"])
        if existing:
            return {**existing, "idempotent_existing": True}
        job_id = 900 + len(self.broadcast_jobs_by_idempotency_key) + 1
        job = {
            "id": job_id,
            "status": "queued",
            **kwargs,
            "target_unionids_json": json.dumps(kwargs.get("target_unionids") or []),
            "content_payload": kwargs.get("content_payload") or {},
            "metadata": kwargs.get("metadata") or {},
        }
        self.broadcast_jobs_by_idempotency_key[kwargs["idempotency_key"]] = job
        return dict(job)

    def get_campaign_by_code(self, campaign_code: str) -> dict[str, Any] | None:
        self.calls.append("get_campaign_by_code")
        item = self.campaigns_by_code.get(campaign_code)
        return dict(item) if item else None

    def get_campaign_by_id(self, campaign_id: int) -> dict[str, Any] | None:
        item = self.campaigns_by_id.get(int(campaign_id))
        return dict(item) if item else None

    def count_open_campaign_jobs(self, campaign_id: int) -> int:
        self.calls.append("count_open_campaign_jobs")
        return 0

    def assemble_campaign_overview(self, campaign_id: int) -> dict[str, Any]:
        self.calls.append("assemble_campaign_overview")
        if self.overview is not None:
            return dict(self.overview)
        campaign = self.campaigns_by_id.get(int(campaign_id)) or {"id": int(campaign_id), "campaign_code": "camp"}
        return {
            "campaign": dict(campaign),
            "segments": [{"segment_code": "seg", "allocated_count": 1, "steps": []}],
            "member_status_counts": {"pending": 1},
            "total_members": 1,
        }

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _payload(**extra: Any) -> dict[str, Any]:
    payload = {
        "owner_userid": "owner_1",
        "external_userid": "ext_1",
        "scheduled_for": "2026-06-09 10:30",
        "message": "hello",
        "idempotency_key": "idem_1",
        "group_code": "group_1",
    }
    payload.update(extra)
    return payload


def _repo_with_target(external_userid: str = "ext_1") -> FakeExternalCampaignRepository:
    repo = FakeExternalCampaignRepository()
    row = {
        "unionid": f"union_{external_userid}",
        "primary_external_userid": external_userid,
        "external_userid": external_userid,
        "primary_owner_userid": "owner_1",
        "owner_userid": "owner_1",
        "customer_name": "Alice",
    }
    repo.identity_by_external[external_userid] = row
    repo.identity_by_unionid[row["unionid"]] = row
    repo.contact_rows[external_userid] = {"external_userid": external_userid, "owner_userid": "owner_1"}
    return repo


def test_external_campaign_response_has_no_route_local_credential_validator(monkeypatch) -> None:
    monkeypatch.setattr(service, "create_external_campaigns", lambda payload: {"ok": True, "entered": True})
    assert service.create_external_campaigns_response(_payload()) == {"ok": True, "entered": True}


def test_external_campaign_dry_run_preview_no_write() -> None:
    reset_external_effect_fixture_state()
    repo = _repo_with_target()
    result = service.create_external_campaigns(_payload(dry_run=True), repo=repo)
    _items, total = ExternalEffectService().list_jobs({"effect_type": AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK})

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["side_effect_executed"] is False
    assert result["send_path"] == "ai_assist_pending_review"
    assert result["required_send_path"] == "ai_assist_pending_review"
    assert result["forbidden_send_path"] == "direct_broadcast_job"
    assert result["review_required"] is True
    assert result["review_status"] == "pending_review"
    assert result["run_status"] == "draft"
    assert result["jobs"] == []
    assert result["previews"][0]["status"] == "preview"
    assert repo.write_calls == []
    assert total == 0


def test_external_campaign_create_requires_ai_assist_review_without_direct_job() -> None:
    reset_external_effect_fixture_state()
    repo = _repo_with_target()
    try:
        service.create_external_campaigns(_payload(), repo=repo)
    except service.ExternalCampaignError as exc:
        payload = exc.to_response()
        status_code = exc.status_code
    else:  # pragma: no cover
        raise AssertionError("expected ExternalCampaignError")
    _items, total = ExternalEffectService().list_jobs({"effect_type": AI_ASSIST_CAMPAIGN_MESSAGE_LOOPBACK})

    assert status_code == 409
    assert payload["error"] == "ai_assist_review_required"
    assert payload["phase"] == "ai_assist_review_guard"
    assert payload["send_path"] == "ai_assist_pending_review"
    assert payload["required_send_path"] == "ai_assist_pending_review"
    assert payload["forbidden_send_path"] == "direct_broadcast_job"
    assert payload["review_required"] is True
    assert payload["review_status"] == "pending_review"
    assert payload["run_status"] == "draft"
    assert payload["scheduled_jobs"] == 0
    assert payload["recipient_count"] == 1
    assert payload["previews"][0]["unionid"] == "union_ext_1"
    assert payload["previews"][0]["external_userid"] == "ext_1"
    assert repo.write_calls == []
    assert repo.commits == 0
    assert repo.rollbacks == 1
    assert "fetch_user_ops_pool_current_row" not in repo.calls
    assert repo.real_outbound_send_called is False
    assert total == 0


def test_external_campaign_allows_attachment_only_step() -> None:
    repo = _repo_with_target()
    payload = _payload(
        message="",
        steps=[
            {
                "scheduled_for": "2026-06-09 10:30",
                "content_payload": {"miniprogram_library_ids": [17]},
            }
        ],
    )

    try:
        service.create_external_campaigns(payload, repo=repo)
    except service.ExternalCampaignError as exc:
        result = exc.to_response()
    else:  # pragma: no cover
        raise AssertionError("expected ExternalCampaignError")

    assert result["error"] == "ai_assist_review_required"
    assert result["previews"][0]["jobs"][0]["content_summary"] == "attachments:miniprogram_library_ids=1"
    assert repo.broadcast_jobs_by_idempotency_key == {}


def test_external_campaign_contact_lookup_is_optional_when_wecom_tables_missing() -> None:
    class MissingContactTablesDb:
        def __init__(self) -> None:
            self.rolled_back = False

        def execute(self, sql: str, params=()):
            assert "wecom_external_contact_identity_map" in sql
            raise RuntimeError("relation does not exist")

        def rollback(self) -> None:
            self.rolled_back = True

    db = MissingContactTablesDb()
    repo = PostgresExternalCampaignRepository(db=db)

    assert repo.fetch_contact_row("wm_missing") == {}
    assert db.rolled_back is True


def test_campaign_private_broadcast_job_fields_are_complete() -> None:
    from aicrm_next.extensions.growth.cloud_orchestrator.repository import (
        _campaign_private_broadcast_job_extra_fields,
        _campaign_private_broadcast_payload,
    )

    columns, placeholders, params = _campaign_private_broadcast_job_extra_fields(
        {"business_domain", "channel", "target_kind"}
    )
    payload = _campaign_private_broadcast_payload(
        campaign={"owner_userid": "owner_1"},
        step={"content_text": "", "content_payload_json": {"miniprogram_library_ids": [17]}},
        members=[{"unionid": "union_1"}],
    )

    assert columns == ["business_domain", "channel", "target_kind"]
    assert placeholders == ["%s", "%s", "%s"]
    assert params == ["automation_ops", "wecom_private", "unionid"]
    assert payload["channel"] == "wecom_private"
    assert payload["target_kind"] == "unionid"
    assert payload["step"]["content_payload_json"] == {"miniprogram_library_ids": [17]}


def test_external_campaign_repeated_create_still_requires_review_and_stays_read_only() -> None:
    repo = _repo_with_target()
    for _ in range(2):
        try:
            service.create_external_campaigns(_payload(campaign_code="fixed_code"), repo=repo)
        except service.ExternalCampaignError as exc:
            payload = exc.to_response()
        else:  # pragma: no cover
            raise AssertionError("expected ExternalCampaignError")

        assert payload["error"] == "ai_assist_review_required"
        assert payload["campaign_code"] == "fixed_code"
    assert repo.write_calls == []
    assert repo.commits == 0


def test_direct_wecom_private_send_dry_run_no_write() -> None:
    repo = _repo_with_target()

    result = service.create_direct_wecom_private_send(_payload(dry_run=True), repo=repo)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["side_effect_executed"] is False
    assert result["jobs"][0]["status"] == "preview"
    assert result["jobs"][0]["external_userid"] == "ext_1"
    assert repo.write_calls == []
    assert repo.commits == 0


def test_direct_wecom_private_send_content_required() -> None:
    repo = _repo_with_target()

    try:
        service.create_direct_wecom_private_send(_payload(message="", content_payload={}), repo=repo)
    except service.ExternalCampaignError as exc:
        payload = exc.to_response()
        status_code = exc.status_code
    else:  # pragma: no cover
        raise AssertionError("expected ExternalCampaignError")

    assert payload["error"] == "content_required"
    assert payload["phase"] == "content_validation"
    assert status_code == 400
    assert repo.write_calls == []


def test_direct_wecom_private_send_rejects_unresolved_material_refs() -> None:
    repo = _repo_with_target()

    try:
        service.create_direct_wecom_private_send(_payload(message="", material_asset_ids=["asset_without_type"]), repo=repo)
    except service.ExternalCampaignError as exc:
        payload = exc.to_response()
        status_code = exc.status_code
    else:  # pragma: no cover
        raise AssertionError("expected ExternalCampaignError")

    assert payload["error"] == "material_invalid"
    assert payload["phase"] == "content_validation"
    assert status_code == 400
    assert repo.write_calls == []


def test_direct_wecom_private_send_dnd_blocks_by_default() -> None:
    repo = _repo_with_target()
    repo.dnd_reasons["union_ext_1"] = [{"reason_code": "manual_pause", "reason_text": "paused"}]

    try:
        service.create_direct_wecom_private_send(_payload(), repo=repo)
    except service.ExternalCampaignError as exc:
        payload = exc.to_response()
        status_code = exc.status_code
    else:  # pragma: no cover
        raise AssertionError("expected ExternalCampaignError")

    assert payload["error"] == "do_not_disturb"
    assert status_code == 409
    assert repo.write_calls == []


def test_direct_wecom_private_send_bypass_dnd_warns_and_queues() -> None:
    repo = _repo_with_target()
    repo.dnd_reasons["union_ext_1"] = [{"reason_code": "manual_pause", "reason_text": "paused"}]

    result = service.create_direct_wecom_private_send(_payload(bypass_dnd=True), repo=repo)

    assert result["created_count"] == 1
    assert result["jobs"][0]["status"] == "queued"
    assert result["jobs"][0]["warnings"][0]["code"] == "do_not_disturb_bypassed"
    assert repo.write_calls == ["create_broadcast_job"]


def test_external_campaign_target_identity_not_found() -> None:
    repo = FakeExternalCampaignRepository()

    try:
        service.create_external_campaigns(_payload(), repo=repo)
    except service.ExternalCampaignError as exc:
        payload = exc.to_response()
        status_code = exc.status_code
    else:  # pragma: no cover
        raise AssertionError("expected ExternalCampaignError")

    assert payload["error"] == "target_identity_not_found"
    assert payload["phase"] == "target_lookup"
    assert status_code == 404


def test_external_campaign_owner_mismatch_does_not_prevalidate() -> None:
    repo = _repo_with_target()
    repo.identity_by_external["ext_1"]["owner_userid"] = "owner_2"
    repo.identity_by_external["ext_1"]["primary_owner_userid"] = "owner_2"
    repo.contact_rows["ext_1"] = {"external_userid": "ext_1", "owner_userid": "owner_2"}

    result = service.create_external_campaigns(_payload(dry_run=True), repo=repo)

    assert result["ok"] is True
    assert result["previews"][0]["warnings"] == []
    assert repo.write_calls == []


def test_external_campaign_ignores_legacy_strict_owner_match_flag() -> None:
    repo = _repo_with_target()
    repo.identity_by_external["ext_1"]["owner_userid"] = "owner_2"
    repo.identity_by_external["ext_1"]["primary_owner_userid"] = "owner_2"

    result = service.create_external_campaigns(_payload(strict_owner_match=True, dry_run=True), repo=repo)

    assert result["ok"] is True
    assert result["previews"][0]["warnings"] == []


def test_external_campaign_automation_member_backfill_is_retired() -> None:
    repo = FakeExternalCampaignRepository()
    repo.backfill_rows["ext_1"] = {"source": "sidebar_binding", "owner_userid": "owner_1", "customer_name": "Alice"}

    try:
        service.create_external_campaigns(_payload(dry_run=True, auto_backfill_automation_member=True), repo=repo)
    except service.ExternalCampaignError as exc:
        payload = exc.to_response()
        status_code = exc.status_code
    else:  # pragma: no cover
        raise AssertionError("expected ExternalCampaignError")

    assert status_code == 410
    assert payload["error"] == "automation_member_backfill_retired"
    assert "insert_automation_member" not in repo.write_calls
    assert repo.write_calls == []


def test_external_campaign_multi_recipient_campaign_code_suffix() -> None:
    repo = _repo_with_target("ext_1")
    row = {
        "unionid": "union_ext_2",
        "primary_external_userid": "ext_2",
        "external_userid": "ext_2",
        "primary_owner_userid": "owner_1",
        "owner_userid": "owner_1",
    }
    repo.identity_by_external["ext_2"] = row
    repo.identity_by_unionid["union_ext_2"] = row
    repo.contact_rows["ext_2"] = {"external_userid": "ext_2", "owner_userid": "owner_1"}
    result = service.create_external_campaigns(
        _payload(dry_run=True, campaign_code="fixed_code", recipients=["ext_1", "ext_2"]),
        repo=repo,
    )

    assert len(result["previews"]) == 2
    assert result["jobs"] == []
    assert {item["unionid"] for item in result["previews"]} == {"union_ext_1", "union_ext_2"}
    assert repo.write_calls == []


def test_external_campaign_workflow_flag_is_retired() -> None:
    repo = _repo_with_target()

    try:
        service.create_external_campaigns(_payload(use_campaign_workflow=True), repo=repo)
    except service.ExternalCampaignError as exc:
        payload = exc.to_response()
        status_code = exc.status_code
    else:  # pragma: no cover
        raise AssertionError("expected ExternalCampaignError")

    assert status_code == 410
    assert payload["error"] == "campaign_workflow_retired"
    assert payload["send_path"] == "ai_assist_pending_review"
    assert payload["required_send_path"] == "ai_assist_pending_review"
    assert payload["forbidden_send_path"] == "direct_broadcast_job"
    assert payload["retired_path"] == "campaign_workflow"
    assert repo.write_calls == []


def test_external_campaign_status_uses_next_repo() -> None:
    repo = FakeExternalCampaignRepository()
    repo.campaigns_by_code["camp_1"] = {"id": 7, "campaign_code": "camp_1", "review_status": "pending_review", "run_status": "draft"}
    repo.campaigns_by_id[7] = repo.campaigns_by_code["camp_1"]
    result = service.get_external_campaign_status("camp_1", repo=repo)

    assert result["ok"] is True
    assert result["campaign"]["campaign_code"] == "camp_1"
    assert result["segments"]
    assert result["member_status_counts"] == {"pending": 1}
    assert result["scheduled_jobs"] == 0
    assert "assemble_campaign_overview" in repo.calls


def test_external_campaign_status_not_found() -> None:
    repo = FakeExternalCampaignRepository()

    try:
        service.get_external_campaign_status("missing", repo=repo)
    except service.ExternalCampaignError as exc:
        payload = exc.to_response()
        status_code = exc.status_code
    else:  # pragma: no cover
        raise AssertionError("expected ExternalCampaignError")

    assert payload["error"] == "campaign_not_found"
    assert status_code == 404
