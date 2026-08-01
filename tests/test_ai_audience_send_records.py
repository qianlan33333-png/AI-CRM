from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import text

from aicrm_next.extensions.ai.ai_audience_ops.send_records.application import (
    GetAudienceSendRecordQuery,
    ListAudienceSendRecordsQuery,
)
from aicrm_next.platform.shared.db_session import get_session_factory
from tests.admin_auth_test_helpers import admin_session_cookies


class FakeSendRecordRepository:
    def __init__(self, rows: list[dict[str, Any]], *, package_id: int = 7, package_key: str = "pkg-7") -> None:
        self.package_id = package_id
        self.package_key = package_key
        self.rows = rows

    def get_package(self, package_id: int) -> dict[str, Any] | None:
        if int(package_id) != self.package_id:
            return None
        return {"id": self.package_id, "package_key": self.package_key, "name": "测试包"}

    def list_records(self, *, package_id: int, package_key: str, limit: int, offset: int):
        assert package_id == self.package_id
        assert package_key == self.package_key
        return self.rows[offset : offset + limit], len(self.rows)

    def get_record(self, *, package_id: int, package_key: str, record_id: str):
        assert package_id == self.package_id
        assert package_key == self.package_key
        return next((row for row in self.rows if row["record_id"] == record_id), None)


def _row(**overrides: Any) -> dict[str, Any]:
    row = {
        "record_id": "automation:101",
        "source": "agent_bot",
        "nickname": "浅蓝",
        "external_userid": "wm_test_101",
        "raw_status": "succeeded",
        "business_status": "sent",
        "side_effect_executed": True,
        "provider_result_received": True,
        "attempt_count": 2,
        "provider_call_started_at": datetime(2026, 8, 1, 2, 0, tzinfo=timezone.utc),
        "effect_completed_at": datetime(2026, 8, 1, 2, 1, tzinfo=timezone.utc),
        "message_sent_at": datetime(2026, 8, 1, 2, 1, tzinfo=timezone.utc),
        "failure_reason": "",
        "actual_content_text": "完整的自动化话术",
        "message_content_text": "消息快照话术",
        "planned_content_text": "计划话术",
        "actual_attachments": [{"msgtype": "image", "image": {"media_id": "secret-media-id"}}],
        "planned_attachments": [],
        "media_refs": [],
        "planned_content_package": {},
        "actual_content_package": {},
        "effect_materialized": True,
        "business_created_at": datetime(2026, 8, 1, 1, 59, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def test_list_send_records_normalizes_status_time_source_and_pagination() -> None:
    rows = [
        _row(),
        _row(
            record_id="manual:202",
            source="manual_broadcast",
            nickname="",
            raw_status="failed_retryable",
            side_effect_executed=False,
            provider_result_received=False,
            message_sent_at=None,
            effect_completed_at=None,
            failure_reason="provider timeout",
            actual_content_text="手动群发完整话术",
            actual_attachments=[],
        ),
    ]

    result = ListAudienceSendRecordsQuery(FakeSendRecordRepository(rows))(7, limit=1, offset=1)

    assert result["ok"] is True
    assert result["total"] == 2
    assert result["limit"] == 1
    assert result["offset"] == 1
    assert result["items"] == [
        {
            "record_id": "manual:202",
            "nickname": "未命名客户",
            "external_userid": "wm_test_101",
            "source": "manual_broadcast",
            "source_label": "手动群发",
            "status": "retrying",
            "status_label": "重试中",
            "send_time": "2026-08-01T02:00:00Z",
            "failure_reason": "provider timeout",
            "has_attachments": False,
            "detail_available": True,
        }
    ]


def test_send_record_success_requires_real_side_effect_and_provider_receipt() -> None:
    row = _row(side_effect_executed=True, provider_result_received=False, message_sent_at=None)

    item = ListAudienceSendRecordsQuery(FakeSendRecordRepository([row]))(7)["items"][0]

    assert item["status"] == "unknown_after_dispatch"
    assert item["status_label"] == "调用后状态未知"
    assert item["send_time"] == "2026-08-01T02:00:00Z"


@pytest.mark.parametrize(
    ("raw_status", "provider_call_started_at", "expected_status", "has_send_time"),
    [
        ("pending", None, "queued", False),
        ("dispatching", datetime(2026, 8, 1, 2, 0, tzinfo=timezone.utc), "sending", False),
        ("failed_retryable", datetime(2026, 8, 1, 2, 0, tzinfo=timezone.utc), "retrying", True),
        ("failed_terminal", datetime(2026, 8, 1, 2, 0, tzinfo=timezone.utc), "failed", True),
        ("cancelled", None, "cancelled", False),
        ("unknown_after_dispatch", datetime(2026, 8, 1, 2, 0, tzinfo=timezone.utc), "unknown_after_dispatch", True),
        ("simulated", datetime(2026, 8, 1, 2, 0, tzinfo=timezone.utc), "simulated", False),
    ],
)
def test_send_record_status_and_send_time_semantics(
    raw_status: str,
    provider_call_started_at: datetime | None,
    expected_status: str,
    has_send_time: bool,
) -> None:
    row = _row(
        raw_status=raw_status,
        business_status=raw_status,
        side_effect_executed=False,
        provider_result_received=False,
        provider_call_started_at=provider_call_started_at,
        message_sent_at=None,
        effect_completed_at=None,
    )

    item = ListAudienceSendRecordsQuery(FakeSendRecordRepository([row]))(7)["items"][0]

    assert item["status"] == expected_status
    assert bool(item["send_time"]) is has_send_time


def test_send_record_detail_reports_message_snapshot_when_effect_has_no_text() -> None:
    row = _row(actual_content_text="", message_content_text="完整消息快照")

    record = GetAudienceSendRecordQuery(FakeSendRecordRepository([row]))(7, "automation:101")["record"]

    assert record["content_text"] == "完整消息快照"
    assert record["content_basis"] == "message_snapshot"
    assert record["content_basis_label"] == "消息快照"


def test_send_record_detail_sanitizes_materialized_attachments_and_counts_retries() -> None:
    detail = GetAudienceSendRecordQuery(FakeSendRecordRepository([_row()]))(7, "automation:101")

    assert detail["ok"] is True
    record = detail["record"]
    assert record["content_text"] == "完整的自动化话术"
    assert record["content_basis"] == "frozen_effect_payload"
    assert record["attachment_basis"] == "materialized"
    assert record["attachment_count"] == 1
    assert record["attachments"] == [
        {
            "type": "image",
            "type_label": "图片",
            "name": "图片",
            "description": "",
            "thumbnail_url": "",
            "availability": "available",
        }
    ]
    assert record["technical_attempt_count"] == 2
    assert record["technical_retry_count"] == 1
    assert "secret-media-id" not in json.dumps(detail, ensure_ascii=False)


def test_send_record_detail_enriches_frozen_attachment_without_replacing_its_fact_basis() -> None:
    row = _row(planned_content_package={"image_library_ids": [12]})
    detail = GetAudienceSendRecordQuery(
        FakeSendRecordRepository([row]),
        material_previewer=lambda _package: {
            "preview": {
                "materials": [
                    {
                        "type": "image",
                        "name": "发送时课程海报",
                        "thumbnail_url": "/static/course-poster.png",
                    }
                ]
            }
        },
    )(7, "automation:101")

    record = detail["record"]
    assert record["attachment_basis"] == "materialized"
    assert record["attachments"] == [
        {
            "type": "image",
            "type_label": "图片",
            "name": "发送时课程海报",
            "description": "",
            "thumbnail_url": "/static/course-poster.png",
            "availability": "available",
        }
    ]


def test_send_record_detail_uses_planned_material_fallback_when_asset_is_deleted() -> None:
    row = _row(
        record_id="manual:303",
        source="manual_broadcast",
        actual_attachments=[],
        media_refs=[{"kind": "group_invite", "title": "加入学习群", "media_id": "hidden"}],
        planned_content_package={"attachment_library_ids": [56]},
    )
    query = GetAudienceSendRecordQuery(
        FakeSendRecordRepository([row]),
        material_previewer=lambda _package: (_ for _ in ()).throw(RuntimeError("asset deleted")),
    )

    record = query(7, "manual:303")["record"]

    assert record["attachment_basis"] == "planned"
    assert [item["type"] for item in record["attachments"]] == ["attachment", "group_invite"]
    assert all(item["availability"] in {"missing", "planned"} for item in record["attachments"])
    rendered = json.dumps(record, ensure_ascii=False)
    assert "hidden" not in rendered


def test_send_record_detail_keeps_missing_assets_when_preview_is_partial() -> None:
    row = _row(
        actual_attachments=[],
        media_refs=[],
        planned_content_package={"image_library_ids": [12, 13], "miniprogram_library_ids": [21]},
    )
    query = GetAudienceSendRecordQuery(
        FakeSendRecordRepository([row]),
        material_previewer=lambda _package: {
            "preview": {
                "materials": [
                    {"type": "image", "name": "仍可用的海报", "thumbnail_url": "/static/poster.png"}
                ]
            }
        },
    )

    record = query(7, "automation:101")["record"]

    assert record["attachment_count"] == 3
    assert [item["availability"] for item in record["attachments"]] == ["available", "missing", "missing"]
    assert {item["type"] for item in record["attachments"]} == {"image", "miniprogram"}


def test_send_record_detail_resolves_manual_media_refs_without_exposing_library_ids() -> None:
    row = _row(
        record_id="manual:404",
        source="manual_broadcast",
        actual_attachments=[],
        planned_content_package={},
        media_refs=[
            {"kind": "image", "library_id": 12},
            {"kind": "file", "library_id": 34},
        ],
    )
    seen_packages: list[dict[str, Any]] = []

    def preview(content_package: dict[str, Any]) -> dict[str, Any]:
        seen_packages.append(content_package)
        return {
            "preview": {
                "materials": [
                    {"type": "image", "name": "手动群发海报"},
                    {"type": "attachment", "name": "手动群发手册.pdf"},
                ]
            }
        }

    record = GetAudienceSendRecordQuery(
        FakeSendRecordRepository([row]),
        material_previewer=preview,
    )(7, "manual:404")["record"]

    assert seen_packages == [
        {
            "image_library_ids": [12],
            "miniprogram_library_ids": [],
            "attachment_library_ids": [34],
            "group_invite_library_ids": [],
        }
    ]
    assert [item["name"] for item in record["attachments"]] == ["手动群发海报", "手动群发手册.pdf"]
    assert "12" not in json.dumps(record, ensure_ascii=False)
    assert "34" not in json.dumps(record, ensure_ascii=False)


def test_send_record_detail_enforces_package_scope_and_record_id_contract() -> None:
    repo = FakeSendRecordRepository([_row()])

    assert GetAudienceSendRecordQuery(repo)(8, "automation:101") == {"ok": False, "error": "package_not_found"}
    assert GetAudienceSendRecordQuery(repo)(7, "automation:not-a-number") == {
        "ok": False,
        "error": "send_record_not_found",
    }
    assert GetAudienceSendRecordQuery(repo)(7, "manual:101") == {"ok": False, "error": "send_record_not_found"}


def test_admin_send_record_routes_require_admin_session(next_client, monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ADMIN_AUTH_ENFORCED", "true")

    list_response = next_client.get("/api/admin/ai-audience/packages/7/send-records")
    detail_response = next_client.get("/api/admin/ai-audience/packages/7/send-records/automation:101")

    assert list_response.status_code == 401
    assert list_response.json()["error"] == "admin_auth_required"
    assert detail_response.status_code == 401
    assert detail_response.json()["error"] == "admin_auth_required"


def _insert_package(session, package_key: str, name: str) -> int:
    return int(
        session.execute(
            text(
                """
                INSERT INTO ai_audience_package (package_key, name, status)
                VALUES (:package_key, :name, 'active')
                RETURNING id
                """
            ),
            {"package_key": package_key, "name": name},
        ).scalar_one()
    )


def _insert_effect(
    session,
    *,
    business_type: str,
    business_id: str,
    target_id: str,
    content_text: str,
    external_userid: str,
    status: str = "succeeded",
    created_at: str = "2026-08-01 10:00:00+08",
    attachments: str = "[]",
    media_refs: str = "[]",
) -> int:
    succeeded = status == "succeeded"
    provider_called = status not in {"planned", "queued", "cancelled", "simulated"}
    return int(
        session.execute(
            text(
                """
                INSERT INTO external_effect_job (
                    effect_type, adapter_name, operation, target_type, target_id,
                    business_type, business_id, source_module, idempotency_key,
                    execution_mode, payload_json, status, attempt_count,
                    side_effect_executed, provider_result_received,
                    provider_call_started_at, completed_at, created_at, updated_at
                )
                VALUES (
                    'wecom.message.private.send', 'wecom_private_message', 'send_private_message',
                    'user_ops_customer', CAST(:target_id AS text), CAST(:business_type AS text), CAST(:business_id AS text),
                    'test_send_records', CAST(:idempotency_key AS text), 'execute',
                    jsonb_build_object(
                        'target_unionid', CAST(:target_id AS text),
                        'target_display_name', '冻结昵称',
                        'external_userids', jsonb_build_array(CAST(:external_userid AS text)),
                        'content_text', CAST(:content_text AS text),
                        'attachments', CAST(:attachments AS jsonb),
                        'media_refs', CAST(:media_refs AS jsonb)
                    ),
                    CAST(:status AS text), 2, :succeeded, :succeeded,
                    CASE WHEN :provider_called THEN CAST(:created_at AS timestamptz) ELSE NULL END,
                    CASE WHEN :succeeded THEN CAST(:created_at AS timestamptz) + interval '1 minute' ELSE NULL END,
                    CAST(:created_at AS timestamptz), CAST(:created_at AS timestamptz)
                )
                RETURNING id
                """
            ),
            {
                "target_id": target_id,
                "business_type": business_type,
                "business_id": business_id,
                "idempotency_key": f"send-record-test:{business_type}:{business_id}:{target_id}",
                "external_userid": external_userid,
                "content_text": content_text,
                "attachments": attachments,
                "media_refs": media_refs,
                "status": status,
                "succeeded": succeeded,
                "provider_called": provider_called,
                "created_at": created_at,
            },
        ).scalar_one()
    )


def test_admin_send_records_api_reads_two_fact_sources_and_blocks_cross_package_idor(
    next_client,
    next_pg_schema,
    monkeypatch,
) -> None:
    del next_pg_schema
    monkeypatch.setenv("SECRET_KEY", "ai-audience-send-records-test")
    with get_session_factory()() as session:
        package_id = _insert_package(session, "send_records_pkg", "发送记录包")
        other_package_id = _insert_package(session, "send_records_other", "其他包")
        session.execute(
            text(
                """
                INSERT INTO crm_user_identity (
                    unionid, primary_external_userid, external_userids_json, customer_name,
                    identity_status, created_at, updated_at
                )
                VALUES (
                    'union_auto_record', 'wm_auto_record', '["wm_auto_record"]'::jsonb,
                    '当前昵称', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (unionid) DO NOTHING
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO automation_agent_webhook_batch (
                    batch_id, agent_code, bound_package_key, status, agent_config_snapshot_json
                )
                VALUES (
                    'batch_send_record', 'fixed_record_agent', 'send_records_pkg', 'succeeded',
                    '{"automation_type":"fixed_script"}'::jsonb
                )
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO automation_agent_webhook_item (
                    batch_id, agent_code, unionid, external_event_id, status,
                    context_snapshot_json, content_package_json
                )
                VALUES (
                    'batch_send_record', 'fixed_record_agent', 'union_auto_record',
                    'event_send_record', 'send_plan_created',
                    '{"customer":{"customer_name":"发送时昵称"}}'::jsonb,
                    '{"content_text":"完整固定话术","image_library_ids":[12]}'::jsonb
                )
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO cloud_broadcast_plans (
                    plan_id, selection_json, content_strategy, status, created_at, updated_at
                )
                VALUES (
                    'plan_send_record',
                    '{"source":"automation_agent","package_key":"send_records_pkg","external_event_id":"event_send_record"}'::jsonb,
                    'agent_generated_single', 'committed',
                    TIMESTAMPTZ '2026-08-01 09:00:00+08', TIMESTAMPTZ '2026-08-01 09:00:00+08'
                )
                """
            )
        )
        broadcast_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO broadcast_jobs (
                        source_type, source_id, source_table, status, target_unionids_json,
                        target_count, content_type, content_payload, side_effect_executed,
                        provider_result_received, attempt_count, created_at, updated_at, sent_at
                    )
                    VALUES (
                        'cloud_plan', 'plan_send_record', 'cloud_broadcast_plans', 'sent',
                        '["union_auto_record"]'::jsonb, 1, 'text', '{}'::jsonb,
                        TRUE, TRUE, 2,
                        TIMESTAMPTZ '2026-08-01 09:01:00+08',
                        TIMESTAMPTZ '2026-08-01 09:02:00+08',
                        TIMESTAMPTZ '2026-08-01 09:02:00+08'
                    )
                    RETURNING id
                    """
                )
            ).scalar_one()
        )
        automation_effect_id = _insert_effect(
            session,
            business_type="broadcast_job",
            business_id=str(broadcast_id),
            target_id="union_auto_record",
            external_userid="wm_auto_record",
            content_text="完整固定话术",
            created_at="2026-08-01 09:01:30+08",
            attachments='[{"msgtype":"image","image":{"media_id":"must-not-leak"}}]',
        )
        session.execute(
            text("UPDATE broadcast_jobs SET external_effect_job_id = :effect_id WHERE id = :broadcast_id"),
            {"effect_id": automation_effect_id, "broadcast_id": broadcast_id},
        )
        recipient_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO cloud_broadcast_plan_recipients (
                        plan_id, unionid, display_name, approval_status, send_status, broadcast_job_id, created_at, updated_at
                    )
                    VALUES (
                        'plan_send_record', 'union_auto_record', '计划昵称', 'approved', 'sent', :broadcast_id,
                        TIMESTAMPTZ '2026-08-01 09:00:30+08', TIMESTAMPTZ '2026-08-01 09:02:00+08'
                    )
                    RETURNING id
                    """
                ),
                {"broadcast_id": broadcast_id},
            ).scalar_one()
        )
        session.execute(
            text(
                """
                INSERT INTO cloud_broadcast_plan_recipient_messages (
                    plan_id, recipient_id, unionid, content_text, content_payload_json,
                    attachments_json, status, sent_at, created_at, updated_at
                )
                VALUES (
                    'plan_send_record', :recipient_id, 'union_auto_record', '完整固定话术',
                    '{"content_text":"完整固定话术","image_library_ids":[12]}'::jsonb,
                    '[]'::jsonb, 'sent', TIMESTAMPTZ '2026-08-01 09:02:00+08',
                    TIMESTAMPTZ '2026-08-01 09:00:40+08', TIMESTAMPTZ '2026-08-01 09:02:00+08'
                )
                """
            ),
            {"recipient_id": recipient_id},
        )

        session.execute(
            text(
                """
                INSERT INTO user_ops_send_records_next (
                    record_key, execution_backend, target_source, target_source_id,
                    content_preview, status, created_at
                )
                VALUES (
                    'manual_send_record', 'external_effect_queue', 'ai_audience_package', :package_id,
                    '手动群发完整话术', 'queued', TIMESTAMPTZ '2026-08-01 10:00:00+08'
                )
                """
            ),
            {"package_id": package_id},
        )
        manual_effect_id = _insert_effect(
            session,
            business_type="user_ops_batch_send",
            business_id="manual_send_record",
            target_id="union_manual_record",
            external_userid="wm_manual_record",
            content_text="手动群发完整话术",
            status="failed_terminal",
            created_at="2026-08-01 10:00:10+08",
            media_refs='[{"kind":"group_invite","title":"加入学习群"}]',
        )
        session.execute(
            text(
                """
                UPDATE external_effect_job
                SET provider_call_started_at = NULL,
                    last_error_code = '',
                    last_error_message = ''
                WHERE id = :effect_id
                """
            ),
            {"effect_id": manual_effect_id},
        )
        session.execute(
            text(
                """
                INSERT INTO external_effect_attempt (
                    attempt_id, job_id, adapter_name, adapter_mode, operation,
                    status, error_code, error_message, started_at, completed_at
                )
                VALUES (
                    'attempt_manual_send_record', :effect_id, 'wecom_private_message', 'real',
                    'send_private_message', 'failed_terminal', 'wecom_api_error', '企微调用失败',
                    TIMESTAMPTZ '2026-08-01 10:00:20+08', TIMESTAMPTZ '2026-08-01 10:00:21+08'
                )
                """
            ),
            {"effect_id": manual_effect_id},
        )
        session.commit()

    cookies = admin_session_cookies(next_client, "super_admin")
    response = next_client.get(
        f"/api/admin/ai-audience/packages/{package_id}/send-records?limit=20&offset=0",
        cookies=cookies,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert [item["record_id"] for item in payload["items"]] == [f"manual:{manual_effect_id}", f"automation:{broadcast_id}"]
    assert [item["source_label"] for item in payload["items"]] == ["手动群发", "固定话术"]
    assert payload["items"][0]["status"] == "failed"
    assert payload["items"][0]["send_time"] == "2026-08-01T02:00:20Z"
    assert payload["items"][0]["failure_reason"] == "企微调用失败"
    assert payload["items"][1]["status"] == "sent"
    assert payload["items"][1]["nickname"] == "发送时昵称"

    detail = next_client.get(
        f"/api/admin/ai-audience/packages/{package_id}/send-records/automation:{broadcast_id}",
        cookies=cookies,
    )
    assert detail.status_code == 200
    assert detail.json()["record"]["content_text"] == "完整固定话术"
    assert detail.json()["record"]["attachments"][0]["type"] == "image"
    assert "must-not-leak" not in detail.text

    idor = next_client.get(
        f"/api/admin/ai-audience/packages/{other_package_id}/send-records/automation:{broadcast_id}",
        cookies=cookies,
    )
    assert idor.status_code == 404
    assert idor.json()["error"] == "send_record_not_found"

    missing_package = next_client.get(
        "/api/admin/ai-audience/packages/999999/send-records",
        cookies=cookies,
    )
    assert missing_package.status_code == 404
    assert missing_package.json()["error"] == "package_not_found"
