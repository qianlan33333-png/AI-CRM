from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol

from aicrm_next.shared.db_session import get_engine
from aicrm_next.shared.errors import ContractError
from aicrm_next.shared.repository_provider import assert_repository_allowed
from aicrm_next.shared.runtime import production_data_ready, raw_database_url
from aicrm_next.shared.runtime_settings import startup_environment_setting

from .agent_outputs import agent_output_projection, normalize_agent_output_filters
from .agent_runs import agent_run_projection, normalize_agent_run_filters
from .agents import AGENT_ROUTE_FAMILY, agent_projection, agent_side_effect_safety, normalize_agent_create_payload, utc_now_iso

AGENT_BACKEND_ENV = "AICRM_AGENTS_REPO_BACKEND"
AGENT_TEST_DATABASE_URL_ENV = "AICRM_AGENTS_TEST_DATABASE_URL"
AGENT_STAGING_DATABASE_URL_ENV = "AICRM_AGENTS_STAGING_DATABASE_URL"
AGENT_SQL_BACKENDS = {"sql", "sqlalchemy", "postgres", "postgresql"}
AGENT_OUTPUT_BACKEND_ENV = "AICRM_AGENT_OUTPUTS_REPO_BACKEND"
AGENT_OUTPUT_TEST_DATABASE_URL_ENV = "AICRM_AGENT_OUTPUTS_TEST_DATABASE_URL"
AGENT_OUTPUT_STAGING_DATABASE_URL_ENV = "AICRM_AGENT_OUTPUTS_STAGING_DATABASE_URL"
AGENT_OUTPUT_SQL_BACKENDS = {"sql", "sqlalchemy", "postgres", "postgresql"}
AGENT_RUN_BACKEND_ENV = "AICRM_AGENT_RUNS_REPO_BACKEND"
AGENT_RUN_TEST_DATABASE_URL_ENV = "AICRM_AGENT_RUNS_TEST_DATABASE_URL"
AGENT_RUN_STAGING_DATABASE_URL_ENV = "AICRM_AGENT_RUNS_STAGING_DATABASE_URL"
AGENT_RUN_SQL_BACKENDS = {"sql", "sqlalchemy", "postgres", "postgresql"}

DEFAULT_AGENT_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {"agent_code": "central_router_agent", "agent_name": "中央路由 Agent", "agent_type": "classifier", "sort_order": 10},
    {"agent_code": "welcome_agent", "agent_name": "欢迎接待 Agent", "agent_type": "assistant", "sort_order": 20},
    {"agent_code": "pricing_agent", "agent_name": "价格答疑 Agent", "agent_type": "assistant", "sort_order": 30},
    {"agent_code": "proof_agent", "agent_name": "案例证明 Agent", "agent_type": "assistant", "sort_order": 40},
    {"agent_code": "closing_agent", "agent_name": "成交推进 Agent", "agent_type": "followup", "sort_order": 50},
)


def _psycopg_database_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://") :]
    return url


def channel_admin_uses_postgres() -> bool:
    database_url = _psycopg_database_url(raw_database_url())
    return database_url.startswith(("postgresql://", "postgres://"))


def connect_channel_admin_db() -> Any | None:
    database_url = _psycopg_database_url(raw_database_url())
    if not database_url.startswith(("postgresql://", "postgres://")):
        return None
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(database_url, row_factory=dict_row)


class AutomationRepository(Protocol):
    def list_agents(self, filters: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], int]: ...
    def create_agent(self, payload: dict[str, Any], *, idempotency_key: str, operator: str) -> dict[str, Any]: ...
    def list_agent_audit_events(self) -> list[dict[str, Any]]: ...
    def list_agent_outputs(self, filters: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], int, dict[str, Any]]: ...
    def get_agent_output(self, output_id: str, filters: dict[str, Any] | None = None) -> dict[str, Any] | None: ...
    def list_agent_runs(self, filters: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], int, dict[str, Any]]: ...
    def get_agent_run(self, run_id: str, filters: dict[str, Any] | None = None) -> dict[str, Any] | None: ...


class InMemoryAutomationRepository:
    def __init__(self) -> None:
        self._agents: dict[int, dict[str, Any]] = {
            index: agent_projection(
                {
                    "id": index,
                    "status": "active",
                    "metadata": {"source": "next_default_fixture"},
                    "config": {"description": "metadata only"},
                    "enabled": True,
                    "created_by": "fixture",
                    "updated_by": "fixture",
                    "created_at": "2026-05-20T09:40:00Z",
                    "updated_at": "2026-05-20T09:40:00Z",
                    **definition,
                }
            )
            for index, definition in enumerate(DEFAULT_AGENT_DEFINITIONS, start=1)
        }
        self._agent_idempotency: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._agent_audit_events: list[dict[str, Any]] = []
        self._agent_outputs: dict[str, dict[str, Any]] = {
            "phase4bk_output_reply_draft": {
                "id": "phase4bk_output_reply_draft",
                "output_id": "phase4bk_output_reply_draft",
                "run_id": "phase4bo_run_draft",
                "request_id": "req_phase4bk_reply",
                "userid": "user_phase4_fixture",
                "unionid": "union_external_001",
                "agent_code": "phase4bg_review_agent",
                "output_type": "reply_draft",
                "rendered_output_text": "Fixture reply draft for console review only.",
                "target_agent_code": "phase4bg_review_agent",
                "target_pool": "unactivated_priority",
                "confidence": 0.82,
                "reason": "Fixture metadata output; no LLM or live delivery executed.",
                "need_human_review": True,
                "applied_status": "pending_review",
                "error_code": "",
                "error_message": "",
                "created_at": "2026-05-20T09:50:00Z",
            },
            "phase4bk_output_route_decision": {
                "id": "phase4bk_output_route_decision",
                "output_id": "phase4bk_output_route_decision",
                "run_id": "phase4bo_run_route",
                "request_id": "req_phase4bk_route",
                "userid": "user_phase4_fixture",
                "unionid": "union_external_002",
                "agent_code": "phase4bg_followup_agent",
                "output_type": "route_decision",
                "rendered_output_text": "Fixture route decision metadata for audit-only review.",
                "target_agent_code": "phase4bg_followup_agent",
                "target_pool": "silent",
                "confidence": 0.74,
                "reason": "Fixture local route metadata; no task or agent-run execution.",
                "need_human_review": False,
                "applied_status": "draft",
                "error_code": "",
                "error_message": "",
                "created_at": "2026-05-20T09:55:00Z",
            },
        }
        self._agent_runs: dict[str, dict[str, Any]] = {
            "phase4bo_run_completed_metadata": {
                "id": "phase4bo_run_completed_metadata",
                "run_id": "phase4bo_run_completed_metadata",
                "request_id": "req_phase4bo_completed",
                "agent_code": "phase4bg_review_agent",
                "run_status": "completed",
                "trigger_source": "fixture",
                "unionid": "union_external_001",
                "userid": "user_phase4_fixture",
                "started_at": "2026-05-20T09:48:00Z",
                "finished_at": "2026-05-20T09:49:00Z",
                "duration_ms": 60000,
                "error_code": "",
                "error_message": "",
                "output_count": 1,
                "metadata": {"source": "fixture", "runtime_enabled": False},
                "created_at": "2026-05-20T09:48:00Z",
                "updated_at": "2026-05-20T09:49:00Z",
            },
            "phase4bo_run_failed_metadata": {
                "id": "phase4bo_run_failed_metadata",
                "run_id": "phase4bo_run_failed_metadata",
                "request_id": "req_phase4bo_failed",
                "agent_code": "phase4bg_followup_agent",
                "run_status": "failed",
                "trigger_source": "fixture",
                "unionid": "union_external_002",
                "userid": "user_phase4_fixture",
                "started_at": "2026-05-20T09:52:00Z",
                "finished_at": "2026-05-20T09:52:30Z",
                "duration_ms": 30000,
                "error_code": "fixture_agent_run_failed",
                "error_message": "Fixture local metadata failure; no live runtime path was invoked.",
                "output_count": 0,
                "metadata": {"source": "fixture", "runtime_enabled": False},
                "created_at": "2026-05-20T09:52:00Z",
                "updated_at": "2026-05-20T09:52:30Z",
            },
        }

    def list_agents(self, filters: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], int]:
        filters = filters or {}
        agent_type = str(filters.get("agent_type") or "").strip()
        status = str(filters.get("status") or "").strip()
        include_archived = bool(filters.get("include_archived"))
        limit = int(filters.get("limit") or 50)
        offset = int(filters.get("offset") or 0)
        rows = [agent_projection(item) for item in self._agents.values()]
        if agent_type:
            rows = [item for item in rows if str(item.get("agent_type") or "") == agent_type]
        if status:
            rows = [item for item in rows if str(item.get("status") or "") == status]
        if not include_archived:
            rows = [item for item in rows if not str(item.get("archived_at") or "").strip()]
        rows.sort(key=lambda item: (int(item.get("sort_order") or 0), int(item.get("id") or 0)))
        total = len(rows)
        return deepcopy(rows[offset : offset + limit]), total

    def create_agent(self, payload: dict[str, Any], *, idempotency_key: str, operator: str) -> dict[str, Any]:
        key = str(idempotency_key or "").strip()
        if not key:
            raise ContractError("idempotency_key is required")
        operator_id = str(operator or "system").strip() or "system"
        normalized = normalize_agent_create_payload({**payload, "operator": operator_id})
        idempotency_scope = (AGENT_ROUTE_FAMILY, "create", operator_id, key)
        request_hash = self._request_hash(normalized)
        replay = self._agent_idempotency.get(idempotency_scope)
        if replay:
            if replay.get("request_hash") != request_hash:
                raise ContractError("idempotency key conflicts with a different request payload")
            response = deepcopy(replay.get("response_snapshot") or {})
            response["idempotent_replay"] = True
            return response
        self._assert_unique_agent(normalized["agent_code"])
        now = utc_now_iso()
        agent_id = max(self._agents) + 1 if self._agents else 1
        saved = agent_projection({**normalized, "id": agent_id, "created_at": now, "updated_at": now})
        self._agents[agent_id] = deepcopy(saved)
        rollback_payload = {
            "strategy": "archive_created_agent_in_later_approved_phase",
            "created_agent_id": agent_id,
            "agent_code": saved["agent_code"],
            "delete_approved": False,
        }
        audit_event = self._append_agent_audit_event(
            operation="create",
            operator=operator_id,
            resource_id=agent_id,
            before={},
            after=saved,
            request_payload=normalized,
            rollback_payload=rollback_payload,
        )
        result = {
            "agent": deepcopy(saved),
            "agents": [deepcopy(saved)],
            "audit_event": audit_event,
            "rollback_payload": rollback_payload,
            "idempotent_replay": False,
        }
        self._agent_idempotency[idempotency_scope] = {
            "request_hash": request_hash,
            "response_snapshot": deepcopy(result),
            "resource_id": agent_id,
            "status": "succeeded",
        }
        return result

    def list_agent_audit_events(self) -> list[dict[str, Any]]:
        return deepcopy(self._agent_audit_events)

    def list_agent_outputs(self, filters: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
        normalized = normalize_agent_output_filters(filters)
        rows = [deepcopy(item) for item in self._agent_outputs.values()]
        for field in ("request_id", "unionid", "userid", "agent_code", "output_type", "applied_status"):
            value = str(normalized.get(field) or "").strip()
            if value:
                rows = [item for item in rows if str(item.get(field) or "") == value]
        min_confidence = normalized.get("min_confidence")
        if min_confidence is not None:
            rows = [item for item in rows if float(item.get("confidence") or 0) >= float(min_confidence)]
        max_confidence = normalized.get("max_confidence")
        if max_confidence is not None:
            rows = [item for item in rows if float(item.get("confidence") or 0) <= float(max_confidence)]
        has_error = normalized.get("has_error")
        if has_error is not None:
            expected = bool(has_error)
            rows = [item for item in rows if bool(str(item.get("error_code") or item.get("error_message") or "").strip()) is expected]
        rows.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("output_id") or "")), reverse=True)
        total = len(rows)
        page_rows = rows[normalized["offset"] : normalized["offset"] + normalized["page_size"]]
        projected = [agent_output_projection(item, visibility=normalized["visibility"]) for item in page_rows]
        return deepcopy(projected), total, deepcopy(normalized)

    def get_agent_output(self, output_id: str, filters: dict[str, Any] | None = None) -> dict[str, Any] | None:
        normalized = normalize_agent_output_filters(filters)
        item = self._agent_outputs.get(str(output_id or "").strip())
        if not item:
            return None
        return agent_output_projection(item, visibility=normalized["visibility"])

    def list_agent_runs(self, filters: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
        normalized = normalize_agent_run_filters(filters)
        rows = [deepcopy(item) for item in self._agent_runs.values()]
        for field in ("request_id", "run_id", "agent_code", "run_status", "trigger_source", "unionid", "userid"):
            value = str(normalized.get(field) or "").strip()
            if value:
                rows = [item for item in rows if str(item.get(field) or "") == value]
        if normalized["started_after"]:
            rows = [item for item in rows if str(item.get("started_at") or "") >= normalized["started_after"]]
        if normalized["started_before"]:
            rows = [item for item in rows if str(item.get("started_at") or "") <= normalized["started_before"]]
        has_error = normalized.get("has_error")
        if has_error is not None:
            expected = bool(has_error)
            rows = [item for item in rows if bool(str(item.get("error_code") or item.get("error_message") or "").strip()) is expected]
        rows.sort(key=lambda item: (str(item.get("started_at") or ""), str(item.get("run_id") or "")), reverse=True)
        total = len(rows)
        page_rows = rows[normalized["offset"] : normalized["offset"] + normalized["page_size"]]
        projected = [agent_run_projection(item, visibility=normalized["visibility"]) for item in page_rows]
        return deepcopy(projected), total, deepcopy(normalized)

    def get_agent_run(self, run_id: str, filters: dict[str, Any] | None = None) -> dict[str, Any] | None:
        normalized = normalize_agent_run_filters(filters)
        item = self._agent_runs.get(str(run_id or "").strip())
        if not item:
            return None
        return agent_run_projection(item, visibility=normalized["visibility"])

    def _request_hash(self, payload: dict[str, Any]) -> str:
        import hashlib
        import json

        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def _assert_unique_agent(self, agent_code: str) -> None:
        normalized_code = str(agent_code or "").strip().lower()
        for agent in self._agents.values():
            item = agent_projection(agent)
            if str(item.get("agent_code") or "").strip().lower() == normalized_code:
                raise ContractError("agent code already exists")

    def _append_agent_audit_event(
        self,
        *,
        operation: str,
        operator: str,
        resource_id: int,
        before: dict[str, Any],
        after: dict[str, Any],
        request_payload: dict[str, Any],
        rollback_payload: dict[str, Any],
    ) -> dict[str, Any]:
        event = {
            "route_family": AGENT_ROUTE_FAMILY,
            "operation": operation,
            "operator": str(operator or "system"),
            "resource_type": "agent",
            "resource_id": int(resource_id),
            "before_snapshot": deepcopy(before),
            "after_snapshot": deepcopy(after),
            "request_payload": deepcopy(request_payload),
            "validation_result": {"ok": True},
            "rollback_payload": deepcopy(rollback_payload),
            "side_effect_safety": agent_side_effect_safety(),
            "created_at": utc_now_iso(),
        }
        self._agent_audit_events.insert(0, event)
        return deepcopy(event)


_fixture_repo = InMemoryAutomationRepository()


def _agent_repository_backend() -> str:
    return startup_environment_setting(AGENT_BACKEND_ENV, "fixture").strip().lower()


def _agent_database_url() -> str:
    return str(
        startup_environment_setting(AGENT_TEST_DATABASE_URL_ENV)
        or startup_environment_setting(AGENT_STAGING_DATABASE_URL_ENV)
        or raw_database_url()
    ).strip()


def agent_postgres_enabled() -> bool:
    return _agent_repository_backend() in AGENT_SQL_BACKENDS or bool(production_data_ready() and raw_database_url())


def _agent_output_repository_backend() -> str:
    return startup_environment_setting(AGENT_OUTPUT_BACKEND_ENV, "fixture").strip().lower()


def _agent_output_database_url() -> str:
    return str(
        startup_environment_setting(AGENT_OUTPUT_TEST_DATABASE_URL_ENV)
        or startup_environment_setting(AGENT_OUTPUT_STAGING_DATABASE_URL_ENV)
    ).strip()


def _agent_run_repository_backend() -> str:
    return startup_environment_setting(AGENT_RUN_BACKEND_ENV, "fixture").strip().lower()


def _agent_run_database_url() -> str:
    return str(
        startup_environment_setting(AGENT_RUN_TEST_DATABASE_URL_ENV)
        or startup_environment_setting(AGENT_RUN_STAGING_DATABASE_URL_ENV)
    ).strip()


def build_automation_repository(
    *,
    agent_backend: str | None = None,
    agent_engine: Any | None = None,
    agent_output_backend: str | None = None,
    agent_output_engine: Any | None = None,
    agent_run_backend: str | None = None,
    agent_run_engine: Any | None = None,
) -> AutomationRepository:
    explicit_agent_backend = any(value is not None for value in (agent_backend, agent_output_backend, agent_run_backend))
    selected_agent_backend = str(agent_backend or _agent_repository_backend()).strip().lower()
    if agent_backend is None and not explicit_agent_backend and production_data_ready() and raw_database_url():
        selected_agent_backend = "postgres"
    if selected_agent_backend in AGENT_SQL_BACKENDS:
        engine = agent_engine
        if engine is None:
            database_url = _agent_database_url()
            if not database_url:
                raise ContractError(
                    f"{AGENT_TEST_DATABASE_URL_ENV} or {AGENT_STAGING_DATABASE_URL_ENV} is required when {AGENT_BACKEND_ENV}=sqlalchemy"
                )
            engine = get_engine(database_url)
        from .agent_postgres_repository import PostgresAgentRepository

        return assert_repository_allowed(
            PostgresAgentRepository(engine),
            capability_owner="automation_engine.agents",
        )
    selected_agent_output_backend = str(agent_output_backend or _agent_output_repository_backend()).strip().lower()
    if selected_agent_output_backend in AGENT_OUTPUT_SQL_BACKENDS:
        engine = agent_output_engine
        if engine is None:
            database_url = _agent_output_database_url()
            if not database_url:
                raise ContractError(
                    f"{AGENT_OUTPUT_TEST_DATABASE_URL_ENV} or {AGENT_OUTPUT_STAGING_DATABASE_URL_ENV} is required when {AGENT_OUTPUT_BACKEND_ENV}=sqlalchemy"
                )
            engine = get_engine(database_url)
        from .agent_output_sqlalchemy_repository import SqlAlchemyAgentOutputRepository

        return assert_repository_allowed(
            SqlAlchemyAgentOutputRepository(engine),
            capability_owner="automation_engine.agent_outputs",
        )
    selected_agent_run_backend = str(agent_run_backend or _agent_run_repository_backend()).strip().lower()
    if selected_agent_run_backend in AGENT_RUN_SQL_BACKENDS:
        engine = agent_run_engine
        if engine is None:
            database_url = _agent_run_database_url()
            if not database_url:
                raise ContractError(
                    f"{AGENT_RUN_TEST_DATABASE_URL_ENV} or {AGENT_RUN_STAGING_DATABASE_URL_ENV} is required when {AGENT_RUN_BACKEND_ENV}=sqlalchemy"
                )
            engine = get_engine(database_url)
        from .agent_run_sqlalchemy_repository import SqlAlchemyAgentRunRepository

        return assert_repository_allowed(
            SqlAlchemyAgentRunRepository(engine),
            capability_owner="automation_engine.agent_runs",
        )
    return assert_repository_allowed(_fixture_repo, capability_owner="automation_engine")


def reset_automation_fixture_state() -> None:
    global _fixture_repo
    _fixture_repo = InMemoryAutomationRepository()


FixtureAutomationRepository = InMemoryAutomationRepository
