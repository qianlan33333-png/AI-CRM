from __future__ import annotations

import copy
import hashlib
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from aicrm_next.automation.background_jobs import broadcast_queue_worker
from aicrm_next.crm.identity_contact.campaign_admission_port import PostgresCampaignAdmissionPort
from aicrm_next.engagement.media_library.dynamic_card_port import PostgresDynamicCardMediaPort
from aicrm_next.engagement.send_content.application import normalize_send_content_package
from aicrm_next.extensions.ai.ai_assist.campaign_preparations import (
    CampaignPreparationError,
    commit_campaign_preparation,
    create_campaign_preparation,
)
from aicrm_next.extensions.ai.ai_assist.campaign_preparations_repo import (
    PostgresCampaignPreparationRepository,
)
from aicrm_next.extensions.growth.cloud_orchestrator.campaign_preparation_port import (
    CampaignPreparationCommitError,
    PostgresCampaignPreparationCommandPort,
)
from aicrm_next.extensions.growth.cloud_orchestrator.repository import PostgresCloudPlanRepository
from aicrm_next.operation_cycle_fact_composition import operation_cycle_system_fact_consumer
from aicrm_next.platform.platform_foundation.internal_events.models import (
    InternalEvent,
    InternalEventConsumerRun,
)
from aicrm_next.platform.shared.db_session import get_session_factory
from aicrm_next.platform.shared.query_telemetry import request_query_telemetry_scope
from sqlalchemy import text


CONTEXT_HASH = "b" * 64
OWNER = "HuangYouCan"


class MemoryPreparationRepo:
    def __init__(self) -> None:
        self.details: dict[str, dict[str, Any]] = {}
        self.by_key: dict[str, str] = {}
        self.internal_rows: dict[str, list[dict[str, Any]]] = {}

    def cleanup_expired_staging(self) -> None:
        return None

    def get(self, preparation_id: str):
        item = self.details.get(preparation_id)
        return copy.deepcopy(item) if item else None

    def get_by_idempotency_key(self, idempotency_key: str):
        preparation_id = self.by_key.get(idempotency_key)
        return self.get(preparation_id) if preparation_id else None

    def create(self, preparation: dict[str, Any], rows: list[dict[str, Any]]):
        preparation_id = preparation["preparation_id"]
        detail = {
            "ok": True,
            "preparation_id": preparation_id,
            "preparation_hash": preparation["preparation_hash"],
            "strategy_key": preparation["strategy_key"],
            "strategy_version": preparation["strategy_version"],
            "context_hash": preparation["context_hash"],
            "status": preparation["status"],
            "scheduled_for": preparation["scheduled_for"],
            "timezone": preparation["timezone"],
            "input_count": preparation["input_count"],
            "eligible_count": preparation["eligible_count"],
            "skipped_count": preparation["skipped_count"],
            "counts": copy.deepcopy(preparation["counts"]),
            "blockers": copy.deepcopy(preparation["blockers"]),
            "timings_ms": copy.deepcopy(preparation["timings_ms"]),
            "sql_batch_count": preparation["sql_batch_count"],
            "plan_id": "",
            "rows": [
                {
                    key: row[key]
                    for key in ("row_key", "identity_status", "policy_status", "row_status", "reason_code")
                }
                for row in rows
            ],
        }
        self.details[preparation_id] = detail
        self.by_key[preparation["idempotency_key"]] = preparation_id
        self.internal_rows[preparation_id] = copy.deepcopy(rows)
        return copy.deepcopy(detail)


class FakeContextPort:
    version = 1
    context_hash = CONTEXT_HASH

    def get_execution_context(self, strategy_key: str):
        return {
            "strategy_key": strategy_key,
            "strategy_version": self.version,
            "context_hash": self.context_hash,
            "execution_contract": {
                "review_required": True,
                "auto_approve_allowed": False,
                "direct_broadcast_jobs_allowed": False,
                "allowed_owner_userids": [OWNER],
                "max_weekly_private_messages": 2,
            },
        }


class FakeAdmissionPort:
    def evaluate(self, inputs, **kwargs):
        del kwargs
        return {
            item["row_key"]: {
                "identity_status": "resolved",
                "policy_status": "eligible",
                "reason_code": "eligible",
                "resolved_unionid": f"union_{item['row_key']}",
                "resolved_external_userid": f"external_{item['row_key']}",
                "resolved_owner_userid": OWNER,
                "weekly_touch_count": 0,
            }
            for item in inputs
        }


class FakeTargetPort:
    def validate_targets(self, cards):
        return {str(card["row_key"]): "" for card in cards}


class FakeMediaPort:
    def validate_cover_ids(self, cover_image_ids):
        return {int(value): "" for value in cover_image_ids}

    def resolve_attachment(self, card):
        return {"msgtype": "miniprogram", "miniprogram": {"page": card["pagepath"]}}


def _payload(size: int = 2) -> dict[str, Any]:
    return {
        "schema_version": "external_campaign_preparation.v1",
        "idempotency_key": f"campaign-20260728-{size}",
        "strategy_key": "hxc_monday_abcd",
        "strategy_version": 1,
        "context_hash": CONTEXT_HASH,
        "md_source_hash": "a" * 64,
        "run_key": "hxc_monday_abcd_20260728",
        "owner_userid": OWNER,
        "scheduled_for": "2026-07-28T14:00:00+08:00",
        "timezone": "Asia/Shanghai",
        "display_name": "2026-07-28 HuangYouCan ABCD 14:00 AI助手审阅",
        "rows": [
            {
                "row_key": f"row-{index}",
                "identity": {"external_userid": f"external-{index}"},
                "content_text": f"第 {index} 条定制话术",
                "card": {
                    "schema_version": "dynamic_miniprogram_card.v1",
                    "appid": "wx123",
                    "title": f"本周复盘 {index}",
                    "pagepath": (
                        f"/pages/review/index?rid=review-{index}&kind=review"
                        f"&cid=cid-{index}&ch=hxc&src=push"
                    ),
                    "card_id": f"card-{index}",
                    "cid": f"cid-{index}",
                    "cover_image_id": 90,
                },
                "group": "A",
                "reason_code": "weekly_review_new_plan",
                "analysis": {"active7": True, "remaining_days": 90},
            }
            for index in range(size)
        ],
    }


def _prepare(
    payload: dict[str, Any],
    repo: MemoryPreparationRepo,
    *,
    target_port: Any | None = None,
    media_port: Any | None = None,
):
    return create_campaign_preparation(
        payload,
        actor_id="api_client:campaign_agent",
        repo=repo,
        context_port=FakeContextPort(),
        admission_port=FakeAdmissionPort(),
        target_port=target_port or FakeTargetPort(),
        media_port=media_port or FakeMediaPort(),
    )


def test_prepare_1500_unique_cards_is_conservative_and_fast():
    repo = MemoryPreparationRepo()
    started = time.perf_counter()
    result = _prepare(_payload(1500), repo)
    elapsed = time.perf_counter() - started

    assert result["status"] == "ready"
    assert result["counts"] == {
        "input": 1500,
        "eligible": 1500,
        "skipped": 0,
        "blocked": 0,
        "reason_codes": {"eligible": 1500},
    }
    assert len({row["dynamic_card_json"]["cid"] for row in repo.internal_rows[result["preparation_id"]]}) == 1500
    assert elapsed < 6


def test_prepare_is_idempotent_and_payload_change_conflicts():
    repo = MemoryPreparationRepo()
    payload = _payload(1)
    first = _prepare(payload, repo)
    second = _prepare(payload, repo)
    assert second["preparation_id"] == first["preparation_id"]
    assert second["idempotent_existing"] is True

    changed = copy.deepcopy(payload)
    changed["rows"][0]["content_text"] = "不同话术"
    with pytest.raises(CampaignPreparationError, match="idempotency_payload_conflict"):
        _prepare(changed, repo)


def test_duplicate_cid_blocks_entire_preparation():
    repo = MemoryPreparationRepo()
    payload = _payload(2)
    payload["rows"][1]["card"]["cid"] = payload["rows"][0]["card"]["cid"]
    payload["rows"][1]["card"]["pagepath"] = payload["rows"][0]["card"]["pagepath"]
    result = _prepare(payload, repo)
    assert result["status"] == "blocked"
    assert result["counts"]["eligible"] == 0
    assert result["counts"]["blocked"] == 2
    assert result["blockers"] == [{"code": "duplicate_cid", "count": 2}]


@pytest.mark.parametrize(
    ("mutate", "target_port", "media_port", "expected_code"),
    [
        (
            lambda payload: payload["rows"][0]["card"].update(
                {"pagepath": "/pages/review/index?kind=review&cid=cid-0&ch=hxc&src=push"}
            ),
            None,
            None,
            "pagepath_rid_missing",
        ),
        (
            lambda payload: None,
            type(
                "MissingTargetPort",
                (),
                {"validate_targets": lambda self, cards: {cards[0]["row_key"]: "target_not_found"}},
            )(),
            None,
            "target_not_found",
        ),
        (
            lambda payload: None,
            None,
            type(
                "UnavailableCoverPort",
                (),
                {
                    "validate_cover_ids": lambda self, cover_image_ids: {
                        int(value): "cover_unavailable" for value in cover_image_ids
                    }
                },
            )(),
            "cover_unavailable",
        ),
    ],
)
def test_path_target_and_material_failures_block_the_batch(
    mutate,
    target_port,
    media_port,
    expected_code,
):
    repo = MemoryPreparationRepo()
    payload = _payload(1)
    mutate(payload)
    result = _prepare(
        payload,
        repo,
        target_port=target_port,
        media_port=media_port,
    )
    assert result["status"] == "blocked"
    assert result["counts"]["blocked"] == 1
    assert result["blockers"] == [{"code": expected_code, "count": 1}]


def test_dynamic_card_title_enforces_wecom_utf8_byte_limit():
    payload = _payload(1)
    payload["rows"][0]["card"]["title"] = "复" * 22
    with pytest.raises(ValueError, match="title exceeds 64 UTF-8 bytes"):
        _prepare(payload, MemoryPreparationRepo())


def test_commit_rechecks_strategy_and_delegates_to_cloud_command_port():
    repo = MemoryPreparationRepo()
    prepared = _prepare(_payload(2), repo)

    class FakeCommandPort:
        def commit(self, preparation_id, *, preparation_hash, actor_id):
            assert preparation_id == prepared["preparation_id"]
            assert preparation_hash == prepared["preparation_hash"]
            assert actor_id == "agent"
            return {
                "ok": True,
                "status": "created",
                "plan_id": "plan_1",
                "review_status": "pending_review",
                "run_status": "draft",
                "recipient_count": 2,
                "message_count": 2,
                "broadcast_jobs": 0,
            }

    result = commit_campaign_preparation(
        prepared["preparation_id"],
        {"preparation_hash": prepared["preparation_hash"]},
        actor_id="agent",
        repo=repo,
        context_port=FakeContextPort(),
        command_port=FakeCommandPort(),
    )
    assert result["review_status"] == "pending_review"
    assert result["run_status"] == "draft"
    assert result["broadcast_jobs"] == 0


def test_dynamic_card_is_preserved_and_worker_resolves_shared_cover(monkeypatch):
    card = _payload(1)["rows"][0]["card"]
    package = normalize_send_content_package(
        {"content_text": "你好", "dynamic_miniprogram_card": card}
    )
    assert package["dynamic_miniprogram_card"]["cid"] == "cid-0"

    monkeypatch.setenv("AICRM_DYNAMIC_MINIPROGRAM_CARD_V1_ENABLED", "true")
    monkeypatch.setattr(
        broadcast_queue_worker,
        "_dynamic_miniprogram_attachment_resolver",
        FakeMediaPort().resolve_attachment,
    )
    hydrated = broadcast_queue_worker._with_dynamic_miniprogram_attachment(
        {"content_payload_json": {"content_package": package}}
    )
    assert hydrated["attachments"][0]["msgtype"] == "miniprogram"
    assert hydrated["attachments"][0]["miniprogram"]["page"].endswith("ch=hxc&src=push")


def test_postgres_1500_row_persist_and_atomic_commit_stay_within_budget(next_pg_schema):
    del next_pg_schema
    preparation_id = "ecprep_pytest_1500_dynamic_cards"
    preparation_hash = "c" * 64
    plan_id = "plan_campaign_" + hashlib.sha256(
        f"{preparation_id}\0{preparation_hash}".encode("utf-8")
    ).hexdigest()[:28]
    scheduled_for = datetime.now(timezone.utc) + timedelta(hours=1)
    Session = get_session_factory()
    with Session.begin() as session:
        session.execute(
            text("DELETE FROM operation_cycle_system_facts WHERE plan_id = :plan_id"),
            {"plan_id": plan_id},
        )
        session.execute(
            text("DELETE FROM operation_cycle_plan_links WHERE plan_id = :plan_id"),
            {"plan_id": plan_id},
        )
        session.execute(
            text("DELETE FROM cloud_broadcast_plans WHERE plan_id = :plan_id"),
            {"plan_id": plan_id},
        )
        session.execute(
            text("DELETE FROM external_campaign_preparations WHERE preparation_id = :preparation_id"),
            {"preparation_id": preparation_id},
        )
        material_count_before = int(
            session.execute(text("SELECT COUNT(*) FROM miniprogram_library")).scalar_one()
        )

    rows = []
    for index in range(1500):
        cid = f"cid-pg-{index}"
        row_key = f"row-pg-{index}"
        rows.append(
            {
                "row_key": row_key,
                "identity_external_userid": f"external-pg-{index}",
                "identity_unionid": f"union-pg-{index}",
                "identity_mobile_normalized": "",
                "resolved_external_userid": f"external-pg-{index}",
                "resolved_unionid": f"union-pg-{index}",
                "resolved_owner_userid": OWNER,
                "identity_status": "resolved",
                "policy_status": "eligible",
                "row_status": "eligible",
                "reason_code": "eligible",
                "content_text": f"第 {index} 条数据库基准话术",
                "dynamic_card_json": {
                    "schema_version": "dynamic_miniprogram_card.v1",
                    "appid": "wx123",
                    "title": f"本周复盘 {index}",
                    "pagepath": (
                        f"/pages/review/index?rid=review-pg-{index}&kind=review"
                        f"&cid={cid}&ch=hxc&src=push"
                    ),
                    "card_id": f"card-pg-{index}",
                    "cid": cid,
                    "cover_image_id": 90,
                },
                "analysis_json": {"group": "A", "reason_code": "weekly_review_new_plan"},
                "row_hash": hashlib.sha256(row_key.encode("utf-8")).hexdigest(),
            }
        )

    repository = PostgresCampaignPreparationRepository()
    persist_started = time.perf_counter()
    preparation_header = {
        "preparation_id": preparation_id,
        "idempotency_key": "pytest-campaign-preparation-1500",
        "preparation_hash": preparation_hash,
        "source_hash": "d" * 64,
        "strategy_key": "hxc_monday_abcd",
        "strategy_version": 1,
        "context_hash": "e" * 64,
        "run_key": "hxc_monday_abcd_pytest",
        "owner_userid": OWNER,
        "scheduled_for": scheduled_for,
        "timezone": "Asia/Shanghai",
        "display_name": "pytest 1500 AI助手审阅",
        "status": "ready",
        "input_count": 1500,
        "eligible_count": 1500,
        "skipped_count": 0,
        "counts": {"input": 1500, "eligible": 1500, "skipped": 0, "blocked": 0},
        "blockers": [],
        "timings_ms": {"identity": 1, "policy": 1, "path": 1, "material": 1},
        "sql_batch_count": 3,
        "created_by": "pytest",
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=24),
    }
    with request_query_telemetry_scope() as query_telemetry:
        preparation_header["_query_count_started"] = query_telemetry.snapshot().query_count
        prepared = repository.create(preparation_header, rows)
        measured_sql_batch_count = query_telemetry.snapshot().query_count
    persist_elapsed = time.perf_counter() - persist_started
    assert prepared["eligible_count"] == 1500
    assert prepared["sql_batch_count"] == measured_sql_batch_count
    assert persist_elapsed < 6

    commit_started = time.perf_counter()
    committed = PostgresCampaignPreparationCommandPort().commit(
        preparation_id,
        preparation_hash=preparation_hash,
        actor_id="pytest",
    )
    commit_elapsed = time.perf_counter() - commit_started
    assert committed == {
        "ok": True,
        "status": "created",
        "plan_id": plan_id,
        "review_status": "pending_review",
        "run_status": "draft",
        "recipient_count": 1500,
        "message_count": 1500,
        "broadcast_jobs": 0,
    }
    assert commit_elapsed < 2

    reused = PostgresCampaignPreparationCommandPort().commit(
        preparation_id,
        preparation_hash=preparation_hash,
        actor_id="pytest",
    )
    assert reused["status"] == "reused"
    assert reused["plan_id"] == plan_id
    with pytest.raises(CampaignPreparationCommitError, match="preparation_hash_conflict"):
        PostgresCampaignPreparationCommandPort().commit(
            preparation_id,
            preparation_hash="f" * 64,
            actor_id="pytest",
        )

    with Session() as session:
        state = session.execute(
            text(
                """
                SELECT plan.review_status, plan.run_status,
                       COUNT(message.id)::integer AS message_count,
                       COUNT(DISTINCT message.content_payload_json->'dynamic_miniprogram_card'->>'cid')::integer AS cid_count
                FROM cloud_broadcast_plans plan
                JOIN cloud_broadcast_plan_recipient_messages message ON message.plan_id = plan.plan_id
                WHERE plan.plan_id = :plan_id
                GROUP BY plan.review_status, plan.run_status
                """
            ),
            {"plan_id": plan_id},
        ).mappings().one()
        material_count_after = int(
            session.execute(text("SELECT COUNT(*) FROM miniprogram_library")).scalar_one()
        )
    assert dict(state) == {
        "review_status": "pending_review",
        "run_status": "draft",
        "message_count": 1500,
        "cid_count": 1500,
    }
    assert material_count_after == material_count_before

    with Session.begin() as session:
        session.execute(
            text(
                "UPDATE cloud_broadcast_plans SET review_status = 'approved' "
                "WHERE plan_id = :plan_id"
            ),
            {"plan_id": plan_id},
        )
        recipient_id = int(
            session.execute(
                text(
                    "SELECT id FROM cloud_broadcast_plan_recipients "
                    "WHERE plan_id = :plan_id ORDER BY id LIMIT 1"
                ),
                {"plan_id": plan_id},
            ).scalar_one()
        )
    approved_recipient = PostgresCloudPlanRepository().approve_recipient(
        plan_id,
        recipient_id,
        operator="pytest",
    )
    with Session() as session:
        queued_at = session.execute(
            text("SELECT scheduled_for FROM broadcast_jobs WHERE id = :job_id"),
            {"job_id": int(approved_recipient["job_id"])},
        ).scalar_one()
    assert queued_at == scheduled_for

    with Session.begin() as session:
        session.execute(
            text(
                "UPDATE external_campaign_preparations "
                "SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' "
                "WHERE preparation_id = :preparation_id"
            ),
            {"preparation_id": preparation_id},
        )
    repository.cleanup_expired_staging()
    with Session() as session:
        retained_header = session.execute(
            text(
                "SELECT status, plan_id FROM external_campaign_preparations "
                "WHERE preparation_id = :preparation_id"
            ),
            {"preparation_id": preparation_id},
        ).mappings().one()
        staging_count = int(
            session.execute(
                text(
                    "SELECT COUNT(*) FROM external_campaign_preparation_recipients "
                    "WHERE preparation_id = :preparation_id"
                ),
                {"preparation_id": preparation_id},
            ).scalar_one()
        )
    assert dict(retained_header) == {"status": "committed", "plan_id": plan_id}
    assert staging_count == 0
    retained_retry = PostgresCampaignPreparationCommandPort().commit(
        preparation_id,
        preparation_hash=preparation_hash,
        actor_id="pytest",
    )
    assert retained_retry["status"] == "reused"
    assert retained_retry["plan_id"] == plan_id


def test_postgres_batch_admission_uses_current_identity_follow_dnd_and_frequency(next_pg_schema):
    del next_pg_schema
    Session = get_session_factory()
    identities = [
        ("union-campaign-eligible", "external-campaign-eligible"),
        ("union-campaign-dnd", "external-campaign-dnd"),
        ("union-campaign-cap", "external-campaign-cap"),
        ("union-campaign-not-follow", "external-campaign-not-follow"),
        ("union-campaign-conflict", "external-campaign-conflict"),
    ]
    with Session.begin() as session:
        session.execute(
            text(
                "DELETE FROM wecom_external_contact_follow_users "
                "WHERE external_userid LIKE 'external-campaign-%'"
            )
        )
        session.execute(
            text("DELETE FROM user_ops_do_not_disturb_next WHERE unionid LIKE 'union-campaign-%'")
        )
        session.execute(
            text("DELETE FROM crm_user_identity WHERE unionid LIKE 'union-campaign-%'")
        )
        for unionid, external_userid in identities:
            session.execute(
                text(
                    """
                    INSERT INTO crm_user_identity (
                        unionid, primary_external_userid, external_userids_json,
                        identity_status, created_at, updated_at
                    ) VALUES (
                        :unionid, :external_userid,
                        jsonb_build_array(CAST(:external_userid AS text)),
                        'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {"unionid": unionid, "external_userid": external_userid},
            )
        for external_userid in (
            "external-campaign-eligible",
            "external-campaign-dnd",
            "external-campaign-cap",
            "external-campaign-conflict",
        ):
            session.execute(
                text(
                    """
                    INSERT INTO wecom_external_contact_follow_users (
                        external_userid, user_id, relation_status, is_primary
                    ) VALUES (:external_userid, :owner_userid, 'active', TRUE)
                    """
                ),
                {"external_userid": external_userid, "owner_userid": OWNER},
            )
        session.execute(
            text(
                "INSERT INTO user_ops_do_not_disturb_next (unionid, is_active, reason_code) "
                "VALUES ('union-campaign-dnd', TRUE, 'pytest')"
            )
        )
        session.execute(
            text(
                "DELETE FROM broadcast_jobs "
                "WHERE idempotency_key = 'pytest-campaign-frequency-cap'"
            )
        )
        session.execute(
            text(
                """
                INSERT INTO broadcast_jobs (
                    source_type, source_id, source_table, scheduled_for, status,
                    business_domain, idempotency_key, channel, target_kind,
                    target_unionids_json, target_count, content_type, content_payload
                ) VALUES (
                    'cloud_plan', 'campaign-cap', 'cloud_broadcast_plan_recipients',
                    CURRENT_TIMESTAMP, 'sent', 'ai_assistant',
                    'pytest-campaign-frequency-cap', 'wecom_private', 'unionid',
                    '["union-campaign-cap"]'::jsonb, 1, 'cloud_plan', '{}'::jsonb
                )
                """
            )
        )

    result = PostgresCampaignAdmissionPort().evaluate(
        [
            {
                "row_key": "consistent",
                "external_userid": "external-campaign-eligible",
                "unionid": "union-campaign-eligible",
            },
            {"row_key": "dnd", "external_userid": "external-campaign-dnd"},
            {"row_key": "cap", "external_userid": "external-campaign-cap"},
            {"row_key": "not-follow", "external_userid": "external-campaign-not-follow"},
            {
                "row_key": "conflict",
                "external_userid": "external-campaign-eligible",
                "unionid": "union-campaign-conflict",
            },
            {"row_key": "unmatched", "external_userid": "external-campaign-missing"},
        ],
        owner_userid=OWNER,
        week_started_at=datetime.now(timezone.utc) - timedelta(days=1),
        weekly_limit=1,
    )
    assert result["consistent"]["policy_status"] == "eligible"
    assert result["dnd"]["reason_code"] == "do_not_disturb"
    assert result["cap"]["reason_code"] == "weekly_private_message_cap"
    assert result["not-follow"]["reason_code"] == "owner_current_follow_missing"
    assert result["conflict"]["identity_status"] == "identity_conflict"
    assert result["unmatched"]["identity_status"] == "unmatched"


def test_postgres_dynamic_card_media_validation_reuses_one_available_cover(next_pg_schema):
    del next_pg_schema
    Session = get_session_factory()
    with Session.begin() as session:
        cover_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO image_library (
                        name, file_name, data_base64, mime_type, enabled
                    ) VALUES (
                        'pytest dynamic campaign cover', 'campaign-cover.png',
                        'cHl0ZXN0', 'image/png', TRUE
                    ) RETURNING id
                    """
                )
            ).scalar_one()
        )
    missing_id = cover_id + 10_000_000
    result = PostgresDynamicCardMediaPort().validate_cover_ids(
        [cover_id, cover_id, missing_id]
    )
    assert result == {
        cover_id: "",
        missing_id: "cover_image_not_found",
    }


def test_operation_cycle_fact_projection_is_idempotent_and_order_independent(
    next_pg_schema,
    monkeypatch,
):
    del next_pg_schema
    monkeypatch.setenv("AICRM_OPERATION_FACT_PROJECTION_V1_ENABLED", "true")
    plan_id = "plan_campaign_fact_projection_pytest"
    Session = get_session_factory()
    with Session.begin() as session:
        session.execute(
            text("DELETE FROM operation_cycle_system_facts WHERE plan_id = :plan_id"),
            {"plan_id": plan_id},
        )
        session.execute(
            text("DELETE FROM operation_cycle_plan_links WHERE plan_id = :plan_id"),
            {"plan_id": plan_id},
        )
        session.execute(
            text("DELETE FROM cloud_broadcast_plans WHERE plan_id = :plan_id"),
            {"plan_id": plan_id},
        )
        session.execute(
            text(
                "DELETE FROM broadcast_jobs "
                "WHERE idempotency_key = 'pytest-operation-cycle-fact-projection'"
            )
        )
        session.execute(
            text(
                """
                INSERT INTO cloud_broadcast_plans (
                    plan_id, trace_id, session_id, operator, intent, display_name,
                    candidate_count, review_status, run_status, status
                ) VALUES (
                    :plan_id, :plan_id, 'pytest', 'pytest', 'fact projection',
                    'fact projection', 1, 'approved', 'draft', 'draft'
                )
                """
            ),
            {"plan_id": plan_id},
        )
        job_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO broadcast_jobs (
                        source_type, source_id, source_table, scheduled_for, status,
                        business_domain, idempotency_key, channel, target_kind,
                        target_unionids_json, target_count, content_type, content_payload
                    ) VALUES (
                        'cloud_plan', :plan_id, 'cloud_broadcast_plan_recipients',
                        CURRENT_TIMESTAMP, 'sent', 'ai_assistant',
                        :idempotency_key, 'wecom_private', 'unionid',
                        '["union-fact-projection"]'::jsonb, 1, 'cloud_plan',
                        jsonb_build_object('plan_id', CAST(:plan_id AS text))
                    ) RETURNING id
                    """
                ),
                {
                    "plan_id": plan_id,
                    "idempotency_key": "pytest-operation-cycle-fact-projection",
                },
            ).scalar_one()
        )
        session.execute(
            text(
                """
                INSERT INTO cloud_broadcast_plan_recipients (
                    plan_id, unionid, owner_userid, display_name,
                    planned_message_count, approval_status, send_status,
                    broadcast_job_id
                ) VALUES (
                    :plan_id, 'union-fact-projection', :owner_userid, 'pytest',
                    1, 'approved', 'sent', :job_id
                )
                """
            ),
            {"plan_id": plan_id, "owner_userid": OWNER, "job_id": job_id},
        )
        session.execute(
            text(
                """
                INSERT INTO operation_cycle_plan_links (
                    tenant_id, strategy_key, strategy_version, run_key, plan_id
                ) VALUES (
                    'aicrm', 'hxc_monday_abcd', 1, 'fact_projection_pytest', :plan_id
                )
                """
            ),
            {"plan_id": plan_id},
        )

    occurred_at = datetime.now(timezone.utc).isoformat()
    events = [
        InternalEvent(
            event_id="iev_fact_finalized",
            event_type="broadcast_task.finalized",
            aggregate_id=str(job_id),
            occurred_at=occurred_at,
            payload_json={
                "broadcast_task": {
                    "plan_id": plan_id,
                    "status": "sent",
                    "sent_count": 1,
                    "failed_count": 0,
                }
            },
        ),
        InternalEvent(
            event_id="iev_fact_created",
            event_type="broadcast_task.created",
            aggregate_id=str(job_id),
            occurred_at=occurred_at,
            payload_json={"broadcast_task": {"status": "created"}},
        ),
        InternalEvent(
            event_id="iev_fact_approved",
            event_type="ops_plan.approved",
            aggregate_id=plan_id,
            occurred_at=occurred_at,
            payload_json={"plan": {"plan_id": plan_id, "review_status": "approved"}},
        ),
    ]
    run = InternalEventConsumerRun(consumer_name="operation_cycle_system_fact_consumer")
    for event in events:
        assert operation_cycle_system_fact_consumer(event, run).status == "succeeded"
    for event in reversed(events):
        assert operation_cycle_system_fact_consumer(event, run).status == "succeeded"

    with Session() as session:
        projection = session.execute(
            text(
                """
                SELECT task_count, finalized_count, sent_count, failed_count,
                       approved_at IS NOT NULL AS approved,
                       (SELECT COUNT(*) FROM operation_cycle_system_facts fact
                         WHERE fact.plan_id = link.plan_id)::integer AS fact_count
                FROM operation_cycle_plan_links link
                WHERE plan_id = :plan_id
                """
            ),
            {"plan_id": plan_id},
        ).mappings().one()
    assert dict(projection) == {
        "task_count": 1,
        "finalized_count": 1,
        "sent_count": 1,
        "failed_count": 0,
        "approved": True,
        "fact_count": 3,
    }
