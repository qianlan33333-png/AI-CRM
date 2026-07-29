from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from aicrm_next.platform.platform_foundation.admin_audit import (
    AdminAuditRecord,
    build_admin_audit_port,
)
from aicrm_next.runtime_configuration import (
    RUNTIME_CONFIG_CUTOVER_KEYS_KEY,
    parse_runtime_config_cutover_keys,
)
from aicrm_next.platform.shared.db_session import get_engine
from aicrm_next.platform.shared.runtime_settings import invalidate_runtime_settings_request_snapshot
from aicrm_next.platform.shared.safe_logging import safe_log_exception
from aicrm_next.platform.shared.secret_store import FileSecretStore, is_secret_reference
from aicrm_next.platform.shared.sensitive_data import redact_sensitive_data

from .settings import SENSITIVE_KEYS

LOGGER = logging.getLogger(__name__)


class AdminConfigRepository:
    def __init__(self, *, engine: Engine | None = None) -> None:
        self._engine = engine or get_engine()

    def list_app_settings(self) -> list[dict[str, Any]]:
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT key, value, updated_at
                        FROM app_settings
                        ORDER BY key ASC
                        """
                    )
                ).mappings().all()
        except SQLAlchemyError as exc:
            safe_log_exception(LOGGER, "admin config app_settings read unavailable", exc, level=logging.WARNING)
            return []
        return [dict(row) for row in rows]

    def get_app_setting(self, key: str) -> dict[str, Any] | None:
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT key, value, updated_at
                        FROM app_settings
                        WHERE key = :key
                        """
                    ),
                    {"key": str(key or "").strip()},
                ).mappings().first()
        except SQLAlchemyError as exc:
            safe_log_exception(LOGGER, "admin config app_setting read unavailable", exc, level=logging.WARNING)
            return None
        return dict(row) if row else None

    def upsert_app_setting(self, *, key: str, value: str) -> dict[str, Any]:
        normalized_key = str(key or "").strip()
        normalized_value = str(value if value is not None else "")
        if normalized_key in SENSITIVE_KEYS:
            if is_secret_reference(normalized_value):
                raise ValueError("secret references cannot be submitted as setting values")
        with self._engine.begin() as conn:
            if self._engine.dialect.name == "postgresql":
                conn.execute(text("LOCK TABLE app_settings IN SHARE ROW EXCLUSIVE MODE"))
            self._assert_legacy_writes_allowed_in_connection(
                conn,
                {normalized_key},
            )
            stored_value = normalized_value
            if normalized_key in SENSITIVE_KEYS:
                current_value = str(
                    conn.execute(
                        text("SELECT value FROM app_settings WHERE key = :key"),
                        {"key": normalized_key},
                    ).scalar_one_or_none()
                    or ""
                ).strip()
                current_reference = (
                    current_value if is_secret_reference(current_value) else ""
                )
                stored_value = FileSecretStore.from_environment().write(
                    normalized_key,
                    normalized_value,
                    current_reference=current_reference,
                )
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
                {"key": normalized_key, "value": stored_value},
            )
            row = conn.execute(
                text("SELECT key, value, updated_at FROM app_settings WHERE key = :key"),
                {"key": normalized_key},
            ).mappings().first()
        invalidate_runtime_settings_request_snapshot()
        return dict(row) if row else {"key": normalized_key, "value": stored_value}

    def assert_legacy_writes_allowed(self, keys: set[str]) -> None:
        normalized = {str(key or "").strip() for key in keys if str(key or "").strip()}
        if not normalized:
            return
        if RUNTIME_CONFIG_CUTOVER_KEYS_KEY in normalized:
            raise ValueError("runtime cutover catalog must be changed through Config Release")
        try:
            with self._engine.connect() as conn:
                self._assert_legacy_writes_allowed_in_connection(conn, normalized)
        except SQLAlchemyError as exc:
            safe_log_exception(
                LOGGER,
                "admin config cutover guard unavailable",
                exc,
                level=logging.WARNING,
            )
            raise ValueError("runtime cutover guard unavailable; legacy write blocked") from exc

    @staticmethod
    def _assert_legacy_writes_allowed_in_connection(
        conn: Connection,
        keys: set[str],
    ) -> None:
        if RUNTIME_CONFIG_CUTOVER_KEYS_KEY in keys:
            raise ValueError("runtime cutover catalog must be changed through Config Release")
        value = conn.execute(
            text("SELECT value FROM app_settings WHERE key = :key"),
            {"key": RUNTIME_CONFIG_CUTOVER_KEYS_KEY},
        ).scalar_one_or_none()
        active = parse_runtime_config_cutover_keys(value)
        blocked = sorted(keys & active)
        if blocked:
            raise ValueError(
                "runtime settings already owned by Config Release: "
                + ", ".join(blocked)
            )

    def insert_audit_log(
        self,
        *,
        operator: str,
        action_type: str,
        target_type: str,
        target_id: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        with self._engine.begin() as conn:
            build_admin_audit_port().append_sqlalchemy(
                conn,
                dialect_name=str(
                    getattr(getattr(self._engine, "dialect", None), "name", "postgresql")
                ),
                record=AdminAuditRecord(
                    operator=operator,
                    action_type=action_type,
                    target_type=target_type,
                    target_id=target_id,
                    before=redact_sensitive_data(before or {}, sensitive_keys=SENSITIVE_KEYS),
                    after=redact_sensitive_data(after or {}, sensitive_keys=SENSITIVE_KEYS),
                ),
            )

    def latest_audit_map(self, *, target_type: str, target_ids: list[str]) -> dict[str, dict[str, Any]]:
        normalized = [str(item or "").strip() for item in target_ids if str(item or "").strip()]
        if not normalized:
            return {}
        placeholders = ", ".join(f":target_{index}" for index, _ in enumerate(normalized))
        params = {f"target_{index}": value for index, value in enumerate(normalized)}
        params["target_type"] = str(target_type or "").strip()
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        f"""
                        SELECT id, operator, action_type, target_type, target_id, before_json, after_json, created_at
                        FROM admin_operation_logs
                        WHERE target_type = :target_type AND target_id IN ({placeholders})
                        ORDER BY id DESC
                        """
                    ),
                    params,
                ).mappings().all()
        except SQLAlchemyError as exc:
            safe_log_exception(LOGGER, "admin config audit map read unavailable", exc, level=logging.WARNING)
            return {}
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            target_id = str(row.get("target_id") or "").strip()
            if target_id and target_id not in result:
                result[target_id] = dict(row)
        return result

    def list_audit_logs(self, *, target_type: str = "", limit: int = 20) -> list[dict[str, Any]]:
        filters = ""
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 200))}
        if target_type:
            filters = "WHERE target_type = :target_type"
            params["target_type"] = str(target_type or "").strip()
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        f"""
                        SELECT id, operator, action_type, target_type, target_id, before_json, after_json, created_at
                        FROM admin_operation_logs
                        {filters}
                        ORDER BY id DESC
                        LIMIT :limit
                        """
                    ),
                    params,
                ).mappings().all()
        except SQLAlchemyError as exc:
            safe_log_exception(LOGGER, "admin config audit log read unavailable", exc, level=logging.WARNING)
            return []
        return [dict(row) for row in rows]

    def list_mcp_tool_settings(self) -> list[dict[str, Any]]:
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT
                            tool_name,
                            tool_group,
                            display_name,
                            description_override,
                            enabled,
                            visible_in_console,
                            show_sample_args,
                            show_sample_output,
                            sort_order,
                            updated_at
                        FROM mcp_tool_settings
                        ORDER BY sort_order ASC, tool_name ASC
                        """
                    )
                ).mappings().all()
        except SQLAlchemyError as exc:
            safe_log_exception(LOGGER, "admin config mcp_tool_settings read unavailable", exc, level=logging.WARNING)
            return []
        return [dict(row) for row in rows]

    def get_mcp_tool_setting(self, tool_name: str) -> dict[str, Any] | None:
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT
                            tool_name,
                            tool_group,
                            display_name,
                            description_override,
                            enabled,
                            visible_in_console,
                            show_sample_args,
                            show_sample_output,
                            sort_order,
                            updated_at
                        FROM mcp_tool_settings
                        WHERE tool_name = :tool_name
                        """
                    ),
                    {"tool_name": str(tool_name or "").strip()},
                ).mappings().first()
        except SQLAlchemyError as exc:
            safe_log_exception(LOGGER, "admin config mcp_tool_setting read unavailable", exc, level=logging.WARNING)
            return None
        return dict(row) if row else None

    def upsert_mcp_tool_setting(
        self,
        *,
        tool_name: str,
        tool_group: str,
        display_name: str,
        description_override: str,
        enabled: bool,
        visible_in_console: bool,
        show_sample_args: bool,
        show_sample_output: bool,
        sort_order: int,
    ) -> dict[str, Any]:
        payload = {
            "tool_name": str(tool_name or "").strip(),
            "tool_group": str(tool_group or "").strip(),
            "display_name": str(display_name or "").strip(),
            "description_override": str(description_override or "").strip(),
            "enabled": bool(enabled),
            "visible_in_console": bool(visible_in_console),
            "show_sample_args": bool(show_sample_args),
            "show_sample_output": bool(show_sample_output),
            "sort_order": int(sort_order or 0),
        }
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO mcp_tool_settings (
                        tool_name,
                        tool_group,
                        display_name,
                        description_override,
                        enabled,
                        visible_in_console,
                        show_sample_args,
                        show_sample_output,
                        sort_order,
                        updated_at
                    )
                    VALUES (
                        :tool_name,
                        :tool_group,
                        :display_name,
                        :description_override,
                        :enabled,
                        :visible_in_console,
                        :show_sample_args,
                        :show_sample_output,
                        :sort_order,
                        CURRENT_TIMESTAMP
                    )
                    ON CONFLICT(tool_name) DO UPDATE SET
                        tool_group = excluded.tool_group,
                        display_name = excluded.display_name,
                        description_override = excluded.description_override,
                        enabled = excluded.enabled,
                        visible_in_console = excluded.visible_in_console,
                        show_sample_args = excluded.show_sample_args,
                        show_sample_output = excluded.show_sample_output,
                        sort_order = excluded.sort_order,
                        updated_at = CURRENT_TIMESTAMP
                    """
                ),
                payload,
            )
        return self.get_mcp_tool_setting(payload["tool_name"]) or payload

    def get_marketing_automation_config(self, automation_key: str) -> dict[str, Any] | None:
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT
                            id,
                            automation_key,
                            automation_name,
                            target_event,
                            channel_type,
                            status,
                            do_not_start_after_hour,
                            config_payload_json,
                            created_at,
                            updated_at
                        FROM marketing_automation_configs
                        WHERE automation_key = :automation_key
                        """
                    ),
                    {"automation_key": str(automation_key or "").strip()},
                ).mappings().first()
        except SQLAlchemyError as exc:
            safe_log_exception(LOGGER, "admin config marketing automation config read unavailable", exc, level=logging.WARNING)
            return None
        return dict(row) if row else None

    def list_marketing_automation_question_rules(self, automation_config_id: int) -> list[dict[str, Any]]:
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT
                            id,
                            automation_config_id,
                            questionnaire_id,
                            question_id,
                            rule_code,
                            rule_name,
                            answer_match_type,
                            answer_match_value_json,
                            score_delta,
                            segment_hint,
                            stage_hint,
                            is_active,
                            sort_order,
                            rule_payload_json,
                            created_at,
                            updated_at
                        FROM marketing_automation_question_rules
                        WHERE automation_config_id = :automation_config_id
                          AND is_active = TRUE
                        ORDER BY sort_order ASC, id ASC
                        """
                    ),
                    {"automation_config_id": int(automation_config_id or 0)},
                ).mappings().all()
        except SQLAlchemyError as exc:
            safe_log_exception(LOGGER, "admin config marketing automation rules read unavailable", exc, level=logging.WARNING)
            return []
        return [dict(row) for row in rows]

    def get_questionnaire(self, questionnaire_id: int) -> dict[str, Any] | None:
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT id, name, title, is_disabled, created_at, updated_at
                        FROM questionnaires
                        WHERE id = :questionnaire_id
                        """
                    ),
                    {"questionnaire_id": int(questionnaire_id or 0)},
                ).mappings().first()
        except SQLAlchemyError as exc:
            safe_log_exception(LOGGER, "admin config questionnaire lookup unavailable", exc, level=logging.WARNING)
            return None
        return dict(row) if row else None

    def list_questionnaire_questions(self, questionnaire_id: int) -> list[dict[str, Any]]:
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT id, questionnaire_id, type, title, required, sort_order
                        FROM questionnaire_questions
                        WHERE questionnaire_id = :questionnaire_id
                        ORDER BY sort_order ASC, id ASC
                        """
                    ),
                    {"questionnaire_id": int(questionnaire_id or 0)},
                ).mappings().all()
        except SQLAlchemyError as exc:
            safe_log_exception(LOGGER, "admin config questionnaire questions read unavailable", exc, level=logging.WARNING)
            return []
        return [dict(row) for row in rows]

    def list_questionnaire_options(self, question_ids: list[int]) -> list[dict[str, Any]]:
        ids = [int(item) for item in question_ids if int(item or 0) > 0]
        if not ids:
            return []
        placeholders = ", ".join(f":id_{index}" for index, _ in enumerate(ids))
        params = {f"id_{index}": value for index, value in enumerate(ids)}
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        f"""
                        SELECT id, question_id, option_text, sort_order
                        FROM questionnaire_options
                        WHERE question_id IN ({placeholders})
                        ORDER BY question_id ASC, sort_order ASC, id ASC
                        """
                    ),
                    params,
                ).mappings().all()
        except SQLAlchemyError as exc:
            safe_log_exception(LOGGER, "admin config questionnaire options read unavailable", exc, level=logging.WARNING)
            return []
        return [dict(row) for row in rows]

    def upsert_marketing_automation_config(
        self,
        *,
        automation_key: str,
        automation_name: str,
        target_event: str,
        channel_type: str,
        status: str,
        do_not_start_after_hour: int,
        config_payload: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "automation_key": str(automation_key or "").strip(),
            "automation_name": str(automation_name or "").strip(),
            "target_event": str(target_event or "").strip(),
            "channel_type": str(channel_type or "").strip(),
            "status": str(status or "").strip(),
            "do_not_start_after_hour": int(do_not_start_after_hour or 0),
            "config_payload_json": json.dumps(config_payload or {}, ensure_ascii=False, sort_keys=True, default=str),
        }
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO marketing_automation_configs (
                        automation_key,
                        automation_name,
                        target_event,
                        channel_type,
                        status,
                        do_not_start_after_hour,
                        config_payload_json,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        :automation_key,
                        :automation_name,
                        :target_event,
                        :channel_type,
                        :status,
                        :do_not_start_after_hour,
                        :config_payload_json,
                        CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP
                    )
                    ON CONFLICT(automation_key) DO UPDATE SET
                        automation_name = excluded.automation_name,
                        target_event = excluded.target_event,
                        channel_type = excluded.channel_type,
                        status = excluded.status,
                        do_not_start_after_hour = excluded.do_not_start_after_hour,
                        config_payload_json = excluded.config_payload_json,
                        updated_at = CURRENT_TIMESTAMP
                    """
                ),
                payload,
            )
        return self.get_marketing_automation_config(payload["automation_key"]) or payload

    def replace_marketing_automation_question_rules(
        self,
        *,
        automation_config_id: int,
        questionnaire_id: int,
        rules: list[dict[str, Any]],
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text("DELETE FROM marketing_automation_question_rules WHERE automation_config_id = :automation_config_id"),
                {"automation_config_id": int(automation_config_id or 0)},
            )
            for index, rule in enumerate(rules, start=1):
                question_id = int(rule.get("questionnaire_question_id") or rule.get("question_id") or 0)
                conn.execute(
                    text(
                        """
                        INSERT INTO marketing_automation_question_rules (
                            automation_config_id,
                            questionnaire_id,
                            question_id,
                            rule_code,
                            rule_name,
                            answer_match_type,
                            answer_match_value_json,
                            score_delta,
                            segment_hint,
                            stage_hint,
                            is_active,
                            sort_order,
                            rule_payload_json,
                            created_at,
                            updated_at
                        )
                        VALUES (
                            :automation_config_id,
                            :questionnaire_id,
                            :question_id,
                            :rule_code,
                            :rule_name,
                            'any_of',
                            :answer_match_value_json,
                            0,
                            '',
                            '',
                            TRUE,
                            :sort_order,
                            :rule_payload_json,
                            CURRENT_TIMESTAMP,
                            CURRENT_TIMESTAMP
                        )
                        """
                    ),
                    {
                        "automation_config_id": int(automation_config_id or 0),
                        "questionnaire_id": int(questionnaire_id or 0),
                        "question_id": question_id,
                        "rule_code": str(rule.get("rule_code") or f"question-{question_id}"),
                        "rule_name": str(rule.get("rule_name") or rule.get("question_title") or f"question-{question_id}"),
                        "answer_match_value_json": json.dumps(rule.get("hit_option_ids_json") or [], ensure_ascii=False, default=str),
                        "sort_order": int(rule.get("sort_order") or index),
                        "rule_payload_json": json.dumps(rule.get("rule_payload") or {"questionnaire_id": int(questionnaire_id or 0)}, ensure_ascii=False, default=str),
                    },
                )

    def count_admin_users(self) -> int:
        try:
            with self._engine.connect() as conn:
                value = conn.execute(text("SELECT COUNT(*) FROM admin_users")).scalar()
        except SQLAlchemyError as exc:
            safe_log_exception(LOGGER, "admin users count unavailable", exc, level=logging.WARNING)
            return 0
        return int(value or 0)

    def list_admin_users(self) -> list[dict[str, Any]]:
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT
                            id, wecom_userid, wecom_corpid, display_name, is_active, auth_source,
                            last_login_at, created_at, updated_at, updated_by, login_enabled, admin_level,
                            session_version
                        FROM admin_users
                        ORDER BY id ASC
                        """
                    )
                ).mappings().all()
        except SQLAlchemyError as exc:
            safe_log_exception(LOGGER, "admin users read unavailable", exc, level=logging.WARNING)
            return []
        return [dict(row) for row in rows]

    def list_admin_user_roles(self, admin_user_ids: list[int]) -> list[dict[str, Any]]:
        ids = [int(item) for item in admin_user_ids if int(item or 0) > 0]
        if not ids:
            return []
        placeholders = ", ".join(f":id_{index}" for index, _ in enumerate(ids))
        params = {f"id_{index}": value for index, value in enumerate(ids)}
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        f"""
                        SELECT admin_user_id, role_code
                        FROM admin_user_roles
                        WHERE admin_user_id IN ({placeholders})
                        ORDER BY admin_user_id ASC, role_code ASC, id ASC
                        """
                    ),
                    params,
                ).mappings().all()
        except SQLAlchemyError as exc:
            safe_log_exception(LOGGER, "admin user roles read unavailable", exc, level=logging.WARNING)
            return []
        return [dict(row) for row in rows]

    def get_admin_user(self, user_id: int) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT
                        id, wecom_userid, wecom_corpid, display_name, is_active, auth_source,
                        last_login_at, created_at, updated_at, updated_by, login_enabled, admin_level,
                        session_version
                    FROM admin_users
                    WHERE id = :id
                    """
                ),
                {"id": int(user_id or 0)},
            ).mappings().first()
        return dict(row) if row else None

    def get_admin_user_by_wecom_userid(self, wecom_userid: str) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT
                        id, wecom_userid, wecom_corpid, display_name, is_active, auth_source,
                        last_login_at, created_at, updated_at, updated_by, login_enabled, admin_level,
                        session_version
                    FROM admin_users
                    WHERE wecom_userid = :wecom_userid
                    ORDER BY id ASC
                    LIMIT 1
                    """
                ),
                {"wecom_userid": str(wecom_userid or "").strip()},
            ).mappings().first()
        return dict(row) if row else None

    def get_active_wecom_directory_member(self, wecom_userid: str) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT
                        wecom_userid, corp_id, display_name, department_name,
                        position, avatar_url, is_active, wecom_status
                    FROM admin_wecom_directory_members
                    WHERE wecom_userid = :wecom_userid
                      AND is_active = TRUE
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 1
                    """
                ),
                {"wecom_userid": str(wecom_userid or "").strip()},
            ).mappings().first()
        return dict(row) if row else None

    def admin_user_role_codes(self, admin_user_id: int) -> list[str]:
        rows = self.list_admin_user_roles([int(admin_user_id or 0)])
        return [str(row.get("role_code") or "").strip() for row in rows if str(row.get("role_code") or "").strip()]

    def add_admin_user_role(self, *, admin_user_id: int, role_code: str) -> None:
        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO admin_user_roles (admin_user_id, role_code, created_at)
                    VALUES (:admin_user_id, :role_code, CURRENT_TIMESTAMP)
                    ON CONFLICT(admin_user_id, role_code) DO NOTHING
                    """
                ),
                {"admin_user_id": int(admin_user_id), "role_code": str(role_code or "").strip()},
            )
            if result.rowcount:
                conn.execute(
                    text("UPDATE admin_users SET session_version = session_version + 1 WHERE id = :id"),
                    {"id": int(admin_user_id)},
                )

    def remove_admin_user_role(self, *, admin_user_id: int, role_code: str) -> None:
        with self._engine.begin() as conn:
            result = conn.execute(
                text("DELETE FROM admin_user_roles WHERE admin_user_id = :admin_user_id AND role_code = :role_code"),
                {"admin_user_id": int(admin_user_id), "role_code": str(role_code or "").strip()},
            )
            if result.rowcount:
                conn.execute(
                    text("UPDATE admin_users SET session_version = session_version + 1 WHERE id = :id"),
                    {"id": int(admin_user_id)},
                )

    def disable_service_period_grid_account(self, *, admin_user_id: int, operator: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE admin_users
                    SET is_active = FALSE,
                        login_enabled = FALSE,
                        session_version = session_version + 1,
                        updated_at = CURRENT_TIMESTAMP,
                        updated_by = :operator
                    WHERE id = :admin_user_id
                      AND auth_source = 'service_period_grid_collaboration'
                    """
                ),
                {"admin_user_id": int(admin_user_id), "operator": str(operator or "system").strip()},
            )

    def list_super_admin_users(self) -> list[dict[str, Any]]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT DISTINCT
                        users.id,
                        users.wecom_userid,
                        users.display_name,
                        users.is_active,
                        users.login_enabled,
                        COALESCE(directory.avatar_url, '') AS avatar_url
                    FROM admin_users users
                    LEFT JOIN admin_user_roles roles ON roles.admin_user_id = users.id
                    LEFT JOIN admin_wecom_directory_members directory
                      ON directory.wecom_userid = users.wecom_userid
                     AND directory.is_active = TRUE
                    WHERE users.is_active = TRUE
                      AND users.login_enabled = TRUE
                      AND (users.admin_level = 'super_admin' OR roles.role_code = 'super_admin')
                    ORDER BY users.id
                    """
                )
            ).mappings().all()
        return [dict(row) for row in rows]

    def upsert_admin_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = int(payload.get("id") or 0)
        if user_id:
            with self._engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE admin_users
                        SET wecom_userid = :wecom_userid,
                            wecom_corpid = :wecom_corpid,
                            display_name = :display_name,
                            is_active = :is_active,
                            auth_source = :auth_source,
                            updated_at = CURRENT_TIMESTAMP,
                            updated_by = :updated_by,
                            login_enabled = :login_enabled,
                            admin_level = :admin_level,
                            session_version = session_version + 1
                        WHERE id = :id
                        """
                    ),
                    {**payload, "id": user_id},
                )
            return self.get_admin_user(user_id) or {}
        existing = self.get_admin_user_by_wecom_userid(str(payload.get("wecom_userid") or ""))
        if existing:
            payload = {**payload, "id": existing["id"]}
            return self.upsert_admin_user(payload)
        with self._engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO admin_users (
                        wecom_userid, wecom_corpid, display_name, is_active, auth_source,
                        updated_at, updated_by, login_enabled, admin_level
                    )
                    VALUES (
                        :wecom_userid, :wecom_corpid, :display_name, :is_active, :auth_source,
                        CURRENT_TIMESTAMP, :updated_by, :login_enabled, :admin_level
                    )
                    RETURNING id
                    """
                ),
                payload,
            ).mappings().first()
        return self.get_admin_user(int((row or {}).get("id") or 0)) or {}

    def replace_admin_user_roles(self, *, admin_user_id: int, role_codes: list[str]) -> None:
        with self._engine.begin() as conn:
            conn.execute(text("DELETE FROM admin_user_roles WHERE admin_user_id = :id"), {"id": int(admin_user_id)})
            for role_code in role_codes:
                conn.execute(
                    text(
                        """
                        INSERT INTO admin_user_roles (admin_user_id, role_code, created_at)
                        VALUES (:admin_user_id, :role_code, CURRENT_TIMESTAMP)
                        ON CONFLICT(admin_user_id, role_code) DO NOTHING
                        """
                    ),
                    {"admin_user_id": int(admin_user_id), "role_code": str(role_code or "").strip()},
                )
            conn.execute(
                text("UPDATE admin_users SET session_version = session_version + 1 WHERE id = :id"),
                {"id": int(admin_user_id)},
            )

    def list_admin_login_audit(self, *, limit: int = 20) -> list[dict[str, Any]]:
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT
                            a.id, a.admin_user_id, a.login_type, a.login_result, a.ip, a.user_agent, a.created_at,
                            u.wecom_userid, u.display_name
                        FROM admin_login_audit a
                        LEFT JOIN admin_users u ON u.id = a.admin_user_id
                        ORDER BY a.id DESC
                        LIMIT :limit
                        """
                    ),
                    {"limit": max(1, min(int(limit), 200))},
                ).mappings().all()
        except SQLAlchemyError as exc:
            safe_log_exception(LOGGER, "admin login audit read unavailable", exc, level=logging.WARNING)
            return []
        return [dict(row) for row in rows]

    def insert_admin_login_audit(
        self,
        *,
        admin_user_id: int,
        login_type: str,
        login_result: str,
        ip: str = "",
        user_agent: str = "",
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO admin_login_audit (
                        admin_user_id, login_type, login_result, ip, user_agent, created_at
                    )
                    VALUES (
                        :admin_user_id, :login_type, :login_result, :ip, :user_agent, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "admin_user_id": int(admin_user_id or 0) or None,
                    "login_type": str(login_type or "").strip(),
                    "login_result": str(login_result or "").strip(),
                    "ip": str(ip or "").strip(),
                    "user_agent": str(user_agent or "").strip()[:500],
                },
            )

    def update_admin_last_login(self, *, admin_user_id: int) -> None:
        if int(admin_user_id or 0) <= 0:
            return
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE admin_users
                    SET last_login_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id
                    """
                ),
                {"id": int(admin_user_id or 0)},
            )
