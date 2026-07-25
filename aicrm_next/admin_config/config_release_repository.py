from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from aicrm_next.deployment_profile import DeploymentProfile
from aicrm_next.shared.db_session import get_engine
from aicrm_next.shared.runtime_settings import invalidate_runtime_settings_request_snapshot
from aicrm_next.shared.secret_store import is_secret_reference


class ConfigReleaseRepository:
    def __init__(self, *, engine: Engine | None = None) -> None:
        self._engine = engine or get_engine()

    def create_draft(
        self,
        *,
        profile_id: str,
        changes: dict[str, str | None],
        operator: str,
        based_on_release_id: int | None = None,
        rollback_of_release_id: int | None = None,
    ) -> dict[str, Any]:
        release_key = "cfg_" + uuid4().hex
        checksum = config_release_checksum(profile_id=profile_id, changes=changes)
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    INSERT INTO config_releases (
                        release_key, profile_id, status, changes_json, before_json,
                        validation_errors_json, checksum, based_on_release_id,
                        rollback_of_release_id, created_by, created_at
                    )
                    VALUES (
                        :release_key, :profile_id, 'draft', {self._json_bind('changes_json')},
                        {self._json_bind('before_json')}, {self._json_bind('validation_errors_json')},
                        :checksum, :based_on_release_id, :rollback_of_release_id,
                        :created_by, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "release_key": release_key,
                    "profile_id": profile_id,
                    "changes_json": _json_dump(changes),
                    "before_json": "{}",
                    "validation_errors_json": "[]",
                    "checksum": checksum,
                    "based_on_release_id": based_on_release_id,
                    "rollback_of_release_id": rollback_of_release_id,
                    "created_by": operator,
                },
            )
            row = conn.execute(
                text("SELECT * FROM config_releases WHERE release_key = :release_key"),
                {"release_key": release_key},
            ).mappings().first()
        return _release_row(row)

    def get_release(self, release_id: int) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM config_releases WHERE id = :release_id"),
                {"release_id": int(release_id)},
            ).mappings().first()
        return _release_row(row) if row else None

    def list_releases(self, *, profile_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        where = ""
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 200))}
        if profile_id:
            where = "WHERE profile_id = :profile_id"
            params["profile_id"] = str(profile_id)
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT * FROM config_releases
                    {where}
                    ORDER BY id DESC
                    LIMIT :limit
                    """
                ),
                params,
            ).mappings().all()
        return [_release_row(row) for row in rows]

    def set_validation(self, *, release_id: int, errors: list[dict[str, str]]) -> dict[str, Any]:
        status = "validation_failed" if errors else "validated"
        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    f"""
                    UPDATE config_releases
                    SET status = :status,
                        validation_errors_json = {self._json_bind('validation_errors_json')},
                        validated_at = CURRENT_TIMESTAMP
                    WHERE id = :release_id
                      AND status IN ('draft', 'validated', 'validation_failed')
                    """
                ),
                {
                    "release_id": int(release_id),
                    "status": status,
                    "validation_errors_json": _json_dump(errors),
                },
            )
            if result.rowcount != 1:
                raise ValueError("config release is not editable")
            row = conn.execute(
                text("SELECT * FROM config_releases WHERE id = :release_id"),
                {"release_id": int(release_id)},
            ).mappings().first()
        return _release_row(row)

    def publish(
        self,
        *,
        release_id: int,
        expected_checksum: str,
        operator: str,
        profile: DeploymentProfile,
        sensitive_keys: set[str],
        expected_settings: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        with self._engine.begin() as conn:
            release = self._get_release_for_update(conn, release_id)
            if not release:
                raise KeyError("config release not found")
            if release["status"] != "validated":
                raise ValueError("config release must be validated before publish")
            if release["profile_id"] != profile.profile_id:
                raise ValueError("deployment profile does not match config release")
            if release["checksum"] != str(expected_checksum or "").strip():
                raise ValueError("config release checksum changed; refresh before publishing")

            if self._engine.dialect.name == "postgresql":
                conn.execute(text("LOCK TABLE app_settings IN SHARE ROW EXCLUSIVE MODE"))
            row_lock = " FOR UPDATE" if self._engine.dialect.name == "postgresql" else ""
            for key, expected in sorted(expected_settings.items()):
                row = conn.execute(
                    text(
                        f"SELECT key, value FROM app_settings "
                        f"WHERE key = :key{row_lock}"
                    ),
                    {"key": key},
                ).mappings().first()
                expected_exists = bool(expected.get("exists"))
                current_exists = row is not None
                expected_value = str(expected.get("value") or "")
                current_value = str((row or {}).get("value") or "")
                if expected_exists != current_exists or not hmac.compare_digest(
                    expected_value,
                    current_value,
                ):
                    raise ValueError(
                        f"app_settings changed after validation: {key}; revalidate before publishing"
                    )

            changes = dict(release.get("changes") or {})
            before: dict[str, dict[str, Any]] = {}
            for key in sorted(changes):
                row = conn.execute(
                    text("SELECT key, value FROM app_settings WHERE key = :key"),
                    {"key": key},
                ).mappings().first()
                current_value = str((row or {}).get("value") or "")
                if key in sensitive_keys and row and current_value and not is_secret_reference(current_value):
                    raise ValueError(f"{key} contains a legacy raw secret; migrate it to Secret Store before publish")
                before[key] = {"exists": bool(row), "value": current_value if row else ""}

            conn.execute(
                text(
                    """
                    UPDATE config_releases
                    SET status = 'superseded'
                    WHERE profile_id = :profile_id AND status = 'published'
                    """
                ),
                {"profile_id": profile.profile_id},
            )
            for key, value in sorted(changes.items()):
                if value is None:
                    conn.execute(text("DELETE FROM app_settings WHERE key = :key"), {"key": key})
                    continue
                conn.execute(
                    text(
                        """
                        INSERT INTO app_settings (key, value, updated_at)
                        VALUES (:key, :value, CURRENT_TIMESTAMP)
                        ON CONFLICT(key) DO UPDATE SET
                            value = excluded.value,
                            updated_at = CURRENT_TIMESTAMP
                        """
                    ),
                    {"key": key, "value": value},
                )

            conn.execute(
                text(
                    f"""
                    UPDATE config_releases
                    SET status = 'published',
                        before_json = {self._json_bind('before_json')},
                        published_by = :published_by,
                        published_at = CURRENT_TIMESTAMP
                    WHERE id = :release_id
                    """
                ),
                {
                    "release_id": int(release_id),
                    "before_json": _json_dump(before),
                    "published_by": operator,
                },
            )
            conn.execute(
                text(
                    f"""
                    INSERT INTO deployment_profile_state (
                        profile_id, core_version, config_schema_version,
                        activation_mode, enabled_capabilities_json,
                        runtime_roles_json, active_config_release_id,
                        updated_by, updated_at
                    )
                    VALUES (
                        :profile_id, :core_version, :config_schema_version,
                        :activation_mode, {self._json_bind('enabled_capabilities_json')},
                        {self._json_bind('runtime_roles_json')}, :active_config_release_id,
                        :updated_by, CURRENT_TIMESTAMP
                    )
                    ON CONFLICT(profile_id) DO UPDATE SET
                        core_version = excluded.core_version,
                        config_schema_version = excluded.config_schema_version,
                        activation_mode = excluded.activation_mode,
                        enabled_capabilities_json = excluded.enabled_capabilities_json,
                        runtime_roles_json = excluded.runtime_roles_json,
                        active_config_release_id = excluded.active_config_release_id,
                        updated_by = excluded.updated_by,
                        updated_at = CURRENT_TIMESTAMP
                    """
                ),
                {
                    "profile_id": profile.profile_id,
                    "core_version": profile.core_version,
                    "config_schema_version": profile.config_schema_version,
                    "activation_mode": profile.activation_mode,
                    "enabled_capabilities_json": _json_dump(profile.enabled_capabilities),
                    "runtime_roles_json": _json_dump(profile.runtime_roles),
                    "active_config_release_id": int(release_id),
                    "updated_by": operator,
                },
            )
            row = conn.execute(
                text("SELECT * FROM config_releases WHERE id = :release_id"),
                {"release_id": int(release_id)},
            ).mappings().first()
        invalidate_runtime_settings_request_snapshot()
        return _release_row(row)

    def get_profile_state(self, profile_id: str) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM deployment_profile_state WHERE profile_id = :profile_id"),
                {"profile_id": str(profile_id or "").strip()},
            ).mappings().first()
        if not row:
            return None
        result = dict(row)
        result["enabled_capabilities"] = _json_load(result.pop("enabled_capabilities_json", []), [])
        result["runtime_roles"] = _json_load(result.pop("runtime_roles_json", []), [])
        return result

    def get_app_settings(self, keys: list[str]) -> dict[str, str]:
        result: dict[str, str] = {}
        with self._engine.connect() as conn:
            for key in sorted({str(item or "").strip() for item in keys if str(item or "").strip()}):
                row = conn.execute(
                    text("SELECT value FROM app_settings WHERE key = :key"),
                    {"key": key},
                ).mappings().first()
                if row is not None:
                    result[key] = str(row.get("value") or "")
        return result

    def get_app_settings_state(
        self,
        keys: list[str] | set[str],
    ) -> dict[str, dict[str, Any]]:
        normalized = sorted(
            {str(item or "").strip() for item in keys if str(item or "").strip()}
        )
        result: dict[str, dict[str, Any]] = {}
        with self._engine.connect() as conn:
            for key in normalized:
                row = conn.execute(
                    text("SELECT value FROM app_settings WHERE key = :key"),
                    {"key": key},
                ).mappings().first()
                result[key] = {
                    "exists": row is not None,
                    "value": str((row or {}).get("value") or ""),
                }
        return result

    def _get_release_for_update(self, conn: Connection, release_id: int) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if self._engine.dialect.name == "postgresql" else ""
        row = conn.execute(
            text(f"SELECT * FROM config_releases WHERE id = :release_id{suffix}"),
            {"release_id": int(release_id)},
        ).mappings().first()
        return _release_row(row) if row else None

    def _json_bind(self, name: str) -> str:
        if self._engine.dialect.name == "postgresql":
            return f"CAST(:{name} AS jsonb)"
        return f":{name}"


def config_release_checksum(*, profile_id: str, changes: dict[str, str | None]) -> str:
    payload = _json_dump({"profile_id": profile_id, "changes": changes})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _json_load(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return default


def _release_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    result = dict(row)
    result["changes"] = _json_load(result.pop("changes_json", {}), {})
    result["before"] = _json_load(result.pop("before_json", {}), {})
    result["validation_errors"] = _json_load(result.pop("validation_errors_json", []), [])
    return result


__all__ = ["ConfigReleaseRepository", "config_release_checksum"]
