from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError

from aicrm_next.platform.platform_foundation.admin_audit import (
    AdminAuditRecord,
    build_admin_audit_port,
)

from .repository import (
    _dependency_source_type,
    _json_dumps,
    _public_row,
    _text,
    default_refresh_started_at,
    next_daily_refresh_at,
    text,
)
from .automation_binding.repository import (
    AudienceAutomationBindingRepository,
    AutomationAlreadyBoundError,
    AutomationNotActiveError,
    AutomationNotFoundError,
    BindingStateInvalidError,
)


class AudienceGroupNameConflictError(RuntimeError):
    pass


class AudienceGroupNotEmptyError(RuntimeError):
    pass


class ActivePackageTemplateUpdateError(RuntimeError):
    pass


class ArchivedPackageTemplateUpdateError(RuntimeError):
    pass


class AudiencePackageRepositoryMixin:
    def list_packages(self) -> list[dict[str, Any]]:
        return self._all(
            """
            SELECT p.*, v.version_number AS current_version_number
            FROM ai_audience_package p
            LEFT JOIN ai_audience_package_version v ON v.id = p.current_version_id
            ORDER BY p.id DESC
            """
        )

    def list_package_summaries(
        self,
        *,
        limit: int = 200,
        offset: int = 0,
        group_id: int | None = None,
        ungrouped: bool = False,
    ) -> list[dict[str, Any]]:
        group_clause = ""
        params: dict[str, Any] = {
            "limit": max(1, min(int(limit or 200), 200)),
            "offset": max(0, int(offset or 0)),
        }
        if ungrouped:
            group_clause = "AND p.group_id IS NULL"
        elif group_id is not None:
            group_clause = "AND p.group_id = :group_id"
            params["group_id"] = int(group_id)
        return self._all(
            f"""
            WITH member_counts AS (
                SELECT
                    package_id,
                    COUNT(*) FILTER (WHERE status = 'active') AS member_count
                FROM ai_audience_member_current
                GROUP BY package_id
            ),
            latest_runs AS (
                SELECT DISTINCT ON (package_id)
                    package_id,
                    refresh_finished_at,
                    refresh_started_at,
                    status AS run_status
                FROM ai_audience_package_run
                ORDER BY package_id, refresh_finished_at DESC NULLS LAST, id DESC
            )
            SELECT
                p.id,
                p.package_key,
                p.name,
                p.status,
                COALESCE(mc.member_count, 0) AS member_count,
                lr.refresh_finished_at AS last_refreshed_at,
                p.incremental_enabled,
                p.incremental_interval_seconds,
                p.daily_enabled,
                p.daily_refresh_time,
                p.group_id,
                g.name AS group_name,
                p.updated_at,
                v.template_key,
                v.template_version,
                COUNT(*) OVER () AS total_count
            FROM ai_audience_package p
            LEFT JOIN ai_audience_package_version v ON v.id = p.current_version_id
            LEFT JOIN ai_audience_package_group g ON g.id = p.group_id
            LEFT JOIN member_counts mc ON mc.package_id = p.id
            LEFT JOIN latest_runs lr ON lr.package_id = p.id
            WHERE p.status <> 'archived'
              {group_clause}
            ORDER BY p.updated_at DESC, p.id DESC
            LIMIT :limit
            OFFSET :offset
            """,
            params,
        )

    def list_package_groups(self) -> list[dict[str, Any]]:
        return self._all(
            """
            SELECT
                g.id,
                g.name,
                COUNT(p.id) FILTER (WHERE p.status <> 'archived') AS package_count,
                g.created_at,
                g.updated_at
            FROM ai_audience_package_group g
            LEFT JOIN ai_audience_package p ON p.group_id = g.id
            GROUP BY g.id, g.name, g.created_at, g.updated_at
            ORDER BY LOWER(g.name) ASC, g.id ASC
            """
        )

    def count_package_summaries(self, *, group_id: int | None = None, ungrouped: bool = False) -> int:
        group_clause = ""
        params: dict[str, Any] = {}
        if ungrouped:
            group_clause = "AND group_id IS NULL"
        elif group_id is not None:
            group_clause = "AND group_id = :group_id"
            params["group_id"] = int(group_id)
        row = self._one(
            f"""
            SELECT COUNT(*) AS package_count
            FROM ai_audience_package
            WHERE status <> 'archived'
              {group_clause}
            """,
            params,
        )
        return int((row or {}).get("package_count") or 0)

    def count_ungrouped_packages(self) -> int:
        return self.count_package_summaries(ungrouped=True)

    def get_package_group(self, group_id: int) -> dict[str, Any] | None:
        return self._one(
            "SELECT id, name, created_at, updated_at FROM ai_audience_package_group WHERE id = :group_id LIMIT 1",
            {"group_id": int(group_id)},
        )

    @staticmethod
    def _append_group_audit(
        session,
        *,
        operator: str,
        action_type: str,
        group_id: int,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        build_admin_audit_port().append_sqlalchemy(
            session,
            dialect_name=session.get_bind().dialect.name,
            record=AdminAuditRecord(
                operator=_text(operator) or "admin",
                action_type=action_type,
                target_type="ai_audience_package_group",
                target_id=str(int(group_id)),
                before=before or {},
                after=after or {},
            ),
        )

    def create_package_group(self, name: str, *, operator: str = "admin") -> dict[str, Any]:
        try:
            with self._session_factory() as session:
                row = session.execute(
                    text(
                        """
                        INSERT INTO ai_audience_package_group (name, created_at, updated_at)
                        VALUES (:name, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        RETURNING id, name, created_at, updated_at
                        """
                    ),
                    {"name": _text(name)},
                ).mappings().one()
                payload = _public_row(dict(row)) or {}
                self._append_group_audit(
                    session,
                    operator=operator,
                    action_type="ai_audience_group_created",
                    group_id=int(row["id"]),
                    before={},
                    after=payload,
                )
                session.commit()
                return payload
        except IntegrityError as exc:
            raise AudienceGroupNameConflictError() from exc

    def update_package_group(self, group_id: int, name: str, *, operator: str = "admin") -> dict[str, Any] | None:
        try:
            with self._session_factory() as session:
                before_row = session.execute(
                    text("SELECT id, name, created_at, updated_at FROM ai_audience_package_group WHERE id = :group_id FOR UPDATE"),
                    {"group_id": int(group_id)},
                ).mappings().fetchone()
                if not before_row:
                    session.rollback()
                    return None
                row = session.execute(
                    text(
                        """
                        UPDATE ai_audience_package_group
                        SET name = :name, updated_at = CURRENT_TIMESTAMP
                        WHERE id = :group_id
                        RETURNING id, name, created_at, updated_at
                        """
                    ),
                    {"group_id": int(group_id), "name": _text(name)},
                ).mappings().one()
                before = _public_row(dict(before_row)) or {}
                after = _public_row(dict(row)) or {}
                self._append_group_audit(
                    session,
                    operator=operator,
                    action_type="ai_audience_group_renamed",
                    group_id=int(group_id),
                    before=before,
                    after=after,
                )
                session.commit()
                return after
        except IntegrityError as exc:
            raise AudienceGroupNameConflictError() from exc

    def delete_package_group(self, group_id: int, *, operator: str = "admin") -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.execute(
                text("SELECT id, name, created_at, updated_at FROM ai_audience_package_group WHERE id = :group_id FOR UPDATE"),
                {"group_id": int(group_id)},
            ).mappings().fetchone()
            if not row:
                session.rollback()
                return None
            package_count = int(
                session.execute(
                    text("SELECT COUNT(*) FROM ai_audience_package WHERE group_id = :group_id"),
                    {"group_id": int(group_id)},
                ).scalar_one()
                or 0
            )
            if package_count > 0:
                session.rollback()
                raise AudienceGroupNotEmptyError()
            before = _public_row(dict(row)) or {}
            session.execute(
                text("DELETE FROM ai_audience_package_group WHERE id = :group_id"),
                {"group_id": int(group_id)},
            )
            self._append_group_audit(
                session,
                operator=operator,
                action_type="ai_audience_group_deleted",
                group_id=int(group_id),
                before=before,
                after={},
            )
            session.commit()
            return before

    def get_package_detail(self, package_id: int) -> dict[str, Any] | None:
        return self._one(
            """
            WITH member_counts AS (
                SELECT package_id, COUNT(*) FILTER (WHERE status = 'active') AS member_count
                FROM ai_audience_member_current
                WHERE package_id = :package_id
                GROUP BY package_id
            ),
            latest_runs AS (
                SELECT DISTINCT ON (package_id)
                    package_id,
                    refresh_finished_at,
                    refresh_started_at
                FROM ai_audience_package_run
                WHERE package_id = :package_id
                ORDER BY package_id, refresh_finished_at DESC NULLS LAST, id DESC
            )
            SELECT
                p.id,
                p.package_key,
                p.name,
                p.status,
                COALESCE(mc.member_count, 0) AS member_count,
                lr.refresh_finished_at AS last_refreshed_at,
                p.incremental_enabled,
                p.incremental_interval_seconds,
                p.daily_enabled,
                p.daily_refresh_time,
                p.natural_language_definition,
                p.timezone,
                p.group_id,
                g.name AS group_name,
                v.id AS current_version_id,
                v.version_number AS current_version_number,
                v.template_key,
                v.template_version,
                v.template_params_json,
                v.template_fingerprint
            FROM ai_audience_package p
            LEFT JOIN ai_audience_package_group g ON g.id = p.group_id
            LEFT JOIN ai_audience_package_version v ON v.id = p.current_version_id
            LEFT JOIN member_counts mc ON mc.package_id = p.id
            LEFT JOIN latest_runs lr ON lr.package_id = p.id
            WHERE p.id = :package_id
            LIMIT 1
            """,
            {"package_id": int(package_id)},
        )

    def update_package_config(self, package_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._write_one(
            """
            UPDATE ai_audience_package
            SET name = :name,
                natural_language_definition = :natural_language_definition,
                incremental_enabled = :incremental_enabled,
                incremental_interval_seconds = :incremental_interval_seconds,
                daily_enabled = :daily_enabled,
                daily_refresh_time = :daily_refresh_time,
                group_id = :group_id,
                next_incremental_refresh_at = CASE
                    WHEN :incremental_enabled THEN COALESCE(next_incremental_refresh_at, CURRENT_TIMESTAMP)
                    ELSE NULL
                END,
                next_daily_refresh_at = CASE
                    WHEN :daily_enabled THEN COALESCE(next_daily_refresh_at, :next_daily_refresh_at)
                    ELSE NULL
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :package_id
            RETURNING *
            """,
            {
                "package_id": int(package_id),
                "name": _text(payload.get("name")),
                "natural_language_definition": _text(payload.get("natural_language_definition")),
                "incremental_enabled": bool(payload.get("incremental_enabled")),
                "incremental_interval_seconds": int(payload.get("incremental_interval_seconds") or 180),
                "daily_enabled": bool(payload.get("daily_enabled")),
                "daily_refresh_time": _text(payload.get("daily_refresh_time")) or "02:00",
                "group_id": int(payload.get("group_id")) if payload.get("group_id") is not None else None,
                "next_daily_refresh_at": next_daily_refresh_at(
                    _text(payload.get("daily_refresh_time")) or "02:00",
                    _text(payload.get("timezone")) or "Asia/Shanghai",
                ),
            },
        )

    def copy_package(self, package_id: int, *, package_key: str, name: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            source = (
                session.execute(text("SELECT * FROM ai_audience_package WHERE id = :package_id LIMIT 1"), {"package_id": int(package_id)}).mappings().fetchone()
            )
            if not source:
                return None
            row = (
                session.execute(
                    text(
                        """
                    INSERT INTO ai_audience_package (
                        package_key, name, natural_language_definition, status, query_mode, identity_policy,
                        incremental_enabled, daily_enabled, incremental_interval_seconds, daily_refresh_time,
                        timezone, lookback_seconds, group_id,
                        next_incremental_refresh_at, next_daily_refresh_at, created_at, updated_at
                    )
                    VALUES (
                        :package_key, :name, :natural_language_definition, 'draft', :query_mode, :identity_policy,
                        :incremental_enabled, :daily_enabled, :incremental_interval_seconds, :daily_refresh_time,
                        :timezone, :lookback_seconds, :group_id,
                        NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    RETURNING *
                    """
                    ),
                    {
                        "package_key": _text(package_key),
                        "name": _text(name),
                        "natural_language_definition": _text(source.get("natural_language_definition")),
                        "query_mode": _text(source.get("query_mode")) or "hybrid",
                        "identity_policy": _text(source.get("identity_policy")) or "external_userid",
                        "incremental_enabled": bool(source.get("incremental_enabled")),
                        "daily_enabled": bool(source.get("daily_enabled")),
                        "incremental_interval_seconds": int(source.get("incremental_interval_seconds") or 180),
                        "daily_refresh_time": _text(source.get("daily_refresh_time")) or "02:00",
                        "timezone": _text(source.get("timezone")) or "Asia/Shanghai",
                        "lookback_seconds": int(source.get("lookback_seconds") or 600),
                        "group_id": int(source.get("group_id")) if source.get("group_id") is not None else None,
                    },
                )
                .mappings()
                .one()
            )
            new_package_id = int(row["id"])
            version = session.execute(
                text(
                    """
                    SELECT *
                    FROM ai_audience_package_version
                    WHERE id = :version_id
                    LIMIT 1
                    """
                ),
                {"version_id": int(source.get("current_version_id") or 0)},
            ).mappings().fetchone()
            if version:
                new_version = session.execute(
                    text(
                        """
                        INSERT INTO ai_audience_package_version (
                            package_id, version_number, status, incremental_sql_text, snapshot_sql_text,
                            simple_sql_text, simple_compiled_sql_text,
                            ai_prompt, ai_rationale, natural_language_explanation, parameters_json, dependencies_json,
                            explain_json, sample_rows_json, validation_errors_json,
                            template_key, template_version, template_params_json, template_fingerprint,
                            created_at
                        )
                        VALUES (
                            :package_id, 1, 'draft', :incremental_sql_text, :snapshot_sql_text,
                            :simple_sql_text, :simple_compiled_sql_text,
                            :ai_prompt, :ai_rationale, :natural_language_explanation, CAST(:parameters_json AS jsonb), CAST(:dependencies_json AS jsonb),
                            CAST(:explain_json AS jsonb), CAST(:sample_rows_json AS jsonb), CAST(:validation_errors_json AS jsonb),
                            :template_key, :template_version, CAST(:template_params_json AS jsonb), :template_fingerprint,
                            CURRENT_TIMESTAMP
                        )
                        RETURNING *
                        """
                    ),
                    {
                        "package_id": new_package_id,
                        "incremental_sql_text": _text(version.get("incremental_sql_text")),
                        "snapshot_sql_text": _text(version.get("snapshot_sql_text")),
                        "simple_sql_text": _text(version.get("simple_sql_text")),
                        "simple_compiled_sql_text": _text(version.get("simple_compiled_sql_text")),
                        "ai_prompt": _text(version.get("ai_prompt")),
                        "ai_rationale": _text(version.get("ai_rationale")),
                        "natural_language_explanation": _text(version.get("natural_language_explanation")),
                        "parameters_json": _json_dumps(version.get("parameters_json") or {}),
                        "dependencies_json": _json_dumps(version.get("dependencies_json") or []),
                        "explain_json": _json_dumps(version.get("explain_json") or {}),
                        "sample_rows_json": _json_dumps(version.get("sample_rows_json") or []),
                        "validation_errors_json": _json_dumps(version.get("validation_errors_json") or []),
                        "template_key": _text(version.get("template_key")),
                        "template_version": int(version.get("template_version")) if version.get("template_version") is not None else None,
                        "template_params_json": _json_dumps(version.get("template_params_json") or {}),
                        "template_fingerprint": _text(version.get("template_fingerprint")),
                    },
                ).mappings().one()
                session.execute(
                    text("UPDATE ai_audience_package SET current_version_id = :version_id WHERE id = :package_id"),
                    {"version_id": int(new_version["id"]), "package_id": new_package_id},
                )
                session.execute(
                    text(
                        """
                        INSERT INTO ai_audience_package_dependency (
                            package_id, version_id, source_type, source_key, view_name, created_at
                        )
                        SELECT
                            :new_package_id, :new_version_id, source_type, source_key, view_name, CURRENT_TIMESTAMP
                        FROM ai_audience_package_dependency
                        WHERE package_id = :package_id
                          AND version_id = :source_version_id
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {
                        "new_package_id": new_package_id,
                        "new_version_id": int(new_version["id"]),
                        "package_id": int(package_id),
                        "source_version_id": int(version["id"]),
                    },
                )
            if self._table_exists("ai_audience_package_sender"):
                session.execute(
                    text(
                        """
                        INSERT INTO ai_audience_package_sender (
                            package_id, sender_userid, display_name, priority, status, created_at, updated_at
                        )
                        SELECT
                            :new_package_id, sender_userid, display_name, priority, status,
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        FROM ai_audience_package_sender
                        WHERE package_id = :package_id
                        """
                    ),
                    {"new_package_id": new_package_id, "package_id": int(package_id)},
                )
            session.commit()
            copied = session.execute(text("SELECT * FROM ai_audience_package WHERE id = :package_id"), {"package_id": new_package_id}).mappings().one()
            return _public_row(dict(copied))

    def activate_package(self, package_id: int) -> dict[str, Any] | None:
        current = self.get_package(package_id)
        if not current:
            return None
        next_daily = None
        if bool(current.get("daily_enabled")):
            next_daily = next_daily_refresh_at(_text(current.get("daily_refresh_time")) or "02:00", _text(current.get("timezone")) or "Asia/Shanghai")
        return self._write_one(
            """
            UPDATE ai_audience_package
            SET status = 'active',
                next_incremental_refresh_at = CASE WHEN incremental_enabled THEN CURRENT_TIMESTAMP ELSE NULL END,
                next_daily_refresh_at = CASE
                    WHEN daily_enabled THEN CAST(:next_daily_refresh_at AS TIMESTAMPTZ)
                    ELSE NULL
                END,
                paused_reason = '',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :package_id
            RETURNING *
            """,
            {"package_id": int(package_id), "next_daily_refresh_at": next_daily},
        )

    def create_package(self, payload: dict[str, Any]) -> dict[str, Any]:
        daily_enabled = bool(payload.get("daily_enabled", False))
        daily_refresh_time = _text(payload.get("daily_refresh_time")) or "02:00"
        timezone_name = _text(payload.get("timezone")) or "Asia/Shanghai"
        status = _text(payload.get("status")) or "draft"
        if status not in {"draft", "paused", "active"}:
            status = "draft"
        row = self._write_one(
            """
            INSERT INTO ai_audience_package (
                package_key, name, natural_language_definition, status, query_mode, identity_policy,
                incremental_enabled, daily_enabled, incremental_interval_seconds, daily_refresh_time,
                timezone, lookback_seconds, group_id,
                next_incremental_refresh_at, next_daily_refresh_at, created_at, updated_at
            )
            VALUES (
                :package_key, :name, :natural_language_definition, :status, :query_mode, :identity_policy,
                :incremental_enabled, :daily_enabled, :incremental_interval_seconds, :daily_refresh_time,
                :timezone, :lookback_seconds, :group_id,
                :next_incremental_refresh_at, :next_daily_refresh_at,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            RETURNING *
            """,
            {
                "package_key": _text(payload.get("package_key")),
                "name": _text(payload.get("name")),
                "status": status,
                "natural_language_definition": _text(payload.get("natural_language_definition")),
                "query_mode": _text(payload.get("query_mode")) or "hybrid",
                "identity_policy": _text(payload.get("identity_policy")) or "external_userid",
                "incremental_enabled": bool(payload.get("incremental_enabled", True)),
                "daily_enabled": daily_enabled,
                "incremental_interval_seconds": max(60, int(payload.get("incremental_interval_seconds") or 180)),
                "daily_refresh_time": daily_refresh_time,
                "timezone": timezone_name,
                "lookback_seconds": max(0, int(payload.get("lookback_seconds") or 600)),
                "group_id": int(payload.get("group_id")) if payload.get("group_id") is not None else None,
                "next_incremental_refresh_at": default_refresh_started_at() if status == "active" and bool(payload.get("incremental_enabled", True)) else None,
                "next_daily_refresh_at": next_daily_refresh_at(daily_refresh_time, timezone_name) if status == "active" and daily_enabled else None,
            },
        )
        if row is None:
            raise RuntimeError("ai audience package create failed")
        return row

    def get_package(self, package_id: int) -> dict[str, Any] | None:
        return self._one("SELECT * FROM ai_audience_package WHERE id = :id LIMIT 1", {"id": int(package_id)})

    def get_package_by_key(self, package_key: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM ai_audience_package WHERE package_key = :package_key LIMIT 1", {"package_key": _text(package_key)})

    def resolve_template_reference(
        self,
        reference_type: str,
        value: Any,
        *,
        parent_id: int | None = None,
    ) -> list[dict[str, Any]]:
        raw = _text(value)
        numeric_id = int(raw) if raw.isdigit() else -1
        queries: dict[str, tuple[str, dict[str, Any]]] = {
            "questionnaire": (
                """
                SELECT id, slug AS code, COALESCE(NULLIF(title, ''), name) AS title
                FROM questionnaires
                WHERE is_disabled = FALSE
                  AND (id = :numeric_id OR slug = :raw OR title = :raw OR name = :raw)
                ORDER BY id ASC
                LIMIT 11
                """,
                {"numeric_id": numeric_id, "raw": raw},
            ),
            "question": (
                """
                SELECT id, ''::text AS code, title, type
                FROM questionnaire_questions
                WHERE questionnaire_id = :parent_id
                  AND (id = :numeric_id OR title = :raw)
                ORDER BY sort_order ASC, id ASC
                LIMIT 11
                """,
                {"parent_id": int(parent_id or 0), "numeric_id": numeric_id, "raw": raw},
            ),
            "option": (
                """
                SELECT id, ''::text AS code, option_text AS title
                FROM questionnaire_options
                WHERE question_id = :parent_id
                  AND (id = :numeric_id OR option_text = :raw)
                ORDER BY sort_order ASC, id ASC
                LIMIT 11
                """,
                {"parent_id": int(parent_id or 0), "numeric_id": numeric_id, "raw": raw},
            ),
            "product": (
                """
                SELECT id, product_code AS code, name AS title
                FROM wechat_pay_products
                WHERE product_code = :raw OR name = :raw OR id = :numeric_id
                ORDER BY id ASC
                LIMIT 11
                """,
                {"numeric_id": numeric_id, "raw": raw},
            ),
            "channel": (
                """
                SELECT DISTINCT channel_id AS id, channel_code AS code, channel_name AS title
                FROM audience_read.channel_entries_v1
                WHERE channel_code = :raw OR channel_name = :raw OR channel_id = :numeric_id
                ORDER BY channel_id ASC
                LIMIT 11
                """,
                {"numeric_id": numeric_id, "raw": raw},
            ),
            "radar": (
                """
                SELECT id, code, title
                FROM radar_links
                WHERE deleted_at IS NULL
                  AND (code = :raw OR title = :raw OR id = :numeric_id)
                ORDER BY id ASC
                LIMIT 11
                """,
                {"numeric_id": numeric_id, "raw": raw},
            ),
            "group": (
                """
                SELECT id, ''::text AS code, name AS title
                FROM ai_audience_package_group
                WHERE name = :raw
                ORDER BY id ASC
                LIMIT 11
                """,
                {"raw": raw},
            ),
            "automation": (
                """
                SELECT id, agent_code AS code, agent_name AS title, status, bound_package_key
                FROM automation_agent_runtime_config
                WHERE agent_code = :raw
                  AND status <> 'archived'
                ORDER BY id ASC
                LIMIT 11
                """,
                {"raw": raw},
            ),
        }
        query = queries.get(_text(reference_type))
        if not query:
            return []
        statement, params = query
        return self._all(statement, params)

    def apply_template_package(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply a fully resolved template request in one database transaction."""

        package_key = _text(payload.get("package_key"))
        fingerprint = _text(payload.get("template_fingerprint"))
        refresh = dict(payload.get("refresh_config") or {})
        senders = list(payload.get("senders") or [])
        dependencies = list(payload.get("dependencies") or [])
        compiled_sql = _text(payload.get("compiled_sql"))
        refresh_mode = _text(payload.get("refresh_mode"))
        incremental_sql = compiled_sql if refresh_mode in {"every_3m", "every_3m_plus_daily_0200"} else ""
        snapshot_sql = compiled_sql if refresh_mode in {"manual", "daily_0200", "every_3m_plus_daily_0200"} else ""
        with self._session_factory() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:package_key))"), {"package_key": package_key})
            package_row = session.execute(
                text("SELECT * FROM ai_audience_package WHERE package_key = :package_key LIMIT 1 FOR UPDATE"),
                {"package_key": package_key},
            ).mappings().fetchone()
            package = dict(package_row) if package_row else None
            current_version = None
            if package and package.get("current_version_id"):
                row = session.execute(
                    text("SELECT * FROM ai_audience_package_version WHERE id = :version_id LIMIT 1 FOR UPDATE"),
                    {"version_id": int(package["current_version_id"])},
                ).mappings().fetchone()
                current_version = dict(row) if row else None
            if package and _text(package.get("status")) == "archived":
                raise ArchivedPackageTemplateUpdateError()
            if package and _text(package.get("status")) == "active":
                if _text((current_version or {}).get("template_fingerprint")) != fingerprint:
                    raise ActivePackageTemplateUpdateError()
                session.rollback()
                return {
                    "package": _public_row(package) or {},
                    "version": _public_row(current_version) or {},
                    "created": False,
                    "updated": False,
                    "idempotent": True,
                }

            group_id = int(payload["group_id"]) if payload.get("group_id") is not None else None
            package_values = {
                "package_key": package_key,
                "name": _text(payload.get("name")),
                "natural_language_definition": _text(payload.get("natural_language_definition")),
                "incremental_enabled": bool(refresh.get("incremental_enabled")),
                "incremental_interval_seconds": int(refresh.get("incremental_interval_seconds") or 180),
                "daily_enabled": bool(refresh.get("daily_enabled")),
                "daily_refresh_time": _text(refresh.get("daily_refresh_time")) or "02:00",
                "group_id": group_id,
            }
            created = package is None
            if created:
                package_row = session.execute(
                    text(
                        """
                        INSERT INTO ai_audience_package (
                            package_key, name, natural_language_definition, status, query_mode,
                            identity_policy, incremental_enabled, incremental_interval_seconds,
                            daily_enabled, daily_refresh_time, timezone, lookback_seconds,
                            group_id, next_incremental_refresh_at, next_daily_refresh_at,
                            created_at, updated_at
                        )
                        VALUES (
                            :package_key, :name, :natural_language_definition, 'paused', 'template',
                            'external_userid', :incremental_enabled, :incremental_interval_seconds,
                            :daily_enabled, :daily_refresh_time, 'Asia/Shanghai', 600,
                            :group_id, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        RETURNING *
                        """
                    ),
                    package_values,
                ).mappings().one()
            else:
                package_row = session.execute(
                    text(
                        """
                        UPDATE ai_audience_package
                        SET name = :name,
                            natural_language_definition = :natural_language_definition,
                            status = 'paused',
                            query_mode = 'template',
                            incremental_enabled = :incremental_enabled,
                            incremental_interval_seconds = :incremental_interval_seconds,
                            daily_enabled = :daily_enabled,
                            daily_refresh_time = :daily_refresh_time,
                            group_id = :group_id,
                            next_incremental_refresh_at = NULL,
                            next_daily_refresh_at = NULL,
                            paused_reason = 'template_apply',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :package_id
                        RETURNING *
                        """
                    ),
                    {**package_values, "package_id": int(package["id"])},
                ).mappings().one()
            package = dict(package_row)
            package_id = int(package["id"])

            version_row = session.execute(
                text(
                    """
                    SELECT *
                    FROM ai_audience_package_version
                    WHERE package_id = :package_id
                      AND template_fingerprint = :template_fingerprint
                    ORDER BY version_number DESC, id DESC
                    LIMIT 1
                    FOR UPDATE
                    """
                ),
                {"package_id": package_id, "template_fingerprint": fingerprint},
            ).mappings().fetchone()
            reused_version = bool(version_row)
            if version_row:
                version = dict(version_row)
                version_id = int(version["id"])
            else:
                version_row = session.execute(
                    text(
                        """
                        INSERT INTO ai_audience_package_version (
                            package_id, version_number, status,
                            incremental_sql_text, snapshot_sql_text,
                            simple_sql_text, simple_compiled_sql_text,
                            parameters_json, dependencies_json,
                            natural_language_explanation, validation_errors_json,
                            template_key, template_version, template_params_json,
                            template_fingerprint, created_at, published_at
                        )
                        SELECT
                            :package_id, COALESCE(MAX(version_number), 0) + 1, 'published',
                            :incremental_sql_text, :snapshot_sql_text,
                            '', :simple_compiled_sql_text,
                            CAST(:parameters_json AS jsonb), CAST(:dependencies_json AS jsonb),
                            :natural_language_explanation, '[]'::jsonb,
                            :template_key, :template_version, CAST(:template_params_json AS jsonb),
                            :template_fingerprint, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        FROM ai_audience_package_version
                        WHERE package_id = :package_id
                        RETURNING *
                        """
                    ),
                    {
                        "package_id": package_id,
                        "incremental_sql_text": incremental_sql,
                        "snapshot_sql_text": snapshot_sql,
                        "simple_compiled_sql_text": compiled_sql,
                        "parameters_json": _json_dumps(payload.get("execution_parameters") or {}),
                        "dependencies_json": _json_dumps(dependencies),
                        "natural_language_explanation": _text(payload.get("natural_language_definition")),
                        "template_key": _text(payload.get("template_key")),
                        "template_version": int(payload.get("template_version") or 1),
                        "template_params_json": _json_dumps(payload.get("template_parameters") or {}),
                        "template_fingerprint": fingerprint,
                    },
                ).mappings().one()
                version = dict(version_row)
                version_id = int(version["id"])

            session.execute(
                text("UPDATE ai_audience_package_version SET status = 'archived' WHERE package_id = :package_id AND id <> :version_id"),
                {"package_id": package_id, "version_id": version_id},
            )
            session.execute(
                text(
                    """
                    UPDATE ai_audience_package_version
                    SET status = 'published', published_at = COALESCE(published_at, CURRENT_TIMESTAMP)
                    WHERE id = :version_id
                    """
                ),
                {"version_id": version_id},
            )
            package_row = session.execute(
                text(
                    """
                    UPDATE ai_audience_package
                    SET current_version_id = :version_id,
                        status = 'paused',
                        next_incremental_refresh_at = NULL,
                        next_daily_refresh_at = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :package_id
                    RETURNING *
                    """
                ),
                {"package_id": package_id, "version_id": version_id},
            ).mappings().one()
            package = dict(package_row)

            session.execute(
                text("DELETE FROM ai_audience_package_dependency WHERE package_id = :package_id AND version_id = :version_id"),
                {"package_id": package_id, "version_id": version_id},
            )
            for dependency in dependencies:
                session.execute(
                    text(
                        """
                        INSERT INTO ai_audience_package_dependency (
                            package_id, version_id, source_type, source_key, view_name, created_at
                        )
                        VALUES (:package_id, :version_id, :source_type, '', :view_name, CURRENT_TIMESTAMP)
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {
                        "package_id": package_id,
                        "version_id": version_id,
                        "source_type": _dependency_source_type(dependency),
                        "view_name": dependency,
                    },
                )

            session.execute(text("DELETE FROM ai_audience_package_sender WHERE package_id = :package_id"), {"package_id": package_id})
            for sender in senders:
                session.execute(
                    text(
                        """
                        INSERT INTO ai_audience_package_sender (
                            package_id, sender_userid, display_name, priority, status, created_at, updated_at
                        )
                        VALUES (
                            :package_id, :sender_userid, :display_name, :priority, :status,
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        """
                    ),
                    {
                        "package_id": package_id,
                        "sender_userid": _text(sender.get("sender_userid")),
                        "display_name": _text(sender.get("display_name")) or _text(sender.get("sender_userid")),
                        "priority": int(sender.get("priority") or 100),
                        "status": _text(sender.get("status")) or "active",
                    },
                )

            agent_code = _text(payload.get("automation_agent_code"))
            if agent_code:
                automation_row = session.execute(
                    text(
                        """
                        SELECT id, agent_code, agent_name, automation_type, bound_package_key, status, updated_at
                        FROM automation_agent_runtime_config
                        WHERE agent_code = :agent_code AND status <> 'archived'
                        LIMIT 1 FOR UPDATE
                        """
                    ),
                    {"agent_code": agent_code},
                ).mappings().fetchone()
                if not automation_row:
                    raise AutomationNotFoundError()
                automation = dict(automation_row)
                bound_key = _text(automation.get("bound_package_key"))
                if bound_key and bound_key != package_key:
                    raise AutomationAlreadyBoundError()
                current = AudienceAutomationBindingRepository._bound_automations_in_session(session, package_key, lock=True)
                if len(current) > 1:
                    raise BindingStateInvalidError()
                same_binding = bool(current and int(current[0]["id"]) == int(automation["id"]))
                if _text(automation.get("status")) != "active" and not same_binding:
                    raise AutomationNotActiveError()
                if current and not same_binding:
                    session.execute(
                        text("UPDATE automation_agent_runtime_config SET bound_package_key = '', updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
                        {"id": int(current[0]["id"])},
                    )
                session.execute(
                    text("UPDATE automation_agent_runtime_config SET bound_package_key = :package_key, updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
                    {"package_key": package_key, "id": int(automation["id"])},
                )
                automation["bound_package_key"] = package_key
                AudienceAutomationBindingRepository._sync_subscription(session, package_id=package_id, automation=automation)

            build_admin_audit_port().append_sqlalchemy(
                session,
                dialect_name=session.get_bind().dialect.name,
                record=AdminAuditRecord(
                    operator=_text(payload.get("operator")) or "external",
                    action_type="ai_audience_template_applied",
                    target_type="ai_audience_package",
                    target_id=str(package_id),
                    before={"template_fingerprint": _text((current_version or {}).get("template_fingerprint"))},
                    after={
                        "package_key": package_key,
                        "template_key": _text(payload.get("template_key")),
                        "template_version": int(payload.get("template_version") or 1),
                        "template_fingerprint": fingerprint,
                        "status": "paused",
                    },
                ),
            )
            session.commit()
            return {
                "package": _public_row(package) or {},
                "version": _public_row(version) or {},
                "created": created,
                "updated": not created,
                "idempotent": reused_version,
            }

    def create_version(self, package_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        row = self._write_one(
            """
            INSERT INTO ai_audience_package_version (
                package_id, version_number, status, incremental_sql_text, snapshot_sql_text,
                simple_sql_text, simple_compiled_sql_text,
                ai_prompt, ai_rationale, natural_language_explanation, parameters_json, dependencies_json,
                explain_json, sample_rows_json, validation_errors_json, created_at
            )
            SELECT
                :package_id,
                COALESCE(MAX(version_number), 0) + 1,
                'draft',
                :incremental_sql_text,
                :snapshot_sql_text,
                :simple_sql_text,
                :simple_compiled_sql_text,
                :ai_prompt,
                :ai_rationale,
                :natural_language_explanation,
                CAST(:parameters_json AS jsonb),
                CAST(:dependencies_json AS jsonb),
                CAST(:explain_json AS jsonb),
                CAST(:sample_rows_json AS jsonb),
                CAST(:validation_errors_json AS jsonb),
                CURRENT_TIMESTAMP
            FROM ai_audience_package_version
            WHERE package_id = :package_id
            RETURNING *
            """,
            {
                "package_id": int(package_id),
                "incremental_sql_text": _text(payload.get("incremental_sql_text")),
                "snapshot_sql_text": _text(payload.get("snapshot_sql_text")),
                "simple_sql_text": _text(payload.get("simple_sql_text")),
                "simple_compiled_sql_text": _text(payload.get("simple_compiled_sql_text")),
                "ai_prompt": _text(payload.get("ai_prompt")),
                "ai_rationale": _text(payload.get("ai_rationale")),
                "natural_language_explanation": _text(payload.get("natural_language_explanation")),
                "parameters_json": _json_dumps(payload.get("parameters") or payload.get("parameters_json") or {}),
                "dependencies_json": _json_dumps(payload.get("dependencies") or []),
                "explain_json": _json_dumps(payload.get("explain") or {}),
                "sample_rows_json": _json_dumps(payload.get("sample_rows") or []),
                "validation_errors_json": _json_dumps(payload.get("validation_errors") or []),
            },
        )
        if row is None:
            raise RuntimeError("ai audience package version create failed")
        return row

    def update_version_validation(self, version_id: int, *, dependencies: list[str], validation_errors: list[str], sample_rows: list[dict[str, Any]] | None = None, explain: Any | None = None) -> dict[str, Any] | None:
        return self._write_one(
            """
            UPDATE ai_audience_package_version
            SET dependencies_json = CAST(:dependencies_json AS jsonb),
                validation_errors_json = CAST(:validation_errors_json AS jsonb),
                sample_rows_json = COALESCE(CAST(:sample_rows_json AS jsonb), sample_rows_json),
                explain_json = COALESCE(CAST(:explain_json AS jsonb), explain_json)
            WHERE id = :version_id
            RETURNING *
            """,
            {
                "version_id": int(version_id),
                "dependencies_json": _json_dumps(dependencies),
                "validation_errors_json": _json_dumps(validation_errors),
                "sample_rows_json": _json_dumps(sample_rows) if sample_rows is not None else None,
                "explain_json": _json_dumps(explain) if explain is not None else None,
            },
        )

    def get_version(self, version_id: int) -> dict[str, Any] | None:
        return self._one("SELECT * FROM ai_audience_package_version WHERE id = :id LIMIT 1", {"id": int(version_id)})

    def get_current_version(self, package_id: int) -> dict[str, Any] | None:
        return self._one(
            """
            SELECT v.*
            FROM ai_audience_package p
            JOIN ai_audience_package_version v ON v.id = p.current_version_id
            WHERE p.id = :package_id
            LIMIT 1
            """,
            {"package_id": int(package_id)},
        )

    def get_latest_version(self, package_id: int) -> dict[str, Any] | None:
        return self._one(
            """
            SELECT *
            FROM ai_audience_package_version
            WHERE package_id = :package_id
            ORDER BY version_number DESC, id DESC
            LIMIT 1
            """,
            {"package_id": int(package_id)},
        )

    def publish_version(self, package_id: int, version_id: int) -> dict[str, Any] | None:
        with self._session_factory() as session:
            package_row = session.execute(
                text(
                    """
                    SELECT daily_enabled, daily_refresh_time, timezone
                    FROM ai_audience_package
                    WHERE id = :package_id
                    LIMIT 1
                    """
                ),
                {"package_id": int(package_id)},
            ).mappings().fetchone()
            next_daily = None
            if package_row and bool(package_row.get("daily_enabled")):
                next_daily = next_daily_refresh_at(
                    _text(package_row.get("daily_refresh_time")) or "02:00",
                    _text(package_row.get("timezone")) or "Asia/Shanghai",
                )
            session.execute(
                text("UPDATE ai_audience_package_version SET status = 'archived' WHERE package_id = :package_id AND id <> :version_id"),
                {"package_id": int(package_id), "version_id": int(version_id)},
            )
            row = session.execute(
                text(
                    """
                    UPDATE ai_audience_package_version
                    SET status = 'published', published_at = CURRENT_TIMESTAMP
                    WHERE id = :version_id AND package_id = :package_id
                    RETURNING *
                    """
                ),
                {"package_id": int(package_id), "version_id": int(version_id)},
            ).mappings().fetchone()
            if not row:
                session.rollback()
                return None
            session.execute(
                text(
                    """
                    UPDATE ai_audience_package
                    SET current_version_id = :version_id,
                        status = 'active',
                        next_incremental_refresh_at = COALESCE(next_incremental_refresh_at, CURRENT_TIMESTAMP),
                        next_daily_refresh_at = CASE WHEN daily_enabled THEN COALESCE(next_daily_refresh_at, :next_daily_refresh_at) ELSE next_daily_refresh_at END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :package_id
                    """
                ),
                {"package_id": int(package_id), "version_id": int(version_id), "next_daily_refresh_at": next_daily},
            )
            session.commit()
            return _public_row(dict(row))

    def publish_version_without_activation(self, package_id: int, version_id: int) -> dict[str, Any] | None:
        with self._session_factory() as session:
            existing_package = session.execute(
                text(
                    """
                    SELECT status, next_incremental_refresh_at, next_daily_refresh_at
                    FROM ai_audience_package
                    WHERE id = :package_id
                    LIMIT 1
                    """
                ),
                {"package_id": int(package_id)},
            ).mappings().fetchone()
            if not existing_package:
                return None
            session.execute(
                text("UPDATE ai_audience_package_version SET status = 'archived' WHERE package_id = :package_id AND id <> :version_id"),
                {"package_id": int(package_id), "version_id": int(version_id)},
            )
            row = session.execute(
                text(
                    """
                    UPDATE ai_audience_package_version
                    SET status = 'published', published_at = CURRENT_TIMESTAMP
                    WHERE id = :version_id AND package_id = :package_id
                    RETURNING *
                    """
                ),
                {"package_id": int(package_id), "version_id": int(version_id)},
            ).mappings().fetchone()
            if not row:
                session.rollback()
                return None
            session.execute(
                text(
                    """
                    UPDATE ai_audience_package
                    SET current_version_id = :version_id,
                        status = :status,
                        next_incremental_refresh_at = :next_incremental_refresh_at,
                        next_daily_refresh_at = :next_daily_refresh_at,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :package_id
                    """
                ),
                {
                    "package_id": int(package_id),
                    "version_id": int(version_id),
                    "status": _text(existing_package.get("status")) or "paused",
                    "next_incremental_refresh_at": existing_package.get("next_incremental_refresh_at"),
                    "next_daily_refresh_at": existing_package.get("next_daily_refresh_at"),
                },
            )
            session.commit()
            return _public_row(dict(row))

    def update_package_status(self, package_id: int, status: str, *, reason: str = "") -> dict[str, Any] | None:
        statement = """
            UPDATE ai_audience_package
            SET status = :status,
                paused_reason = :paused_reason,
                group_id = CASE WHEN :status = 'archived' THEN NULL ELSE group_id END,
                next_incremental_refresh_at = CASE
                    WHEN :status = 'active' AND incremental_enabled THEN COALESCE(next_incremental_refresh_at, CURRENT_TIMESTAMP)
                    WHEN :status = 'active' THEN NULL
                    ELSE NULL
                END,
                next_daily_refresh_at = CASE
                    WHEN :status = 'active' AND daily_enabled THEN COALESCE(next_daily_refresh_at, CURRENT_TIMESTAMP)
                    WHEN :status = 'active' THEN NULL
                    ELSE NULL
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :package_id
            RETURNING *
            """
        params = {"package_id": int(package_id), "status": _text(status), "paused_reason": _text(reason)}
        if params["status"] != "archived":
            return self._write_one(statement, params)
        with self._session_factory() as session:
            session.execute(
                text(
                    """
                    UPDATE ai_audience_outbound_subscription
                    SET status = 'archived', updated_at = CURRENT_TIMESTAMP
                    WHERE package_id = :package_id
                      AND status <> 'archived'
                    """
                ),
                {"package_id": int(package_id)},
            )
            row = session.execute(text(statement), params).mappings().fetchone()
            session.commit()
            return _public_row(dict(row)) if row else None

    def replace_dependencies(self, package_id: int, version_id: int, dependencies: list[str]) -> None:
        with self._session_factory() as session:
            session.execute(
                text("DELETE FROM ai_audience_package_dependency WHERE package_id = :package_id AND version_id = :version_id"),
                {"package_id": int(package_id), "version_id": int(version_id)},
            )
            for dependency in dependencies:
                source_type = _dependency_source_type(dependency)
                session.execute(
                    text(
                        """
                        INSERT INTO ai_audience_package_dependency (package_id, version_id, source_type, source_key, view_name, created_at)
                        VALUES (:package_id, :version_id, :source_type, '', :view_name, CURRENT_TIMESTAMP)
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {
                        "package_id": int(package_id),
                        "version_id": int(version_id),
                        "source_type": source_type,
                        "view_name": _text(dependency),
                    },
                )
            session.commit()
