from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from aicrm_next.platform.shared.db_session import get_session_factory

from .context import PrincipalType
from .models import ApiClientRecord, AuthSessionRecord, WebhookClientRecord


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class PostgresAuthRepository:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] | None = None,
        database_url: str | None = None,
    ) -> None:
        self._session_factory = session_factory or get_session_factory(database_url)

    def count_admin_users(self) -> int:
        try:
            with self._session_factory() as session:
                value = session.execute(text("SELECT COUNT(*) FROM admin_users")).scalar()
        except SQLAlchemyError:
            return 0
        return int(value or 0)

    def api_client(self, client_id: str) -> ApiClientRecord | None:
        with self._session_factory() as session:
            row = (
                session.execute(
                    text("SELECT * FROM auth_api_clients WHERE client_id = :client_id LIMIT 1"),
                    {"client_id": str(client_id or "").strip()},
                )
                .mappings()
                .first()
            )
        return _api_client(dict(row)) if row else None

    def list_api_clients(self) -> list[ApiClientRecord]:
        with self._session_factory() as session:
            rows = session.execute(text("SELECT * FROM auth_api_clients ORDER BY client_id")).mappings().all()
        return [_api_client(dict(row)) for row in rows]

    def insert_api_client(self, client: ApiClientRecord) -> None:
        with self._session_factory.begin() as session:
            session.execute(
                text(
                    """
                    INSERT INTO auth_api_clients (
                        client_id, principal_id, principal_type, purpose, display_name,
                        secret_hash, audiences_json, scopes_json,
                        capabilities_json, allowed_cidrs_json, corp_id,
                        owner_scope_json, auth_version, token_ttl_seconds, enabled
                    ) VALUES (
                        :client_id, :principal_id, :principal_type, :purpose, :display_name,
                        :secret_hash, CAST(:audiences AS JSONB), CAST(:scopes AS JSONB),
                        CAST(:capabilities AS JSONB), CAST(:allowed_cidrs AS JSONB), :corp_id,
                        CAST(:owner_scope AS JSONB), :auth_version, :token_ttl_seconds, :enabled
                    )
                    """
                ),
                _api_client_params(client),
            )

    def update_api_client_definition(self, client: ApiClientRecord) -> bool:
        with self._session_factory.begin() as session:
            result = session.execute(
                text(
                    """
                    UPDATE auth_api_clients
                    SET principal_id = :principal_id,
                        principal_type = :principal_type,
                        purpose = :purpose,
                        display_name = :display_name,
                        audiences_json = CAST(:audiences AS JSONB),
                        scopes_json = CAST(:scopes AS JSONB),
                        capabilities_json = CAST(:capabilities AS JSONB),
                        allowed_cidrs_json = CAST(:allowed_cidrs AS JSONB),
                        corp_id = :corp_id,
                        owner_scope_json = CAST(:owner_scope AS JSONB),
                        token_ttl_seconds = :token_ttl_seconds,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE client_id = :client_id
                    """
                ),
                _api_client_params(client),
            )
            return bool(result.rowcount)

    def rotate_api_client_secret(self, client_id: str, secret_hash: str) -> int | None:
        with self._session_factory.begin() as session:
            row = (
                session.execute(
                    text(
                        """
                        UPDATE auth_api_clients
                        SET secret_hash = :secret_hash,
                            auth_version = auth_version + 1,
                            last_rotated_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE client_id = :client_id
                        RETURNING auth_version
                        """
                    ),
                    {"client_id": str(client_id or "").strip(), "secret_hash": secret_hash},
                )
                .mappings()
                .first()
            )
        return int(row["auth_version"]) if row else None

    def set_api_client_enabled(self, client_id: str, enabled: bool) -> int | None:
        with self._session_factory.begin() as session:
            row = (
                session.execute(
                    text(
                        """
                        UPDATE auth_api_clients
                        SET enabled = :enabled,
                            auth_version = auth_version + 1,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE client_id = :client_id
                        RETURNING auth_version
                        """
                    ),
                    {"client_id": str(client_id or "").strip(), "enabled": bool(enabled)},
                )
                .mappings()
                .first()
            )
        return int(row["auth_version"]) if row else None

    def webhook_client(self, client_id: str) -> WebhookClientRecord | None:
        with self._session_factory() as session:
            row = (
                session.execute(
                    text("SELECT * FROM auth_webhook_clients WHERE client_id = :client_id LIMIT 1"),
                    {"client_id": str(client_id or "").strip()},
                )
                .mappings()
                .first()
            )
        return _webhook_client(dict(row)) if row else None

    def upsert_webhook_client(self, client: WebhookClientRecord) -> None:
        with self._session_factory.begin() as session:
            session.execute(
                text(
                    """
                    INSERT INTO auth_webhook_clients (
                        client_id, principal_id, display_name, secret_reference,
                        capabilities_json, allowed_cidrs_json, corp_id,
                        owner_scope_json, auth_version, enabled
                    ) VALUES (
                        :client_id, :principal_id, :display_name, :secret_reference,
                        CAST(:capabilities AS JSONB), CAST(:allowed_cidrs AS JSONB), :corp_id,
                        CAST(:owner_scope AS JSONB), :auth_version, :enabled
                    )
                    ON CONFLICT (client_id) DO UPDATE SET
                        principal_id = EXCLUDED.principal_id,
                        display_name = EXCLUDED.display_name,
                        secret_reference = EXCLUDED.secret_reference,
                        capabilities_json = EXCLUDED.capabilities_json,
                        allowed_cidrs_json = EXCLUDED.allowed_cidrs_json,
                        corp_id = EXCLUDED.corp_id,
                        owner_scope_json = EXCLUDED.owner_scope_json,
                        auth_version = auth_webhook_clients.auth_version + 1,
                        enabled = EXCLUDED.enabled,
                        updated_at = CURRENT_TIMESTAMP
                    """
                ),
                {
                    "client_id": client.client_id,
                    "principal_id": client.principal_id,
                    "display_name": client.display_name,
                    "secret_reference": client.secret_reference,
                    "capabilities": _json_text(list(client.capabilities)),
                    "allowed_cidrs": _json_text(list(client.allowed_cidrs)),
                    "corp_id": client.corp_id,
                    "owner_scope": _json_text(client.owner_scope),
                    "auth_version": client.auth_version,
                    "enabled": client.enabled,
                },
            )

    def consume_webhook_event(
        self,
        *,
        client_id: str,
        event_id_hash: str,
        expires_at: datetime,
    ) -> bool:
        with self._session_factory.begin() as session:
            result = session.execute(
                text(
                    """
                    INSERT INTO auth_webhook_replay (client_id, event_id_hash, expires_at)
                    VALUES (:client_id, :event_id_hash, :expires_at)
                    ON CONFLICT (client_id, event_id_hash) DO NOTHING
                    """
                ),
                {
                    "client_id": str(client_id or "").strip(),
                    "event_id_hash": str(event_id_hash or "").strip(),
                    "expires_at": expires_at,
                },
            )
            return bool(result.rowcount)

    def insert_auth_session(self, record: AuthSessionRecord) -> None:
        with self._session_factory.begin() as session:
            session.execute(
                text(
                    """
                    INSERT INTO auth_sessions (
                        session_id, session_secret_hash, csrf_token_hash,
                        principal_id, admin_user_id, corp_id, session_version,
                        scopes_json, capabilities_json, owner_scope_json,
                        auth_time, expires_at
                    ) VALUES (
                        :session_id, :session_secret_hash, :csrf_token_hash,
                        :principal_id, :admin_user_id, :corp_id, :session_version,
                        CAST(:scopes AS JSONB), CAST(:capabilities AS JSONB),
                        CAST(:owner_scope AS JSONB), :auth_time, :expires_at
                    )
                    """
                ),
                {
                    "session_id": record.session_id,
                    "session_secret_hash": record.session_secret_hash,
                    "csrf_token_hash": record.csrf_token_hash,
                    "principal_id": record.principal_id,
                    "admin_user_id": int(record.admin_user_id),
                    "corp_id": record.corp_id,
                    "session_version": record.session_version,
                    "scopes": _json_text(list(record.scopes)),
                    "capabilities": _json_text(list(record.capabilities)),
                    "owner_scope": _json_text(record.owner_scope),
                    "auth_time": record.auth_time,
                    "expires_at": record.expires_at,
                },
            )

    def auth_session_by_hash(self, session_hash: str) -> AuthSessionRecord | None:
        with self._session_factory.begin() as session:
            row = (
                session.execute(
                    text(
                        """
                        SELECT s.*
                        FROM auth_sessions s
                        JOIN admin_users a ON a.id = s.admin_user_id
                        WHERE s.session_secret_hash = :session_hash
                          AND a.is_active = TRUE
                          AND a.login_enabled = TRUE
                          AND a.session_version = s.session_version
                        FOR UPDATE OF s
                        """
                    ),
                    {"session_hash": str(session_hash or "").strip()},
                )
                .mappings()
                .first()
            )
            if row:
                session.execute(
                    text("UPDATE auth_sessions SET last_seen_at = CURRENT_TIMESTAMP WHERE session_id = :session_id"),
                    {"session_id": row["session_id"]},
                )
        return _auth_session(dict(row)) if row else None

    def revoke_auth_session(self, session_hash: str, *, revoked_at: datetime, reason: str) -> bool:
        with self._session_factory.begin() as session:
            result = session.execute(
                text(
                    """
                    UPDATE auth_sessions
                    SET revoked_at = COALESCE(revoked_at, :revoked_at),
                        revoked_reason = CASE WHEN revoked_reason = '' THEN :reason ELSE revoked_reason END
                    WHERE session_secret_hash = :session_hash
                    """
                ),
                {"session_hash": session_hash, "revoked_at": revoked_at, "reason": reason},
            )
            return bool(result.rowcount)

    def record_security_event(
        self,
        *,
        event_type: str,
        client_id: str = "",
        principal_id: str = "",
        outcome: str,
        reason: str = "",
        request_id: str = "",
    ) -> None:
        with self._session_factory.begin() as session:
            session.execute(
                text(
                    """
                    INSERT INTO auth_security_events (
                        event_type, client_id, principal_id, outcome, reason, request_id
                    ) VALUES (
                        :event_type, :client_id, :principal_id, :outcome, :reason, :request_id
                    )
                    """
                ),
                {
                    "event_type": str(event_type or "")[:128],
                    "client_id": str(client_id or "")[:128],
                    "principal_id": str(principal_id or "")[:256],
                    "outcome": str(outcome or "failed")[:32],
                    "reason": str(reason or "")[:256],
                    "request_id": str(request_id or "")[:128],
                },
            )


def _api_client_params(client: ApiClientRecord) -> dict[str, Any]:
    return {
        "client_id": client.client_id,
        "principal_id": client.principal_id,
        "principal_type": client.principal_type.value,
        "purpose": client.purpose,
        "display_name": client.display_name,
        "secret_hash": client.secret_hash,
        "audiences": _json_text(list(client.audiences)),
        "scopes": _json_text(list(client.scopes)),
        "capabilities": _json_text(list(client.capabilities)),
        "allowed_cidrs": _json_text(list(client.allowed_cidrs)),
        "corp_id": client.corp_id,
        "owner_scope": _json_text(client.owner_scope),
        "auth_version": client.auth_version,
        "token_ttl_seconds": client.token_ttl_seconds,
        "enabled": client.enabled,
    }


def _api_client(row: dict[str, Any]) -> ApiClientRecord:
    return ApiClientRecord(
        client_id=str(row.get("client_id") or ""),
        principal_id=str(row.get("principal_id") or ""),
        principal_type=PrincipalType(str(row.get("principal_type") or "api_client")),
        purpose=str(row.get("purpose") or ""),
        display_name=str(row.get("display_name") or ""),
        secret_hash=str(row.get("secret_hash") or ""),
        audiences=tuple(_json(row.get("audiences_json"), [])),
        scopes=tuple(_json(row.get("scopes_json"), [])),
        capabilities=tuple(_json(row.get("capabilities_json"), [])),
        allowed_cidrs=tuple(_json(row.get("allowed_cidrs_json"), [])),
        corp_id=str(row.get("corp_id") or ""),
        owner_scope=dict(_json(row.get("owner_scope_json"), {})),
        auth_version=int(row.get("auth_version") or 1),
        token_ttl_seconds=int(row.get("token_ttl_seconds") or 1800),
        enabled=bool(row.get("enabled")),
    )


def _webhook_client(row: dict[str, Any]) -> WebhookClientRecord:
    return WebhookClientRecord(
        client_id=str(row.get("client_id") or ""),
        principal_id=str(row.get("principal_id") or ""),
        display_name=str(row.get("display_name") or ""),
        secret_reference=str(row.get("secret_reference") or ""),
        capabilities=tuple(_json(row.get("capabilities_json"), [])),
        allowed_cidrs=tuple(_json(row.get("allowed_cidrs_json"), [])),
        corp_id=str(row.get("corp_id") or ""),
        owner_scope=dict(_json(row.get("owner_scope_json"), {})),
        auth_version=int(row.get("auth_version") or 1),
        enabled=bool(row.get("enabled")),
    )


def _auth_session(row: dict[str, Any]) -> AuthSessionRecord:
    return AuthSessionRecord(
        session_id=str(row.get("session_id") or ""),
        session_secret_hash=str(row.get("session_secret_hash") or ""),
        csrf_token_hash=str(row.get("csrf_token_hash") or ""),
        principal_id=str(row.get("principal_id") or ""),
        admin_user_id=str(row.get("admin_user_id") or ""),
        corp_id=str(row.get("corp_id") or ""),
        session_version=int(row.get("session_version") or 1),
        scopes=tuple(_json(row.get("scopes_json"), [])),
        capabilities=tuple(_json(row.get("capabilities_json"), [])),
        owner_scope=dict(_json(row.get("owner_scope_json"), {})),
        auth_time=row["auth_time"],
        expires_at=row["expires_at"],
        revoked_at=row.get("revoked_at"),
        revoked_reason=str(row.get("revoked_reason") or ""),
    )
