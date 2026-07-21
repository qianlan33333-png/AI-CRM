#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from aicrm_next.admin_jobs.application import build_jobs_payload
from aicrm_next.admin_jobs.repository import PostgresAdminJobsRepository
from aicrm_next.customer_read_model.repo import SqlAlchemyCustomerReadModelRepository
from aicrm_next.customer_read_model.sidebar_v2 import SidebarV2SqlRepository, SidebarWorkbenchReadModel
from aicrm_next.platform_foundation.performance_contracts import (
    ReadPathBaseline,
    collect_plan_evidence,
    evaluate_read_path_report,
    load_read_path_baselines,
    percentile,
)
from aicrm_next.platform_foundation.push_center.sql_read_model import SQLPushCenterReadModel
from aicrm_next.questionnaire.repo import PostgresQuestionnaireReadRepository
from aicrm_next.shared.db_session import get_sqlalchemy_database_url


LOCK_KEY = 4_249_015_149


@dataclass(frozen=True)
class CapturedQuery:
    sql: str
    params: Any


class _RecordingConnection:
    def __init__(self, connection: Any, queries: list[CapturedQuery]) -> None:
        self._connection = connection
        self._queries = queries

    def __enter__(self) -> _RecordingConnection:
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback) -> Any:  # noqa: ANN001
        return self._connection.__exit__(exc_type, exc, traceback)

    def execute(self, query: str, params: Any = None) -> Any:
        self._queries.append(CapturedQuery(str(query), params or ()))
        return self._connection.execute(query, params)


class _RecordingQuestionnaireRepository(PostgresQuestionnaireReadRepository):
    def __init__(self, database_url: str, queries: list[CapturedQuery]) -> None:
        super().__init__(database_url)
        self._queries = queries

    def _connect(self):
        return _RecordingConnection(super()._connect(), self._queries)


class _RecordingAdminJobsRepository(PostgresAdminJobsRepository):
    def __init__(self, queries: list[CapturedQuery]) -> None:
        self._queries = queries

    def _rows(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self._queries.append(CapturedQuery(str(query), params))
        return super()._rows(query, params)


class _StaticCustomerContext:
    def __call__(self, request) -> dict[str, Any]:  # noqa: ANN001
        return {
            "ok": True,
            "source_status": "next_read_model",
            "customer": {
                "external_userid": request.external_userid,
                "customer_name": "匿名客户",
                "owner_userid": request.owner_userid,
                "mobile": "13800000001",
                "binding": {"is_bound": True, "mobile": "13800000001"},
                "sidebar_context": {"workflow_title": "基准流程"},
            },
            "binding": {"is_bound": True, "mobile": "13800000001"},
            "messages": [],
            "timeline": {"items": []},
        }


def _psycopg_url(value: str) -> str:
    value = str(value or "").strip()
    if value.startswith("postgresql+psycopg://"):
        return "postgresql://" + value.removeprefix("postgresql+psycopg://")
    if value.startswith(("postgresql://", "postgres://")):
        return value
    raise ValueError("DATABASE_URL must use PostgreSQL")


def _redact_error(message: str, database_url: str) -> str:
    redacted = str(message or "").replace(str(database_url or ""), "[database-url-redacted]")
    try:
        password = urlsplit(str(database_url or "")).password
    except ValueError:
        password = None
    return redacted.replace(password, "***") if password else redacted


def _seed_dataset(database_url: str) -> None:
    import psycopg

    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        try:
            conn.execute(
                "TRUNCATE TABLE customer_list_index_next, questionnaire_questions, "
                "questionnaire_submissions, questionnaires, sidebar_customer_profile_fields, "
                "crm_user_identity, sync_runs, wecom_external_contact_event_logs, "
                "external_effect_job, broadcast_jobs, outbound_webhook_deliveries "
                "RESTART IDENTITY CASCADE"
            )
            conn.execute(
                """
                INSERT INTO customer_list_index_next (
                    id, unionid, customer_name, owner_userid, owner_display_name, remark,
                    description, mobile, is_bound, binding_status, tags_json,
                    class_user_status_json, last_message_at, last_touch_at, updated_at, created_at
                )
                SELECT n, 'perf_union_' || n, '匿名客户' || n, 'owner_' || (n % 20),
                       '匿名顾问', '', '', '138' || lpad(n::text, 8, '0'), TRUE, 'bound',
                       '[]'::jsonb, '{}'::jsonb, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                       CURRENT_TIMESTAMP - (n || ' seconds')::interval,
                       CURRENT_TIMESTAMP - (n || ' seconds')::interval
                FROM generate_series(1, 10000) AS n
                """
            )
            conn.execute(
                """
                INSERT INTO questionnaires (id, slug, name, title, updated_at, created_at)
                SELECT n, 'perf-questionnaire-' || n, '匿名问卷' || n, '匿名问卷' || n,
                       CURRENT_TIMESTAMP - (n || ' seconds')::interval,
                       CURRENT_TIMESTAMP - (n || ' seconds')::interval
                FROM generate_series(1, 2000) AS n
                """
            )
            conn.execute(
                """
                INSERT INTO questionnaire_questions (id, questionnaire_id, title, sort_order)
                SELECT n, ((n - 1) % 2000) + 1, '匿名问题' || n, n % 5
                FROM generate_series(1, 10000) AS n
                """
            )
            conn.execute(
                """
                INSERT INTO questionnaire_submissions (id, questionnaire_id, unionid, submitted_at)
                SELECT n, ((n - 1) % 2000) + 1, 'perf_union_' || (((n - 1) % 5000) + 1),
                       CURRENT_TIMESTAMP - (n || ' seconds')::interval
                FROM generate_series(1, 20000) AS n
                """
            )
            conn.execute(
                """
                INSERT INTO crm_user_identity (
                    unionid, primary_external_userid, external_userids_json, mobile,
                    mobile_normalized, mobile_verified, customer_name,
                    primary_owner_userid, identity_status
                )
                SELECT 'perf_sidebar_union_' || n, 'perf_external_' || n,
                       jsonb_build_array('perf_external_' || n),
                       '138' || lpad(n::text, 8, '0'), '138' || lpad(n::text, 8, '0'),
                       TRUE, '匿名客户' || n, 'owner_' || (n % 20), 'active'
                FROM generate_series(1, 5000) AS n
                """
            )
            conn.execute(
                """
                INSERT INTO sidebar_customer_profile_fields (
                    unionid, source, industry, industry_description,
                    needs_blockers_followup, updated_by
                ) VALUES ('perf_sidebar_union_1', '基准数据', '测试行业', '', '', 'benchmark')
                """
            )
            conn.execute(
                """
                INSERT INTO sync_runs (id, status, owner_userid, created_at)
                SELECT n, CASE WHEN n % 7 = 0 THEN 'failed' ELSE 'success' END,
                       'owner_' || (n % 20), CURRENT_TIMESTAMP - (n || ' seconds')::interval
                FROM generate_series(1, 5000) AS n
                """
            )
            conn.execute(
                """
                INSERT INTO wecom_external_contact_event_logs (
                    id, event_type, change_type, external_userid, user_id, event_time,
                    event_key, process_status, created_at, updated_at
                )
                SELECT n, 'change_external_contact', 'update', 'perf_external_' || n,
                       'owner_' || (n % 20), n::text, 'perf_event_' || n,
                       CASE WHEN n % 9 = 0 THEN 'failed' ELSE 'success' END,
                       CURRENT_TIMESTAMP - (n || ' seconds')::interval,
                       CURRENT_TIMESTAMP - (n || ' seconds')::interval
                FROM generate_series(1, 5000) AS n
                """
            )
            conn.execute(
                """
                INSERT INTO broadcast_jobs (
                    id, source_type, source_id, source_table, scheduled_for, batch_key,
                    status, target_count, created_at, updated_at
                )
                SELECT n, 'manual', 'perf_job_' || n, 'performance_fixture',
                       CURRENT_TIMESTAMP - (n || ' seconds')::interval, 'perf_batch_' || n,
                       CASE WHEN n % 11 = 0 THEN 'failed' ELSE 'queued' END, 1,
                       CURRENT_TIMESTAMP - (n || ' seconds')::interval,
                       CURRENT_TIMESTAMP - (n || ' seconds')::interval
                FROM generate_series(1, 5000) AS n
                """
            )
            conn.execute(
                """
                INSERT INTO outbound_webhook_deliveries (
                    id, event_type, source_key, source_id, target_url, status,
                    created_at, updated_at
                )
                SELECT n, 'questionnaire_submit', 'perf', n::text,
                       'https://example.invalid/performance',
                       CASE WHEN n % 13 = 0 THEN 'failed' ELSE 'success' END,
                       CURRENT_TIMESTAMP - (n || ' seconds')::interval,
                       CURRENT_TIMESTAMP - (n || ' seconds')::interval
                FROM generate_series(1, 5000) AS n
                """
            )
            conn.execute(
                """
                INSERT INTO external_effect_job (
                    effect_type, adapter_name, operation, target_type, target_id,
                    business_type, business_id, source_module, source_route,
                    trace_id, idempotency_key, execution_mode, payload_summary_json,
                    status, scheduled_at, execution_id, lane, available_at,
                    created_at, updated_at
                )
                SELECT
                    CASE WHEN n % 5 = 0 THEN 'wecom.media.upload' ELSE 'wecom.contact.tag.mark' END,
                    'performance_adapter', 'execute', 'external_userid', 'perf_external_' || n,
                    'performance_fixture', 'perf_effect_' || n,
                    'performance_fixture', '/performance/push-center',
                    'perf_effect_trace_' || n, 'perf_effect_idempotency_' || n,
                    'execute', jsonb_build_object(
                        'external_userid', 'perf_external_' || n,
                        'owner_userid', 'owner_' || (n % 20),
                        'execution_id', 'perf_execution_' || n
                    ),
                    'queued', CURRENT_TIMESTAMP - (n || ' seconds')::interval,
                    'exe_perf_' || n,
                    CASE WHEN n % 5 = 0 THEN 'wecom_media' ELSE 'wecom_interactive' END,
                    CURRENT_TIMESTAMP - (n || ' seconds')::interval,
                    CURRENT_TIMESTAMP - (n || ' seconds')::interval,
                    CURRENT_TIMESTAMP - (n || ' seconds')::interval
                FROM generate_series(1, 95000) AS n
                """
            )
            conn.execute(
                "ANALYZE customer_list_index_next, questionnaires, questionnaire_questions, "
                "questionnaire_submissions, crm_user_identity, sidebar_customer_profile_fields, "
                "sync_runs, wecom_external_contact_event_logs, external_effect_job, "
                "broadcast_jobs, outbound_webhook_deliveries"
            )
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


def _measure(
    profile: ReadPathBaseline,
    invoke: Callable[[list[CapturedQuery]], int],
) -> tuple[list[float], int, int, list[CapturedQuery]]:
    invoke([])
    latencies: list[float] = []
    query_counts: list[int] = []
    page_rows: list[int] = []
    evidence_queries: list[CapturedQuery] = []
    for sample_index in range(profile.sample_count):
        captured: list[CapturedQuery] = []
        started = perf_counter()
        rows = invoke(captured)
        latencies.append((perf_counter() - started) * 1000.0)
        query_counts.append(len(captured))
        page_rows.append(int(rows))
        if sample_index == 0:
            evidence_queries = list(captured)
    if len(set(query_counts)) != 1:
        raise RuntimeError(f"{profile.name}: unstable query count across samples: {query_counts}")
    return latencies, max(query_counts), max(page_rows), evidence_queries


def _sqlalchemy_recorder(engine, target: list[CapturedQuery]):  # noqa: ANN001
    @event.listens_for(engine, "before_cursor_execute")
    def record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        target.append(CapturedQuery(str(statement), parameters))

    return record


def _run_customer_list(database_url: str, profile: ReadPathBaseline):
    engine = create_engine(get_sqlalchemy_database_url(database_url), future=True)

    def invoke(captured: list[CapturedQuery]) -> int:
        listener = _sqlalchemy_recorder(engine, captured)
        try:
            with Session(engine) as session:
                repo = SqlAlchemyCustomerReadModelRepository(session)
                rows = repo.list_customers({"owner_userid": "owner_1"}, limit=50, offset=0)
                repo.count_customers({"owner_userid": "owner_1"})
                return len(rows)
        finally:
            event.remove(engine, "before_cursor_execute", listener)

    try:
        return _measure(profile, invoke)
    finally:
        engine.dispose()


def _run_sidebar_workbench(database_url: str, profile: ReadPathBaseline):
    engine = create_engine(get_sqlalchemy_database_url(database_url), future=True)
    repo = SidebarV2SqlRepository(engine)
    read_model = SidebarWorkbenchReadModel(repo=repo, context_query=_StaticCustomerContext())

    def invoke(captured: list[CapturedQuery]) -> int:
        listener = _sqlalchemy_recorder(engine, captured)
        try:
            payload = read_model(
                external_userid="perf_external_1",
                owner_userid="owner_1",
                owner_verified=True,
            )
            return 1 if payload.get("customer") else 0
        finally:
            event.remove(engine, "before_cursor_execute", listener)

    try:
        return _measure(profile, invoke)
    finally:
        engine.dispose()


def _run_questionnaire_admin(database_url: str, profile: ReadPathBaseline):
    def invoke(captured: list[CapturedQuery]) -> int:
        repo = _RecordingQuestionnaireRepository(database_url, captured)
        rows, _total = repo.list_questionnaires(limit=50, offset=0)
        return len(rows)

    return _measure(profile, invoke)


def _run_admin_jobs(database_url: str, profile: ReadPathBaseline):
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url

    def invoke(captured: list[CapturedQuery]) -> int:
        payload = build_jobs_payload({}, repo=_RecordingAdminJobsRepository(captured))
        return max(
            len(payload.get("sync_runs") or []),
            len(payload.get("callback_logs") or []),
            len(payload.get("batch_rows") or []),
            len(payload.get("webhook_deliveries") or []),
        )

    try:
        return _measure(profile, invoke)
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def _run_push_center(database_url: str, profile: ReadPathBaseline):
    engine = create_engine(get_sqlalchemy_database_url(database_url), future=True)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    read_model = SQLPushCenterReadModel(session_factory=factory)
    previous_cursor_secret = os.environ.get("AICRM_PUSH_CENTER_CURSOR_SECRET")
    os.environ["AICRM_PUSH_CENTER_CURSOR_SECRET"] = "critical-read-performance-cursor-secret"
    verified = False

    def invoke(captured: list[CapturedQuery]) -> int:
        nonlocal verified
        listener = _sqlalchemy_recorder(engine, captured)
        try:
            first = read_model.query({}, limit=profile.page_limit)
            if not verified:
                second = read_model.query({}, limit=profile.page_limit, cursor=first.next_cursor)
                filtered = read_model.query({"section": "integrations"}, limit=profile.page_limit)
                first_ids = {item["projection_id"] for item in first.items}
                second_ids = {item["projection_id"] for item in second.items}
                if first.total != profile.dataset_rows:
                    raise RuntimeError(
                        f"push_center: expected {profile.dataset_rows} rows, got {first.total}"
                    )
                if first_ids & second_ids:
                    raise RuntimeError("push_center: keyset pages overlap")
                if filtered.total != 19000 or filtered.counts["by_section"] != {"integrations": 19000}:
                    raise RuntimeError("push_center: SQL section filter/count mismatch")
                verified = True
            return len(first.items)
        finally:
            event.remove(engine, "before_cursor_execute", listener)

    try:
        return _measure(profile, invoke)
    finally:
        if previous_cursor_secret is None:
            os.environ.pop("AICRM_PUSH_CENTER_CURSOR_SECRET", None)
        else:
            os.environ["AICRM_PUSH_CENTER_CURSOR_SECRET"] = previous_cursor_secret
        engine.dispose()


def _explain(database_url: str, queries: list[CapturedQuery]) -> list[dict[str, Any]]:
    import psycopg

    evidence: list[dict[str, Any]] = []
    with psycopg.connect(database_url) as conn:
        for index, query in enumerate(queries, start=1):
            result = conn.execute(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query.sql,
                query.params,
            ).fetchone()
            payload = result[0] if result else []
            root = payload[0]["Plan"]
            evidence.append({"query": f"query_{index}", **collect_plan_evidence(root)})
    return evidence


def run(database_url: str) -> dict[str, Any]:
    profiles = load_read_path_baselines()
    _seed_dataset(database_url)
    runners = {
        "customer_list": _run_customer_list,
        "sidebar_workbench": _run_sidebar_workbench,
        "questionnaire_admin": _run_questionnaire_admin,
        "admin_jobs": _run_admin_jobs,
        "push_center": _run_push_center,
    }
    reports: dict[str, Any] = {}
    failures: list[str] = []
    for name, profile in profiles.items():
        latencies, query_count, max_page_rows, queries = runners[name](database_url, profile)
        report = {
            "route": profile.route,
            "owner": profile.owner,
            "dataset_rows": profile.dataset_rows,
            "sample_count": len(latencies),
            "query_count": query_count,
            "max_page_rows": max_page_rows,
            "p50_ms": round(percentile(latencies, 50), 3),
            "p95_ms": round(percentile(latencies, 95), 3),
            "baseline_p95_ms": profile.baseline_p95_ms,
            "p95_limit_ms": round(profile.baseline_p95_ms * profile.regression_factor, 3),
            "plans": _explain(database_url, queries),
        }
        profile_failures = evaluate_read_path_report(profile, report)
        reports[name] = {**report, "ok": not profile_failures, "failures": profile_failures}
        failures.extend(f"{name}: {failure}" for failure in profile_failures)
    return {
        "ok": not failures,
        "baseline_version": 1,
        "postgresql_only": True,
        "profiles": reports,
        "failures": failures,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check critical Next read-path PostgreSQL performance baselines.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        database_url = _psycopg_url(args.database_url)
        report = run(database_url)
    except Exception as exc:
        report = {
            "ok": False,
            "error": type(exc).__name__,
            "message": _redact_error(str(exc), args.database_url),
        }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
