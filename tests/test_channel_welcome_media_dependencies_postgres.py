from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from aicrm_next.channel_entry.welcome_media_effects_repository import (
    SQLAlchemyWelcomeEffectGraphRepository,
    WelcomeEffectGraphRequest,
)
from aicrm_next.platform_foundation.external_effects import WECOM_WELCOME_MESSAGE_SEND
from aicrm_next.platform_foundation.external_effects.adapters import (
    ExternalEffectAdapterRegistry,
    WeComWelcomeMessageAdapter,
)
from aicrm_next.platform_foundation.external_effects.repo import SQLAlchemyExternalEffectRepository
from aicrm_next.platform_foundation.external_effects.service import ExternalEffectService
from aicrm_next.platform_foundation.external_effects.worker import ExternalEffectWorker
from aicrm_next.shared.db_session import get_session_factory


pytestmark = pytest.mark.usefixtures("next_pg_schema")


@pytest.fixture(autouse=True)
def _wecom_execution_contract(monkeypatch):
    monkeypatch.setenv("AICRM_WECOM_EXECUTION_MODE", "execute")
    monkeypatch.setenv(
        "AICRM_WECOM_ENABLED_EFFECT_TYPES",
        "wecom.media.upload,wecom.welcome_message.send",
    )
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_TEST_EXECUTION_ONLY", "false")
    monkeypatch.setenv("AICRM_WECOM_PROVIDER_TARGET_POLICY", "allowlisted_canary")
    monkeypatch.setenv(
        "AICRM_EXTERNAL_EFFECT_ALLOWED_TARGET_EXTERNAL_USERIDS",
        "wm-postgres-welcome",
    )
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_OWNER_USERIDS", "owner-postgres")
    monkeypatch.setattr(
        "aicrm_next.platform_foundation.external_effects.worker._capability_gate_error",
        lambda job: "",
    )


def _connect():
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, row_factory=dict_row)


def _seed_materials() -> dict[str, int]:
    with _connect() as connection:
        image_id = connection.execute(
            """
            INSERT INTO image_library (name, file_name, data_base64, mime_type, enabled)
            VALUES ('welcome image', 'welcome.png', 'cG5n', 'image/png', TRUE)
            RETURNING id
            """
        ).fetchone()["id"]
        attachment_id = connection.execute(
            """
            INSERT INTO attachment_library (name, file_name, data_base64, mime_type, enabled)
            VALUES ('welcome file', 'welcome.pdf', 'cGRm', 'application/pdf', TRUE)
            RETURNING id
            """
        ).fetchone()["id"]
        miniprogram_id = connection.execute(
            """
            INSERT INTO miniprogram_library (name, appid, pagepath, title, enabled)
            VALUES ('welcome mini', 'wx-welcome', 'pages/welcome', 'Welcome mini', TRUE)
            RETURNING id
            """
        ).fetchone()["id"]
        link_id = connection.execute(
            """
            INSERT INTO group_invite_library (name, title, description, join_url, enabled)
            VALUES (
                'welcome group', 'Join us', 'Welcome',
                'https://work.weixin.qq.com/gm/0123456789abcdef0123456789abcdef', TRUE
            ) RETURNING id
            """
        ).fetchone()["id"]
    return {
        "image": int(image_id),
        "attachment": int(attachment_id),
        "miniprogram": int(miniprogram_id),
        "link": int(link_id),
    }


def _request(key: str, materials: dict[str, int]) -> WelcomeEffectGraphRequest:
    return WelcomeEffectGraphRequest(
        idempotency_key=key,
        channel_id=7001,
        corp_id="ww-postgres",
        external_userid="wm-postgres-welcome",
        follow_user_userid="owner-postgres",
        welcome_code="welcome-postgres-code",
        target_type="external_user",
        target_id="wm-postgres-welcome",
        target_payload={},
        text_content="Welcome from PostgreSQL",
        attachments=(
            {"msgtype": "image", "material_id": materials["image"]},
            {"msgtype": "file", "material_id": materials["attachment"]},
            {"msgtype": "miniprogram", "material_id": materials["miniprogram"]},
            {"msgtype": "link", "material_id": materials["link"]},
        ),
        actor_id="pytest",
        source_event_id="callback-event-postgres",
        scene_value="scene-postgres",
    )


def _complete_upload(job_id: int, *, media_id: str) -> str:
    attempt_id = f"eea_{uuid4().hex}"
    with _connect() as connection:
        payload = connection.execute(
            "SELECT payload_json FROM external_effect_job WHERE id = %s",
            (job_id,),
        ).fetchone()["payload_json"]
        table_and_column = {
            "image": ("image_library", "thumb_media_id"),
            "attachment": ("attachment_library", "media_id"),
            "miniprogram": ("miniprogram_library", "thumb_media_id"),
        }
        table, column = table_and_column[payload["material_kind"]]
        connection.execute(
            f"UPDATE {table} SET {column} = %s WHERE id = %s",
            (media_id, int(payload["material_id"])),
        )
        connection.execute(
            """
            INSERT INTO external_effect_attempt (
                attempt_id, job_id, adapter_name, adapter_mode, operation,
                status, request_summary_json, response_summary_json,
                provider_call_started_at,
                started_at, completed_at
            )
            SELECT %s, id, adapter_name, 'execute', operation,
                   'succeeded', '{}'::jsonb,
                   jsonb_build_object('provider_result_received', TRUE),
                   CURRENT_TIMESTAMP,
                   CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            FROM external_effect_job WHERE id = %s
            """,
            (attempt_id, job_id),
        )
        connection.execute(
            """
            UPDATE external_effect_job
            SET status = 'succeeded', last_attempt_id = %s,
                provider_call_started_at = CURRENT_TIMESTAMP,
                completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (attempt_id, job_id),
        )
    return attempt_id


def test_callback_graph_plans_all_media_atomically_and_is_idempotent():
    materials = _seed_materials()
    repository = SQLAlchemyWelcomeEffectGraphRepository(get_session_factory())
    request = _request("channel_entry:welcome-postgres-atomic", materials)

    planned = repository.plan(request)
    duplicate = SQLAlchemyWelcomeEffectGraphRepository(get_session_factory()).plan(request)

    assert planned["status"] == "waiting_dependencies"
    assert len(planned["upload_effect_job_ids"]) == 3
    assert duplicate["duplicate"] is True
    assert duplicate["external_effect_job_ids"] == planned["external_effect_job_ids"]
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT effect_type, status, lane, parent_execution_id, payload_json, payload_summary_json
            FROM external_effect_job
            WHERE business_type = 'channel_welcome_effect_graph'
              AND business_id = %s
            ORDER BY id
            """,
            (planned["execution_id"],),
        ).fetchall()
        dependencies = connection.execute(
            "SELECT COUNT(*) AS count FROM channel_welcome_effect_dependency WHERE graph_id = (SELECT id FROM channel_welcome_effect_graph WHERE execution_id = %s)",
            (planned["execution_id"],),
        ).fetchone()["count"]
    assert len(rows) == 4
    assert dependencies == 3
    assert all(row["parent_execution_id"] == planned["execution_id"] for row in rows)
    assert sorted(row["lane"] for row in rows) == ["wecom_interactive", "wecom_media", "wecom_media", "wecom_media"]
    final = next(row for row in rows if row["effect_type"] == WECOM_WELCOME_MESSAGE_SEND)
    assert final["status"] == "planned"
    assert final["payload_json"]["attachments"][-1]["link"]["url"].startswith("https://work.weixin.qq.com/gm/")
    assert final["payload_json"]["attachments"][-1]["link"].get("picurl", "") == ""
    assert "wm-postgres-welcome" not in str(final["payload_summary_json"])
    assert "owner-postgres" not in str(final["payload_summary_json"])
    assert final["payload_summary_json"]["external_userid_present"] is True
    assert final["payload_summary_json"]["external_userid_hash"]


def test_upload_completion_releases_once_after_restart_and_one_welcome_attempt_calls_provider_once():
    materials = _seed_materials()
    planned = SQLAlchemyWelcomeEffectGraphRepository(get_session_factory()).plan(
        _request("channel_entry:welcome-postgres-release", materials)
    )
    upload_ids = planned["upload_effect_job_ids"]
    completion_results = []
    for index, upload_id in enumerate(upload_ids, start=1):
        attempt_id = _complete_upload(upload_id, media_id=f"provider-media-{index}")
        # A fresh repository instance models a worker restart between durable
        # completion delivery and dependency release.
        completion_results.append(
            SQLAlchemyWelcomeEffectGraphRepository(get_session_factory()).release_after_upload(
                upload_id,
                attempt_id=attempt_id,
            )
        )
    duplicate = SQLAlchemyWelcomeEffectGraphRepository(get_session_factory()).release_after_upload(
        upload_ids[-1]
    )

    assert [result["released"] for result in completion_results] == [False, False, True]
    assert duplicate["released"] is False
    assert duplicate["reason"] == "final_effect_already_released"
    with _connect() as connection:
        final = connection.execute(
            "SELECT status, payload_json FROM external_effect_job WHERE id = %s",
            (planned["final_effect_job_id"],),
        ).fetchone()
    assert final["status"] == "queued"
    assert final["payload_json"]["attachments"][:3] == [
        {"msgtype": "image", "image": {"media_id": "provider-media-1"}},
        {"msgtype": "file", "file": {"media_id": "provider-media-2"}},
        {
            "msgtype": "miniprogram",
            "miniprogram": {
                "appid": "wx-welcome",
                "page": "pages/welcome",
                "title": "Welcome mini",
                "pic_media_id": "provider-media-3",
            },
        },
    ]
    assert "dependency_key" not in str(final["payload_json"])
    assert "material_id" not in str(final["payload_json"])

    class Provider:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def send_welcome_msg(self, payload: dict) -> dict:
            self.calls.append(payload)
            return {"errcode": 0, "errmsg": "ok"}

    provider = Provider()
    forbidden_uploader_calls: list[list[dict]] = []
    registry = ExternalEffectAdapterRegistry(
        {
            "wecom_welcome_message": WeComWelcomeMessageAdapter(
                adapter_factory=lambda: provider,
                material_resolver=lambda attachments: forbidden_uploader_calls.append(attachments),
            )
        }
    )
    effect_repository = SQLAlchemyExternalEffectRepository(get_session_factory())
    effect_service = ExternalEffectService(effect_repository)
    final_job = effect_service.get(planned["final_effect_job_id"])
    assert final_job is not None
    assert effect_service.authorize_allowlisted_canary(
        final_job.id,
        actor="pytest",
        reason="explicit welcome dependency canary authorization",
        expected_version=final_job.row_version,
    )
    dispatched = ExternalEffectWorker(effect_repository, registry, locked_by="welcome-postgres-worker").dispatch_one(
        planned["final_effect_job_id"]
    )

    assert dispatched["job"]["status"] == "succeeded"
    assert dispatched["attempt"]["status"] == "succeeded"
    assert len(provider.calls) == 1
    assert forbidden_uploader_calls == []
    with _connect() as connection:
        attempt_count = connection.execute(
            "SELECT COUNT(*) AS count FROM external_effect_attempt WHERE job_id = %s",
            (planned["final_effect_job_id"],),
        ).fetchone()["count"]
    assert attempt_count == 1


def test_failed_or_cancelled_upload_never_releases_final_welcome():
    materials = _seed_materials()
    repository = SQLAlchemyWelcomeEffectGraphRepository(get_session_factory())
    planned = repository.plan(_request("channel_entry:welcome-postgres-cancel", materials))
    upload_id = planned["upload_effect_job_ids"][0]
    with _connect() as connection:
        connection.execute(
            "UPDATE external_effect_job SET status = 'failed_terminal', completed_at = CURRENT_TIMESTAMP WHERE id = %s",
            (upload_id,),
        )
    failed = repository.release_after_upload(upload_id)
    cancelled = repository.cancel(planned["execution_id"], actor="pytest", reason="operator_cancel")
    late_attempt = _complete_upload(upload_id, media_id="late-provider-media")
    late = SQLAlchemyWelcomeEffectGraphRepository(get_session_factory()).release_after_upload(
        upload_id,
        attempt_id=late_attempt,
    )

    assert failed["released"] is False
    assert failed["reason"] == "upload_not_succeeded"
    assert cancelled["cancelled"] is True
    assert late["released"] is False
    assert late["reason"] == "graph_cancelled"
    with _connect() as connection:
        final_status = connection.execute(
            "SELECT status FROM external_effect_job WHERE id = %s",
            (planned["final_effect_job_id"],),
        ).fetchone()["status"]
    assert final_status == "cancelled"


def test_terminal_upload_settlement_closes_welcome_graph_and_settles_final_job():
    materials = _seed_materials()
    repository = SQLAlchemyWelcomeEffectGraphRepository(get_session_factory())
    planned = repository.plan(_request("channel_entry:welcome-terminal-settlement", materials))
    upload_id = planned["upload_effect_job_ids"][0]
    with _connect() as connection:
        connection.execute(
            "UPDATE external_effect_job SET status = 'blocked', completed_at = CURRENT_TIMESTAMP WHERE id = %s",
            (upload_id,),
        )

    result = repository.settle_effect(upload_id, status="blocked")

    assert result["settled"] is True
    assert planned["final_effect_job_id"] in result["cancelled_job_ids"]
    with _connect() as connection:
        final = connection.execute(
            "SELECT status, row_version FROM external_effect_job WHERE id = %s",
            (planned["final_effect_job_id"],),
        ).fetchone()
        graph = connection.execute(
            "SELECT status FROM channel_welcome_effect_graph WHERE execution_id = %s",
            (planned["execution_id"],),
        ).fetchone()
        event = connection.execute(
            """
            SELECT event_type, payload_json
            FROM internal_event_outbox
            WHERE idempotency_key = %s
            """,
            (
                f"external_effect.settled:{planned['final_effect_job_id']}:cancelled:"
                f"row-version-{final['row_version']}",
            ),
        ).fetchone()
    assert final["status"] == "cancelled"
    assert graph["status"] == "terminal"
    assert event["event_type"] == "external_effect.settled"
    assert event["payload_json"]["attempt_id"] == ""


def test_terminal_final_welcome_fences_claimed_upload_and_cancels_queued_siblings():
    materials = _seed_materials()
    repository = SQLAlchemyWelcomeEffectGraphRepository(get_session_factory())
    planned = repository.plan(_request("channel_entry:welcome-final-terminal", materials))
    effects = SQLAlchemyExternalEffectRepository(get_session_factory())
    claimed_upload_id = planned["upload_effect_job_ids"][0]
    claimed = effects.acquire_job(claimed_upload_id, locked_by="welcome-review-race")
    assert claimed is not None
    assert claimed.status == "dispatching"
    with _connect() as connection:
        connection.execute(
            """
            UPDATE external_effect_job
            SET status = 'cancelled', cancel_requested_at = CURRENT_TIMESTAMP,
                cancelled_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (planned["final_effect_job_id"],),
        )

    result = repository.settle_effect(planned["final_effect_job_id"], status="cancelled")

    queued_siblings = set(planned["upload_effect_job_ids"][1:])
    assert result["settled"] is True
    assert set(result["cancelled_job_ids"]) == queued_siblings
    assert result["cancel_requested_job_ids"] == [claimed_upload_id]
    current = effects.get_job(claimed_upload_id)
    assert current is not None
    assert current.status == "dispatching"
    assert current.cancel_requested_at
    assert current.provider_call_started_at == ""
    assert effects.begin_provider_attempt(job=claimed, request_summary={}) is None
    settled = effects.settle_cancel(job=current)
    assert settled is not None
    assert settled.status == "cancelled"
    with _connect() as connection:
        siblings = connection.execute(
            "SELECT id, status FROM external_effect_job WHERE id = ANY(%s) ORDER BY id",
            (planned["upload_effect_job_ids"],),
        ).fetchall()
    assert [row["status"] for row in siblings] == ["cancelled", "cancelled", "cancelled"]


def test_invalid_material_rolls_back_graph_and_all_jobs():
    missing = {"image": 999991, "attachment": 999992, "miniprogram": 999993, "link": 999994}
    request = _request("channel_entry:welcome-postgres-rollback", missing)
    with pytest.raises(ValueError, match="unavailable"):
        SQLAlchemyWelcomeEffectGraphRepository(get_session_factory()).plan(request)

    with _connect() as connection:
        graph_count = connection.execute(
            "SELECT COUNT(*) AS count FROM channel_welcome_effect_graph WHERE idempotency_key = %s",
            (request.idempotency_key,),
        ).fetchone()["count"]
        effect_count = connection.execute(
            "SELECT COUNT(*) AS count FROM external_effect_job WHERE idempotency_key LIKE %s",
            (f"{request.idempotency_key}:%",),
        ).fetchone()["count"]
    assert graph_count == 0
    assert effect_count == 0


def test_expired_cached_material_failure_stays_held_and_never_falls_through_to_welcome():
    materials = _seed_materials()
    with _connect() as connection:
        connection.execute(
            """
            UPDATE image_library
            SET thumb_media_id = 'expired-media',
                thumb_media_id_expires_at = CURRENT_TIMESTAMP - INTERVAL '1 hour'
            WHERE id = %s
            """,
            (materials["image"],),
        )
    request = WelcomeEffectGraphRequest(
        **{
            **_request("channel_entry:welcome-postgres-expired", materials).__dict__,
            "attachments": ({"msgtype": "image", "material_id": materials["image"]},),
        }
    )
    repository = SQLAlchemyWelcomeEffectGraphRepository(get_session_factory())
    planned = repository.plan(request)
    upload_id = planned["upload_effect_job_ids"][0]
    with _connect() as connection:
        upload = connection.execute(
            "SELECT status, payload_json FROM external_effect_job WHERE id = %s",
            (upload_id,),
        ).fetchone()
        connection.execute(
            "UPDATE external_effect_job SET status = 'failed_terminal', completed_at = CURRENT_TIMESTAMP WHERE id = %s",
            (upload_id,),
        )
    failed = repository.release_after_upload(upload_id)

    assert upload["status"] == "queued"
    assert upload["payload_json"]["force_refresh"] is False
    assert failed["released"] is False
    assert failed["reason"] == "upload_not_succeeded"
    with _connect() as connection:
        final = connection.execute(
            "SELECT status, attempt_count FROM external_effect_job WHERE id = %s",
            (planned["final_effect_job_id"],),
        ).fetchone()
    assert final == {"status": "planned", "attempt_count": 0}
