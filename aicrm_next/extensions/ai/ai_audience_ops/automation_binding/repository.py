from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aicrm_next.platform.platform_foundation.admin_audit import (
    AdminAuditRecord,
    build_admin_audit_port,
)
from aicrm_next.platform.shared.automation_agent_webhook_contract import (
    automation_agent_code_from_webhook_url,
)
from aicrm_next.platform.shared.db_session import get_session_factory


def _text(value: Any) -> str:
    return str(value or "").strip()


def _agent_webhook_url(agent_code: str) -> str:
    return f"/api/ai/agents/{_text(agent_code)}/audience-webhook"


class BindingRepositoryError(RuntimeError):
    code = "automation_binding_failed"


class PackageNotFoundError(BindingRepositoryError):
    code = "package_not_found"


class AutomationNotFoundError(BindingRepositoryError):
    code = "automation_not_found"


class AutomationNotActiveError(BindingRepositoryError):
    code = "automation_not_active"


class AutomationAlreadyBoundError(BindingRepositoryError):
    code = "automation_already_bound"


class BindingStateInvalidError(BindingRepositoryError):
    code = "automation_binding_state_invalid"


def _row(value: Any) -> dict[str, Any]:
    return dict(value) if value else {}


class AudienceAutomationBindingRepository:
    def __init__(self, session_factory: Callable[[], Session] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    @staticmethod
    def _package_in_session(session: Session, package_id: int, *, lock: bool = False) -> dict[str, Any]:
        suffix = " FOR UPDATE" if lock else ""
        row = session.execute(
            text(
                f"""
                SELECT id, package_key, name, status, group_id
                FROM ai_audience_package
                WHERE id = :package_id
                  AND status <> 'archived'
                LIMIT 1{suffix}
                """
            ),
            {"package_id": int(package_id)},
        ).mappings().fetchone()
        return _row(row)

    @staticmethod
    def _automation_in_session(session: Session, automation_id: int, *, lock: bool = False) -> dict[str, Any]:
        suffix = " FOR UPDATE" if lock else ""
        row = session.execute(
            text(
                f"""
                SELECT id, agent_code, agent_name, automation_type, bound_package_key,
                       status, updated_at
                FROM automation_agent_runtime_config
                WHERE id = :automation_id
                  AND status <> 'archived'
                LIMIT 1{suffix}
                """
            ),
            {"automation_id": int(automation_id)},
        ).mappings().fetchone()
        return _row(row)

    @staticmethod
    def _bound_automations_in_session(session: Session, package_key: str, *, lock: bool = False) -> list[dict[str, Any]]:
        suffix = " FOR UPDATE" if lock else ""
        rows = session.execute(
            text(
                f"""
                SELECT id, agent_code, agent_name, automation_type, bound_package_key,
                       status, updated_at
                FROM automation_agent_runtime_config
                WHERE bound_package_key = :package_key
                  AND status <> 'archived'
                ORDER BY id ASC{suffix}
                """
            ),
            {"package_key": _text(package_key)},
        ).mappings().fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _internal_subscriptions_in_session(session: Session, package_id: int, *, lock: bool = False) -> list[dict[str, Any]]:
        suffix = " FOR UPDATE" if lock else ""
        rows = session.execute(
            text(
                f"""
                SELECT id, package_id, status, trigger_event_type, dispatch_mode,
                       target_type, webhook_url, execution_mode, requires_approval,
                       max_attempts
                FROM ai_audience_outbound_subscription
                WHERE package_id = :package_id
                ORDER BY id ASC{suffix}
                """
            ),
            {"package_id": int(package_id)},
        ).mappings().fetchall()
        return [dict(row) for row in rows if automation_agent_code_from_webhook_url(row.get("webhook_url"))]

    @staticmethod
    def _binding_payload(package: dict[str, Any], automation: dict[str, Any] | None) -> dict[str, Any] | None:
        if not automation:
            return None
        status = _text(automation.get("status")) or "paused"
        return {
            "automation_id": int(automation.get("id") or 0),
            "automation_name": _text(automation.get("agent_name")) or _text(automation.get("agent_code")),
            "agent_code": _text(automation.get("agent_code")),
            "automation_type": _text(automation.get("automation_type")) or "agent",
            "automation_type_label": "固定话术" if _text(automation.get("automation_type")) == "fixed_script" else "Agent 机器人",
            "status": status,
            "package_id": int(package.get("id") or 0),
            "package_key": _text(package.get("package_key")),
            "package_name": _text(package.get("name")),
            "warning": "automation_paused" if status != "active" else "",
        }

    @staticmethod
    def _audit(
        session: Session,
        *,
        operator: str,
        action_type: str,
        package: dict[str, Any],
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        build_admin_audit_port().append_sqlalchemy(
            session,
            dialect_name=session.get_bind().dialect.name,
            record=AdminAuditRecord(
                operator=_text(operator) or "admin",
                action_type=action_type,
                target_type="ai_audience_automation_binding",
                target_id=str(int(package.get("id") or 0)),
                before=before or {},
                after=after or {},
            ),
        )

    @staticmethod
    def _sync_subscription(
        session: Session,
        *,
        package_id: int,
        automation: dict[str, Any],
    ) -> None:
        agent_code = _text(automation.get("agent_code"))
        target_status = "active" if _text(automation.get("status")) == "active" else "paused"
        subscriptions = AudienceAutomationBindingRepository._internal_subscriptions_in_session(
            session,
            package_id,
            lock=True,
        )
        selected = [item for item in subscriptions if automation_agent_code_from_webhook_url(item.get("webhook_url")) == agent_code]
        for item in subscriptions:
            item_agent_code = automation_agent_code_from_webhook_url(item.get("webhook_url"))
            if item_agent_code == agent_code:
                continue
            session.execute(
                text(
                    """
                    UPDATE ai_audience_outbound_subscription
                    SET status = 'paused', updated_at = CURRENT_TIMESTAMP
                    WHERE id = :subscription_id
                    """
                ),
                {"subscription_id": int(item["id"])},
            )
        if len(selected) > 1:
            raise BindingStateInvalidError("duplicate internal subscriptions")
        if selected:
            session.execute(
                text(
                    """
                    UPDATE ai_audience_outbound_subscription
                    SET status = :status,
                        trigger_event_type = 'entered',
                        dispatch_mode = 'per_run',
                        target_type = 'webhook',
                        webhook_url = :webhook_url,
                        headers_json = '{}'::jsonb,
                        payload_template_json = '{}'::jsonb,
                        execution_mode = 'execute',
                        requires_approval = FALSE,
                        max_attempts = 5,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :subscription_id
                    """
                ),
                {
                    "subscription_id": int(selected[0]["id"]),
                    "status": target_status,
                    "webhook_url": _agent_webhook_url(agent_code),
                },
            )
            return
        session.execute(
            text(
                """
                INSERT INTO ai_audience_outbound_subscription (
                    package_id, status, trigger_event_type, dispatch_mode, target_type,
                    webhook_url, headers_json, payload_template_json, execution_mode,
                    requires_approval, max_attempts, created_at, updated_at
                )
                VALUES (
                    :package_id, :status, 'entered', 'per_run', 'webhook',
                    :webhook_url, '{}'::jsonb, '{}'::jsonb, 'execute',
                    FALSE, 5, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "package_id": int(package_id),
                "status": target_status,
                "webhook_url": _agent_webhook_url(agent_code),
            },
        )

    def get_binding(self, package_id: int) -> tuple[bool, dict[str, Any] | None]:
        with self._session_factory() as session:
            package = self._package_in_session(session, package_id)
            if not package:
                return False, None
            automations = self._bound_automations_in_session(session, _text(package.get("package_key")))
            if len(automations) > 1:
                raise BindingStateInvalidError("multiple automations bound to package")
            return True, self._binding_payload(package, automations[0] if automations else None)

    def get_binding_by_agent_code(self, package_id: int, agent_code: str) -> tuple[bool, dict[str, Any] | None]:
        with self._session_factory() as session:
            package = self._package_in_session(session, package_id)
            if not package:
                return False, None
            row = session.execute(
                text(
                    """
                    SELECT id, agent_code, agent_name, automation_type, bound_package_key,
                           status, updated_at
                    FROM automation_agent_runtime_config
                    WHERE agent_code = :agent_code
                      AND status <> 'archived'
                    LIMIT 1
                    """
                ),
                {"agent_code": _text(agent_code)},
            ).mappings().fetchone()
            return True, self._binding_payload(package, dict(row)) if row else None

    def package_has_binding(self, package_id: int) -> bool:
        exists, binding = self.get_binding(package_id)
        return bool(exists and binding)

    def automation_has_binding(self, automation_id: int) -> bool:
        with self._session_factory() as session:
            row = self._automation_in_session(session, automation_id)
            return bool(row and _text(row.get("bound_package_key")))

    def bind(self, package_id: int, automation_id: int, *, operator: str = "admin") -> tuple[dict[str, Any], bool]:
        try:
            with self._session_factory() as session:
                package = self._package_in_session(session, package_id, lock=True)
                if not package:
                    raise PackageNotFoundError()
                automation = self._automation_in_session(session, automation_id, lock=True)
                if not automation:
                    raise AutomationNotFoundError()
                package_key = _text(package.get("package_key"))
                current = self._bound_automations_in_session(session, package_key, lock=True)
                if len(current) > 1:
                    raise BindingStateInvalidError("multiple automations bound to package")
                current_automation = current[0] if current else None
                current_id = int((current_automation or {}).get("id") or 0)
                requested_id = int(automation.get("id") or 0)
                requested_bound_key = _text(automation.get("bound_package_key"))
                same_binding = current_id == requested_id and requested_bound_key == package_key
                if requested_bound_key and requested_bound_key != package_key:
                    raise AutomationAlreadyBoundError()
                if _text(automation.get("status")) != "active" and not same_binding:
                    raise AutomationNotActiveError()
                before = self._binding_payload(package, current_automation)
                if current_automation and current_id != requested_id:
                    session.execute(
                        text(
                            """
                            UPDATE automation_agent_runtime_config
                            SET bound_package_key = '', updated_at = CURRENT_TIMESTAMP
                            WHERE id = :automation_id
                            """
                        ),
                        {"automation_id": current_id},
                    )
                session.execute(
                    text(
                        """
                        UPDATE automation_agent_runtime_config
                        SET bound_package_key = :package_key, updated_at = CURRENT_TIMESTAMP
                        WHERE id = :automation_id
                        """
                    ),
                    {"automation_id": requested_id, "package_key": package_key},
                )
                automation["bound_package_key"] = package_key
                self._sync_subscription(session, package_id=int(package["id"]), automation=automation)
                after = self._binding_payload(package, automation)
                if not same_binding or before != after:
                    self._audit(
                        session,
                        operator=operator,
                        action_type="automation_binding_replaced" if before else "automation_binding_created",
                        package=package,
                        before=before,
                        after=after,
                    )
                session.commit()
                return after or {}, same_binding
        except IntegrityError as exc:
            raise AutomationAlreadyBoundError() from exc

    def bind_by_agent_code(self, package_id: int, agent_code: str, *, operator: str = "admin") -> tuple[dict[str, Any], bool]:
        with self._session_factory() as session:
            row = session.execute(
                text(
                    """
                    SELECT id
                    FROM automation_agent_runtime_config
                    WHERE agent_code = :agent_code
                      AND status <> 'archived'
                    LIMIT 1
                    """
                ),
                {"agent_code": _text(agent_code)},
            ).mappings().fetchone()
        if not row:
            raise AutomationNotFoundError()
        return self.bind(package_id, int(row["id"]), operator=operator)

    def unbind(self, package_id: int, *, operator: str = "admin") -> tuple[dict[str, Any] | None, bool]:
        with self._session_factory() as session:
            package = self._package_in_session(session, package_id, lock=True)
            if not package:
                raise PackageNotFoundError()
            current = self._bound_automations_in_session(session, _text(package.get("package_key")), lock=True)
            if len(current) > 1:
                raise BindingStateInvalidError("multiple automations bound to package")
            if not current:
                session.commit()
                return None, True
            automation = current[0]
            before = self._binding_payload(package, automation)
            session.execute(
                text(
                    """
                    UPDATE automation_agent_runtime_config
                    SET bound_package_key = '', updated_at = CURRENT_TIMESTAMP
                    WHERE id = :automation_id
                    """
                ),
                {"automation_id": int(automation["id"])},
            )
            for item in self._internal_subscriptions_in_session(session, int(package["id"]), lock=True):
                if automation_agent_code_from_webhook_url(item.get("webhook_url")) != _text(automation.get("agent_code")):
                    continue
                session.execute(
                    text(
                        """
                        UPDATE ai_audience_outbound_subscription
                        SET status = 'paused', updated_at = CURRENT_TIMESTAMP
                        WHERE id = :subscription_id
                        """
                    ),
                    {"subscription_id": int(item["id"])},
                )
            self._audit(
                session,
                operator=operator,
                action_type="automation_binding_removed",
                package=package,
                before=before,
                after={},
            )
            session.commit()
            return before, False

    def set_automation_status(self, automation_id: int, status: str, *, operator: str = "admin") -> dict[str, Any]:
        if status not in {"active", "paused"}:
            raise BindingStateInvalidError("unsupported status transition")
        with self._session_factory() as session:
            automation = self._automation_in_session(session, automation_id, lock=True)
            if not automation:
                raise AutomationNotFoundError()
            previous_status = _text(automation.get("status"))
            session.execute(
                text(
                    """
                    UPDATE automation_agent_runtime_config
                    SET status = :status, updated_at = CURRENT_TIMESTAMP
                    WHERE id = :automation_id
                    """
                ),
                {"automation_id": int(automation_id), "status": status},
            )
            automation["status"] = status
            package_key = _text(automation.get("bound_package_key"))
            if package_key:
                package_row = session.execute(
                    text(
                        """
                        SELECT id, package_key, name, status, group_id
                        FROM ai_audience_package
                        WHERE package_key = :package_key
                          AND status <> 'archived'
                        LIMIT 1
                        FOR UPDATE
                        """
                    ),
                    {"package_key": package_key},
                ).mappings().fetchone()
                if not package_row:
                    raise BindingStateInvalidError("bound package missing")
                package = dict(package_row)
                self._sync_subscription(session, package_id=int(package["id"]), automation=automation)
                self._audit(
                    session,
                    operator=operator,
                    action_type="automation_binding_status_synced",
                    package=package,
                    before={"automation_id": int(automation_id), "status": previous_status},
                    after={"automation_id": int(automation_id), "status": status},
                )
            session.commit()
            return automation


__all__ = [
    "AudienceAutomationBindingRepository",
    "AutomationAlreadyBoundError",
    "AutomationNotActiveError",
    "AutomationNotFoundError",
    "BindingRepositoryError",
    "BindingStateInvalidError",
    "PackageNotFoundError",
]
