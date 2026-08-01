from __future__ import annotations

import json
import os
from pathlib import Path

from sqlalchemy import text

from aicrm_next.extensions.ai.ai_audience_ops.refresh_service import AudienceRefreshService, _refresh_sql
from aicrm_next.extensions.ai.ai_audience_ops.repository import build_audience_repository
from aicrm_next.platform.shared.db_session import get_session_factory
from scripts import ai_audience_apply_package_spec as spec_script
from tests.admin_auth_test_helpers import access_token_headers, install_access_token
from tests.test_ai_audience_package_spec import VALID_SPEC


TOKEN = "external-spec-test-token"


def _headers() -> dict[str, str]:
    return {"Content-Type": "application/json"}


def _ready_env(monkeypatch, client) -> None:
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_SPEC_ALLOWED_PREFIXES", "prod_verify_,group_chat_members_,official_,audience_")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_SPEC_ALLOW_NON_VERIFY_PREFIX", "false")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_SPEC_ALLOW_PUBLISH", "false")
    if os.getenv("DATABASE_URL"):
        monkeypatch.setenv("AICRM_AUDIENCE_READONLY_DATABASE_URL", os.environ["DATABASE_URL"])
    token = install_access_token(
        client,
        audience="external_integration",
        capabilities=("external_write",),
        scopes=("write",),
        client_id="pytest-external-audience-agent",
        purpose="external_agent",
    )
    client.headers.update(access_token_headers(token))


def _spec(package_key: str = "spec_q101") -> str:
    return VALID_SPEC.replace("package_key: spec_q101", f"package_key: {package_key}")


def test_external_spec_auth_guards(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    no_auth = next_client.post("/api/external/ai-audience/spec/dry-run", json={"spec_markdown": _spec(), "package_key_prefix": "prod_verify_"})
    assert no_auth.status_code == 401
    assert no_auth.json()["error"] == "access_token_required"

    install_access_token(
        next_client,
        audience="external_integration",
        capabilities=("external_write",),
        scopes=("write",),
        client_id="pytest-external-audience-agent",
        purpose="external_agent",
    )
    wrong = next_client.post(
        "/api/external/ai-audience/spec/dry-run",
        headers={"Authorization": "Bearer malformed"},
        json={"spec_markdown": _spec(), "package_key_prefix": "prod_verify_"},
    )
    assert wrong.status_code == 401
    assert wrong.json()["error"] == "invalid_access_token"
    assert "malformed" not in wrong.text


def test_external_spec_dry_run_validates_without_creating_package(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready_env(monkeypatch, next_client)

    response = next_client.post(
        "/api/external/ai-audience/spec/dry-run",
        headers=_headers(),
        json={"spec_markdown": _spec(), "package_key_prefix": "prod_verify_"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["mode"] == "dry_run"
    assert payload["package_key"] == "prod_verify_spec_q101"
    assert payload["validation_errors"] == []
    assert "audience_read.questionnaire_submissions_v1" in payload["dependencies"]
    with get_session_factory()() as session:
        count = session.execute(text("SELECT COUNT(*) FROM ai_audience_package WHERE package_key = 'prod_verify_spec_q101'")).scalar_one()
        audit_count = session.execute(text("SELECT COUNT(*) FROM admin_operation_logs WHERE target_type = 'ai_audience_external_spec'")).scalar_one()
    assert count == 0
    assert audit_count == 1


def test_external_spec_dry_run_blocks_invalid_sql_and_enforces_prefix_gate(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready_env(monkeypatch, next_client)
    invalid_refresh = _spec().replace("refresh_mode: incremental_3m", "refresh_mode: incremental_5m")
    select_star = _spec().replace(
        "SELECT\n  'external_userid' AS identity_type,",
        "SELECT\n  *,\n  'external_userid' AS identity_type,",
    )
    public_schema = _spec().replace("FROM audience_read.questionnaire_submissions_v1 qs", "FROM public.users qs")

    cases = [
        (invalid_refresh, "invalid_refresh_mode"),
        (select_star, "incremental:select_star_forbidden"),
        (public_schema, "incremental:dependency_not_allowed:public.users"),
    ]
    for markdown, expected in cases:
        response = next_client.post(
            "/api/external/ai-audience/spec/dry-run",
            headers=_headers(),
            json={"spec_markdown": markdown, "package_key_prefix": "prod_verify_"},
        )
        assert response.status_code == 400
        assert expected in response.json()["validation_errors"]

    official_prefix = next_client.post(
        "/api/external/ai-audience/spec/dry-run",
        headers=_headers(),
        json={"spec_markdown": _spec(), "package_key_prefix": "official_"},
    )
    assert official_prefix.status_code == 200
    assert official_prefix.json()["ok"] is True
    assert official_prefix.json()["package_key"] == "official_spec_q101"
    assert official_prefix.json()["validation_errors"] == []

    unsafe_prefix = next_client.post(
        "/api/external/ai-audience/spec/dry-run",
        headers=_headers(),
        json={"spec_markdown": _spec(), "package_key_prefix": "unsafe_"},
    )
    assert unsafe_prefix.status_code == 403
    assert unsafe_prefix.json()["error"] == "unsafe_package_key_prefix"


def test_external_spec_apply_creates_official_package_key_with_valid_token(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready_env(monkeypatch, next_client)

    response = next_client.post(
        "/api/external/ai-audience/spec/apply",
        headers=_headers(),
        json={"spec_markdown": _spec("group_chat_members_manual"), "operator": "codex", "publish": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["package_key"] == "group_chat_members_manual"
    assert payload["created"] is True
    assert payload["preview_ok"] is True
    with get_session_factory()() as session:
        package = session.execute(
            text("SELECT status, current_version_id FROM ai_audience_package WHERE package_key = 'group_chat_members_manual'")
        ).mappings().one()
    assert package["status"] == "paused"
    assert package["current_version_id"] is None


def test_external_spec_apply_previews_group_chat_snapshot_package(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready_env(monkeypatch, next_client)
    markdown = Path("docs/ai_audience/examples/group_chat_members_manual.md").read_text(encoding="utf-8")

    response = next_client.post(
        "/api/external/ai-audience/spec/apply",
        headers=_headers(),
        json={"spec_markdown": markdown, "operator": "codex", "publish": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["package_key"] == "group_chat_members_manual"
    assert payload["created"] is True
    assert payload["preview_ok"] is True
    assert "audience_read.group_chat_members_v1" in payload["dependencies"]


def test_external_spec_apply_creates_package_version_and_no_side_effects(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready_env(monkeypatch, next_client)

    response = next_client.post(
        "/api/external/ai-audience/spec/apply",
        headers=_headers(),
        json={"spec_markdown": _spec(), "package_key_prefix": "prod_verify_", "operator": "codex", "publish": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["package_key"] == "prod_verify_spec_q101"
    assert payload["created"] is True
    assert payload["updated"] is False
    assert payload["package_id"]
    assert payload["version_id"]
    assert payload["preview_ok"] is True
    assert payload["published"] is False
    assert "secret" not in json.dumps(payload, ensure_ascii=False).lower()
    with get_session_factory()() as session:
        package = session.execute(text("SELECT status, current_version_id FROM ai_audience_package WHERE id = :id"), {"id": payload["package_id"]}).mappings().one()
        version = session.execute(text("SELECT parameters_json FROM ai_audience_package_version WHERE id = :id"), {"id": payload["version_id"]}).mappings().one()
        effects = session.execute(text("SELECT COUNT(*) FROM external_effect_job")).scalar_one()
        sends = session.execute(text("SELECT COUNT(*) FROM user_ops_send_records_next")).scalar_one()
    assert package["status"] == "paused"
    assert package["current_version_id"] is None
    assert version["parameters_json"] == {"questionnaire_id": 101}
    assert effects == 0
    assert sends == 0


def test_external_spec_publish_gate_and_no_activate(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready_env(monkeypatch, next_client)
    applied = next_client.post(
        "/api/external/ai-audience/spec/apply",
        headers=_headers(),
        json={"spec_markdown": _spec("publish_spec"), "package_key_prefix": "prod_verify_", "operator": "codex", "publish": False},
    ).json()

    blocked = next_client.post(
        "/api/external/ai-audience/spec/publish",
        headers=_headers(),
        json={"package_key": "prod_verify_publish_spec", "version_id": applied["version_id"], "operator": "codex"},
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"] == "publish_not_allowed"

    monkeypatch.setenv("AICRM_AI_AUDIENCE_SPEC_ALLOW_PUBLISH", "true")
    published = next_client.post(
        "/api/external/ai-audience/spec/publish",
        headers=_headers(),
        json={"package_key": "prod_verify_publish_spec", "version_id": applied["version_id"], "operator": "codex"},
    )
    assert published.status_code == 200
    assert published.json()["published"] is True
    with get_session_factory()() as session:
        package = session.execute(text("SELECT status, current_version_id FROM ai_audience_package WHERE id = :id"), {"id": applied["package_id"]}).mappings().one()
    assert package["status"] == "paused"
    assert package["current_version_id"] == applied["version_id"]


def test_external_spec_archive_allows_official_package_key_with_valid_token(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready_env(monkeypatch, next_client)
    applied = next_client.post(
        "/api/external/ai-audience/spec/apply",
        headers=_headers(),
        json={"spec_markdown": _spec("official_archive_spec"), "operator": "codex"},
    ).json()

    archived = next_client.post(
        "/api/external/ai-audience/packages/official_archive_spec/archive",
        headers=_headers(),
        json={"operator": "codex"},
    )
    assert archived.status_code == 200
    assert archived.json()["archived"] is True
    with get_session_factory()() as session:
        status = session.execute(text("SELECT status FROM ai_audience_package WHERE id = :id"), {"id": applied["package_id"]}).scalar_one()
    assert status == "archived"


def test_external_spec_script_mode_uses_oauth_access_token(monkeypatch, tmp_path) -> None:
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(_spec(), encoding="utf-8")
    calls: list[dict] = []

    def fake_http_json(method, url, *, cookie="", bearer_token="", payload=None):
        calls.append({"method": method, "url": url, "cookie": cookie, "bearer_token": bearer_token, "payload": payload})
        return {
            "ok": True,
            "package_key": "prod_verify_spec_q101",
            "package_id": 1,
            "version_id": 2,
            "created": True,
            "updated": False,
            "preview_ok": True,
            "published": False,
            "validation_errors": [],
            "warnings": [],
        }

    monkeypatch.setattr(spec_script, "_http_json", fake_http_json)
    monkeypatch.setenv("AICRM_AUTH_CLI_ACCESS_TOKEN", TOKEN)
    rc = spec_script.main(
        [
            str(spec_path),
            "--external-api-base",
            "https://example.test",
            "--oauth-access-token-from-env",
            "--apply",
            "--package-key-prefix",
            "prod_verify_",
        ]
    )

    assert rc == 0
    assert calls
    assert calls[0]["url"] == "https://example.test/api/external/ai-audience/spec/apply"
    assert calls[0]["bearer_token"] == TOKEN
    assert calls[0]["cookie"] == ""


def _simple_payload(package_key: str = "audience_simple_hyc") -> dict:
    return {
        "package_key": package_key,
        "name": "Simple 企微未注册人群",
        "natural_language_definition": "负责人为 HuangYouCan，已经添加企业微信，但还没有完成注册的用户。",
        "refresh_mode": "manual",
        "sql": """
            SELECT DISTINCT wc.external_userid
            FROM audience_read.wecom_contacts_v1 wc
            LEFT JOIN audience_read.registration_status_v1 r
              ON r.external_userid = wc.external_userid
            WHERE wc.owner_userid = :owner_userid
              AND COALESCE(r.is_registered, false) = false
        """,
        "parameters": {"owner_userid": "HuangYouCan"},
        "senders": [{"sender_userid": "HuangYouCan", "priority": 1, "status": "active"}],
        "operator": "codex",
    }


def test_refresh_sql_uses_only_strict_daily_simple_compatibility_shape() -> None:
    version = {
        "snapshot_sql_text": "",
        "incremental_sql_text": "SELECT incremental",
        "simple_compiled_sql_text": "SELECT compiled",
    }

    assert _refresh_sql(
        {
            "query_mode": "simple_sql",
            "daily_enabled": True,
            "incremental_enabled": False,
        },
        version,
        "daily",
    ) == "SELECT compiled"
    assert _refresh_sql(
        {
            "query_mode": "snapshot_current",
            "daily_enabled": True,
            "incremental_enabled": False,
        },
        version,
        "daily",
    ) == ""
    assert _refresh_sql(
        {
            "query_mode": "simple_sql",
            "daily_enabled": True,
            "incremental_enabled": True,
        },
        version,
        "daily",
    ) == ""
    assert _refresh_sql({}, version, "incremental") == "SELECT incremental"


def _unionid_for_external_userid(external_userid: str) -> str:
    return "union_" + external_userid.removeprefix("wm_")


def _insert_identities(session, *external_userids: str) -> None:
    for external_userid in external_userids:
        session.execute(
            text(
                """
                INSERT INTO crm_user_identity (
                    unionid, primary_external_userid, external_userids_json, identity_status, created_at, updated_at
                )
                VALUES (
                    :unionid, :external_userid, jsonb_build_array(CAST(:external_userid AS text)),
                    'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (unionid) DO UPDATE SET
                    primary_external_userid = EXCLUDED.primary_external_userid,
                    external_userids_json = EXCLUDED.external_userids_json,
                    identity_status = EXCLUDED.identity_status,
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            {"unionid": _unionid_for_external_userid(external_userid), "external_userid": external_userid},
        )


def test_external_simple_preview_validates_sql_contract(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready_env(monkeypatch, next_client)

    valid = next_client.post(
        "/api/external/ai-audience/simple/preview",
        headers=_headers(),
        json={
            "package_key": "audience_preview_valid",
            "sql": "SELECT DISTINCT external_userid FROM audience_read.wecom_contacts_v1 WHERE owner_userid = :owner_userid",
            "parameters": {"owner_userid": "HuangYouCan"},
        },
    )
    assert valid.status_code == 200
    assert valid.json()["ok"] is True
    assert "compiled_sql" not in valid.json()
    assert "audience_read.wecom_contacts_v1" in valid.json()["dependencies"]

    cases = [
        (
            "SELECT * FROM audience_read.wecom_contacts_v1",
            {},
            "select_star_forbidden",
        ),
        (
            "SELECT external_userid FROM public.users",
            {},
            "dependency_not_allowed:public.users",
        ),
        (
            "SELECT owner_userid FROM audience_read.wecom_contacts_v1",
            {},
            "required_column_missing:external_userid",
        ),
        (
            "SELECT external_userid FROM audience_read.wecom_contacts_v1 WHERE owner_userid = :owner_userid",
            {},
            "parameter_not_declared:owner_userid",
        ),
    ]
    for sql, parameters, expected in cases:
        response = next_client.post(
            "/api/external/ai-audience/simple/preview",
            headers=_headers(),
            json={"package_key": "audience_preview_invalid", "sql": sql, "parameters": parameters},
        )
        assert response.status_code == 400
        assert expected in response.json()["validation_errors"]


def test_external_simple_apply_creates_paused_package_version_and_runtime_config(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready_env(monkeypatch, next_client)

    response = next_client.post("/api/external/ai-audience/simple/apply", headers=_headers(), json=_simple_payload("audience_apply_simple"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["preview_ok"] is True
    assert payload["status"] == "paused"
    assert payload["published"] is True
    with get_session_factory()() as session:
        package = session.execute(
            text("SELECT status, current_version_id, incremental_enabled, daily_enabled FROM ai_audience_package WHERE package_key = 'audience_apply_simple'")
        ).mappings().one()
        version = session.execute(
            text(
                """
                SELECT simple_sql_text, simple_compiled_sql_text, snapshot_sql_text, parameters_json
                FROM ai_audience_package_version
                WHERE id = :version_id
                """
            ),
            {"version_id": payload["version_id"]},
        ).mappings().one()
        senders = session.execute(text("SELECT sender_userid, priority, status FROM ai_audience_package_sender WHERE package_id = :package_id"), {"package_id": payload["package_id"]}).mappings().all()
    assert package["status"] == "paused"
    assert package["current_version_id"] == payload["version_id"]
    assert package["incremental_enabled"] is False
    assert package["daily_enabled"] is False
    assert "SELECT DISTINCT wc.external_userid" in version["simple_sql_text"]
    assert "WITH simple_audience AS" in version["simple_compiled_sql_text"]
    assert version["simple_compiled_sql_text"] == version["snapshot_sql_text"]
    assert version["parameters_json"] == {"owner_userid": "HuangYouCan"}
    assert [dict(item) for item in senders] == [{"sender_userid": "HuangYouCan", "priority": 1, "status": "active"}]


def test_external_simple_activate_and_archive(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready_env(monkeypatch, next_client)
    payload = _simple_payload("audience_activate_simple")
    payload["refresh_mode"] = "every_3m"
    applied = next_client.post("/api/external/ai-audience/simple/apply", headers=_headers(), json=payload).json()

    activated = next_client.post("/api/external/ai-audience/simple/audience_activate_simple/activate", headers=_headers(), json={"operator": "codex"})
    assert activated.status_code == 200
    assert activated.json()["activated"] is True
    with get_session_factory()() as session:
        package = session.execute(
            text(
                """
                SELECT status, current_version_id, incremental_enabled, incremental_interval_seconds,
                       daily_enabled, next_incremental_refresh_at, next_daily_refresh_at
                FROM ai_audience_package
                WHERE id = :package_id
                """
            ),
            {"package_id": applied["package_id"]},
        ).mappings().one()
    assert package["status"] == "active"
    assert package["current_version_id"] == applied["version_id"]
    assert package["incremental_enabled"] is True
    assert package["incremental_interval_seconds"] == 180
    assert package["daily_enabled"] is False
    assert package["next_incremental_refresh_at"] is not None
    assert package["next_daily_refresh_at"] is None

    archived = next_client.post("/api/external/ai-audience/simple/audience_activate_simple/archive", headers=_headers(), json={"operator": "codex"})
    assert archived.status_code == 200
    assert archived.json()["archived"] is True
    with get_session_factory()() as session:
        status = session.execute(text("SELECT status FROM ai_audience_package WHERE id = :package_id"), {"package_id": applied["package_id"]}).scalar_one()
    assert status == "archived"


def test_external_simple_sync_refresh_enters_idempotently_and_exits(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready_env(monkeypatch, next_client)
    with get_session_factory()() as session:
        _insert_identities(session, "wm_simple_001")
        session.execute(
            text(
                """
                INSERT INTO wecom_external_contact_identity_map (external_userid, follow_user_userid, status, updated_at)
                VALUES ('wm_simple_001', 'HuangYouCan', 'active', CURRENT_TIMESTAMP)
                """
            )
        )
        session.commit()
    applied = next_client.post("/api/external/ai-audience/simple/apply", headers=_headers(), json=_simple_payload("audience_refresh_simple")).json()
    repo = build_audience_repository()
    package = repo.get_package(int(applied["package_id"]))

    first = AudienceRefreshService(repository=repo).refresh_package(int(package["id"]), run_type="daily", package=package, row_limit=1000)
    second = AudienceRefreshService(repository=repo).refresh_package(int(package["id"]), run_type="daily", package=repo.get_package(int(package["id"])), row_limit=1000)
    with get_session_factory()() as session:
        session.execute(text("DELETE FROM wecom_external_contact_identity_map WHERE external_userid = 'wm_simple_001'"))
        session.commit()
    third = AudienceRefreshService(repository=repo).refresh_package(int(package["id"]), run_type="daily", package=repo.get_package(int(package["id"])), row_limit=1000)

    assert first["entered_count"] == 1
    assert second["entered_count"] == 0
    assert second["updated_count"] == 0
    assert third["exited_count"] == 1


def test_legacy_daily_simple_package_uses_canonical_compiled_sql(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready_env(monkeypatch, next_client)
    payload = _simple_payload("audience_legacy_daily_simple")
    payload["refresh_mode"] = "daily_0200"
    applied = next_client.post(
        "/api/external/ai-audience/simple/apply",
        headers=_headers(),
        json=payload,
    ).json()
    with get_session_factory()() as session:
        session.execute(
            text(
                """
                UPDATE ai_audience_package_version
                SET snapshot_sql_text = ''
                WHERE id = :version_id
                """
            ),
            {"version_id": applied["version_id"]},
        )
        session.commit()
    repo = build_audience_repository()
    package = repo.get_package(applied["package_id"])

    result = AudienceRefreshService(repository=repo).refresh_package(
        applied["package_id"],
        run_type="daily",
        package=package,
        row_limit=1000,
    )

    assert result["ok"] is True
    assert result["run"]["status"] == "succeeded"


def test_external_simple_rejects_retired_outbound_webhook_url(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready_env(monkeypatch, next_client)
    payload = _simple_payload("audience_outbound_simple")
    payload["outbound_webhook_url"] = "https://agent.example.test/audience"
    response = next_client.post("/api/external/ai-audience/simple/apply", headers=_headers(), json=payload)

    assert response.status_code == 410
    assert response.json()["error"] == "webhook_configuration_retired"


def test_external_simple_prefix_gate_rejects_unsafe_keys(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    _ready_env(monkeypatch, next_client)

    response = next_client.post(
        "/api/external/ai-audience/simple/preview",
        headers=_headers(),
        json={
            "package_key": "unsafe_simple",
            "sql": "SELECT external_userid FROM audience_read.wecom_contacts_v1",
            "parameters": {},
        },
    )

    assert response.status_code == 403
    assert response.json()["error"] == "unsafe_package_key_prefix"
