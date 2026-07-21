"""顶层 pytest fixtures — PG-only。

2026-05 砍掉 SQLite 后，所有测试**必须**连 PG。本地跑：

    docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=test -e POSTGRES_USER=test -e POSTGRES_DB=test postgres:16
    DATABASE_URL=postgresql://test:test@localhost:5432/test pytest

CI 上 service container 自动起 PG 并设 DATABASE_URL。

并行执行（pytest-xdist）：``pytest -n auto``。每个 worker 拿一个独立的
``test_<worker_id>`` 数据库——避免并发 truncate / 写竞争。需要 ``DATABASE_URL``
对应的 user 有 ``CREATEDB`` 权限（postgres 官方镜像里 POSTGRES_USER 是
superuser，开箱即用）。

提供的 fixture：
- ``next_app`` / ``next_client``：Next FastAPI 默认测试入口
- ``next_pg_schema``：显式 opt-in 的 Next/Alembic PG schema 测试入口
- ``app`` / ``client``：默认指向 Next FastAPI，测试层不再提供 legacy Flask bridge
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import pytest

# 让 import 能找到项目包
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Most production-mode tests exercise data-source behavior, not admin login state.
# Auth-specific tests opt in with AICRM_ADMIN_AUTH_ENFORCED=true.
os.environ.setdefault("AICRM_ADMIN_AUTH_ENFORCED", "false")
os.environ.setdefault("SECRET_KEY", "pytest-secret-key")
os.environ.setdefault("WECHAT_SHOP_CALLBACK_TOKEN", "pytest-wechat-shop-callback-token")


def _env_flag(name: str) -> bool:
    return str(os.environ.get(name, "") or "").strip().lower() in {"1", "true", "yes", "on"}


def _fixture_default_runtime_enabled() -> bool:
    return _env_flag("AICRM_PYTEST_FIXTURE_DEFAULT")


def _xdist_worker_id() -> str:
    """xdist 子 worker 是 "gw0" / "gw1" / ...；非并行运行 / 主进程返回 "master"。"""
    return os.environ.get("PYTEST_XDIST_WORKER", "master")


def _resolve_worker_database_url() -> str:
    """把 base ``DATABASE_URL`` 改成 per-worker DB ``test_<worker_id>``。

    主进程（serial 或 xdist master）继续用 base DB。子 worker 各自挂自己的 DB
    避免 truncate / DDL 互相打架。如果 worker DB 还不存在，连 base DB 用 raw
    psycopg 发一次 ``CREATE DATABASE``（postgres 官方镜像里 POSTGRES_USER 是
    superuser，有 CREATEDB 权限）。
    """
    base_url = os.environ.get("DATABASE_URL", "").strip() or os.environ.get("AICRM_TEST_DATABASE_URL", "").strip()
    if not base_url:
        return ""
    worker_id = _xdist_worker_id()
    if worker_id == "master":
        return base_url
    parsed = urlparse(base_url)
    base_db = parsed.path.lstrip("/") or "test"
    worker_db = f"{base_db}_{worker_id}"
    try:
        import psycopg

        bootstrap = psycopg.connect(base_url, autocommit=True)
        cur = bootstrap.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (worker_db,))
        if not cur.fetchone():
            # PG identifiers can't be parameterised; worker_id is "gw\d+" so safe to inline
            cur.execute(f'CREATE DATABASE "{worker_db}"')
        cur.close()
        bootstrap.close()
    except Exception:
        # 起 worker DB 失败时降级回 base DB（serial 模式）
        os.environ["AICRM_TEST_DATABASE_URL"] = base_url
        return base_url
    new_url = urlunparse(parsed._replace(path=f"/{worker_db}"))
    os.environ["AICRM_TEST_DATABASE_URL"] = new_url
    if not _fixture_default_runtime_enabled():
        os.environ["DATABASE_URL"] = new_url
    return new_url


# 测试间需要清理的关键表（FK 反向顺序：子表先清，autouse 用 CASCADE 兜底剩余 FK）
_TABLES_TO_TRUNCATE = [
    # durable callback ingress
    "webhook_inbox",
    # — ai audience ops
    "ai_audience_refresh_source_receipt",
    "ai_audience_refresh_intent",
    "ai_audience_inbound_webhook_event",
    "ai_audience_package_sender",
    "ai_audience_outbound_subscription",
    "ai_audience_member_event",
    "ai_audience_member_current",
    "ai_audience_package_run",
    "ai_audience_package_dependency",
    "ai_audience_package_version",
    "ai_audience_package",
    # — automation / campaign domain
    "automation_touch_delivery_log",
    "automation_frequency_consumption",
    "automation_frequency_budget",
    "automation_task_plan_v2",
    "automation_stage_entry_v2",
    "automation_membership_v2",
    "automation_event_v2",
    "automation_member_audience_entry",
    "automation_program_member_stage_history",
    "automation_program_admission_attempt",
    "automation_program_member",
    "automation_channel_contact",
    "automation_channel_assignment_event",
    "automation_channel_assignee",
    "automation_program_channel_binding",
    "automation_workflow_node_content_variant",
    "automation_workflow_node_content",
    "automation_workflow_node_transition",
    "automation_workflow_node",
    "automation_workflow_goal",
    "automation_operation_templates",
    "automation_workflow",
    "automation_member",
    "wecom_customer_acquisition_links",
    "automation_program_config_block",
    "automation_program",
    "automation_channel",
    "automation_sop_progress",
    "automation_sop_pool_config",
    "automation_sop_batch_item",
    "automation_sop_batch",
    "automation_sop_template",
    "automation_agent_run",
    "channel_welcome_effect_dependency",
    "channel_welcome_effect_graph",
    "automation_group_ops_effect_dependency",
    "automation_group_ops_effect_material",
    "automation_group_ops_effect_graph",
    "automation_agent_output",
    "automation_agent_llm_call_log",
    "automation_agent_webhook_item",
    "automation_agent_webhook_batch",
    "automation_agent_runtime_config",
    "automation_focus_send_batch_item",
    "automation_focus_send_batch",
    "automation_agent_skill_call_audit",
    "automation_agent_skill_registry",
    "automation_agent_prompt_registry",
    "automation_workflow_agent_binding",
    "automation_agent_config",
    "automation_agent_output_export_job",
    "automation_agent_router_config",
    "automation_laohuang_chat_job",
    "automation_reply_monitor_queue",
    "automation_reply_monitor_config",
    "automation_message_activity_sync_item",
    "automation_message_activity_sync_run",
    # — campaigns
    "campaign_members",
    "campaign_steps",
    "campaign_segments",
    "campaigns",
    # — cloud orchestrator
    "cloud_approval_tokens",
    "cloud_broadcast_plans",
    "cloud_agent_audit_log",
    # — segments + value
    "segments",
    "customer_value_segment_history",
    "customer_value_segment_current",
    "customer_marketing_state_history",
    "customer_marketing_state_current",
    # signup_conversion_question_rules / signup_conversion_config 已合入
    # marketing_automation_question_rules / marketing_automation_configs（下面列出），
    # PG schema 里不再有这两张表。
    # — libraries
    "image_library",
    "miniprogram_library",
    # — questionnaire
    "questionnaire_external_push_logs",
    "questionnaire_scrm_apply_logs",
    "questionnaire_submission_answers",
    "questionnaire_submissions",
    "questionnaire_options",
    "questionnaire_questions",
    "questionnaire_score_rules",
    "questionnaires",
    "external_push_delivery",
    "identity_resolution_completion_receipt",
    "internal_event_consumer_attempt",
    "internal_event_consumer_run",
    "internal_event",
    "internal_event_outbox",
    "external_effect_attempt",
    "external_effect_test_receipt",
    "external_effect_job",
    "external_push_config",
    "domain_event_outbox",
    "commerce_coupon_redemptions",
    "commerce_coupon_claims",
    "commerce_coupon_product_bindings",
    "commerce_coupons",
    "service_period_huangyoucan_usage_sync_runs",
    "service_period_huangyoucan_usage_snapshot",
    "service_period_member_collaborators",
    "service_period_member_shares",
    "service_period_member_views",
    "service_period_events",
    "service_period_entitlements",
    "service_period_products",
    "wechat_pay_product_page_slices",
    "wechat_pay_products",
    "wechat_pay_order_export_jobs",
    "wechat_pay_refunds",
    "wechat_pay_order_events",
    "wechat_pay_orders",
    "alipay_pay_order_events",
    "alipay_pay_orders",
    # — admin / auth
    "auth_webhook_replay",
    "auth_sessions",
    "auth_security_events",
    "auth_webhook_clients",
    "auth_api_clients",
    "admin_wecom_directory_members",
    "admin_users",
    "owner_role_map",
    "routing_rule_config",
    "wechat_pay_order_events",
    "wechat_pay_orders",
    "alipay_pay_order_events",
    "alipay_pay_orders",
    "app_settings",
    "mcp_tool_settings",
    # — contacts / identity
    "contacts",
    "external_contact_bindings",
    "crm_user_identity_merge_audit",
    "crm_user_identity_conflicts",
    "crm_user_identity_resolution_queue",
    "crm_user_identity",
    "sidebar_customer_profile_fields",
    "wecom_external_contact_identity_map",
    "wecom_external_contact_follow_users",
    "wecom_external_contact_event_logs",
    "contact_tags",
    "wecom_corp_tags",
    "wecom_corp_tag_groups",
    "group_chats",
    # group_chat_members 不在 PG schema 中（成员信息嵌入 group_chats.raw_payload）
    "people",
    "class_user_status_current",
    "class_user_status_history",
    # — user_ops
    "user_ops_send_records_next",
    "user_ops_huangxiaocan_activation_source",
    "user_ops_activation_status_source",
    "signup_tag_rules",
    "marketing_automation_question_rules",
    "marketing_automation_configs",
    "class_term_tag_mapping",
    # — 激活漏斗看板 (alembic 0010-0011)
    "user_ops_hxc_send_config",
    "user_ops_hxc_dashboard_snapshot",
    "user_ops_hxc_dashboard_meta",
    # — P1 group ops workspace drafts
    "group_ops_workspace_gray_window_approvals",
    "group_ops_workspace_allowlist_snapshots",
    "group_ops_workspace_governance_review_steps",
    "group_ops_workspace_governance_reviews",
    "group_ops_workspace_draft_audit_logs",
    "group_ops_workspace_draft_items",
    "group_ops_workspace_drafts",
    # — broadcast_jobs
    "broadcast_job_events",
    "broadcast_jobs",
    # — customer read model projection
    "customer_recent_message_next",
    "customer_timeline_event_next",
    "customer_detail_snapshot_next",
    "customer_list_index_next",
    "customer_read_model_refresh_source_receipt",
    "customer_read_model_refresh_intent",
    "customer_read_model_refresh_state",
    # — archive / system
    "archived_messages",
    "archive_sync_state",
    "sync_runs",
    "outbound_tasks",
    "outbound_webhook_deliveries",
    "outbound_event_outbox",
    "admin_operation_logs",
    "owner_migration_results",
    "owner_migration_previews",
    "owner_migration_import_sessions",
    "user_ops_import_batches",
    # customer_pulse_* / followup_orchestrator_* 表已经被 PR #232 删除——不再列入
    # truncate 清单（之前每个 test 跑 8 次注定失败的 SQL，刷 PG error log 还耗时）。
]


def _ensure_pg_url() -> str:
    url = os.environ.get("AICRM_TEST_DATABASE_URL", "").strip() or os.environ.get("DATABASE_URL", "").strip()
    if not url:
        pytest.skip(
            "PG required. Run: "
            "docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=test "
            "-e POSTGRES_USER=test -e POSTGRES_DB=test postgres:16; "
            "then DATABASE_URL=postgresql://test:test@localhost:5432/test pytest"
        )
    return url


# 缓存：session 起点 query 出 _TABLES_TO_TRUNCATE 中**真正存在**于当前 worker DB
# 的表名（按原顺序）。每个 test 起点拼成单条 ``TRUNCATE t1, t2, ... CASCADE`` 一次
# round-trip 全部清掉——之前是 N 张表 N 次 round-trip + 不存在表抛 ERROR + 单连接每
# test 重新建。从 ~250ms/test 降到 ~30ms/test。
_truncate_state: dict[str, Any] = {
    "url": "",
    "tables_sql": "",
    "conn": None,  # session-cached autocommit psycopg conn
}

_QUEUE_RUNTIME_RESET_SQL = """
WITH reset_control AS (
    UPDATE queue_runtime_control
    SET active_generation = 0,
        claim_enabled = FALSE,
        rollout_mode = 'standby',
        global_max_in_flight = 20,
        policy_version = 'queue-v2-test-loopback',
        external_claim_scope = 'test_loopback',
        updated_by = 'pytest-fixture',
        updated_reason = 'reset mutable queue control between tests',
        updated_at = CURRENT_TIMESTAMP
    WHERE singleton = TRUE
    RETURNING singleton
)
UPDATE queue_lane_policy
SET max_in_flight = CASE lane
        WHEN 'internal_general' THEN 4
        WHEN 'internal_financial' THEN 1
        WHEN 'webhook_inbox' THEN 4
        WHEN 'wecom_interactive' THEN 4
        WHEN 'wecom_bulk' THEN 1
        WHEN 'wecom_media' THEN 2
        WHEN 'outbound_webhook' THEN 4
        ELSE max_in_flight
    END,
    enabled = TRUE,
    rollout_mode = CASE WHEN lane = 'outbound_webhook' THEN 'blocked' ELSE 'standby' END,
    blocked_until = NULL,
    policy_version = 'queue-v2-test-loopback',
    updated_by = 'pytest-fixture',
    updated_reason = 'reset mutable queue lane policy between tests',
    updated_at = CURRENT_TIMESTAMP
WHERE EXISTS (SELECT 1 FROM reset_control)
"""


def _close_truncate_conn() -> None:
    conn = _truncate_state.pop("conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def _terminate_idle_test_transactions(url: str) -> None:
    parsed = urlparse(url)
    database_name = parsed.path.lstrip("/")
    if not database_name:
        return
    maintenance_url = urlunparse(parsed._replace(path="/postgres"))
    try:
        import psycopg
    except ImportError:  # pragma: no cover
        return
    conn = psycopg.connect(maintenance_url, autocommit=True)
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s
                  AND pid <> pg_backend_pid()
                  AND state = 'idle in transaction'
                """,
                (database_name,),
            )
        finally:
            cur.close()
    finally:
        conn.close()


def _truncate_cached_tables_once() -> None:
    url = _truncate_state.get("url") or os.environ.get("DATABASE_URL", "").strip()
    sql = _truncate_state.get("tables_sql", "")
    if not url or not sql:
        return
    try:
        import psycopg
    except ImportError:  # pragma: no cover
        return
    for attempt in range(2):
        conn = psycopg.connect(url, autocommit=True)
        try:
            cur = conn.cursor()
            try:
                cur.execute("SET lock_timeout = '5s'")
                cur.execute(sql)
                return
            except Exception:
                if attempt == 0:
                    _terminate_idle_test_transactions(url)
                    continue
                raise
            finally:
                cur.close()
        finally:
            conn.close()


def _run_next_alembic_upgrade(url: str) -> None:
    from scripts.ops.bootstrap_database import install_or_upgrade_database

    install_or_upgrade_database(url)


@pytest.fixture(scope="session", autouse=True)
def _ensure_schema_once():
    """Next/Alembic schema setup（每个 xdist worker 各跑一次）：

    1. 路由到 per-worker DB（``test_<worker_id>``，主进程仍用 base ``test``）
    2. 跑 Alembic migrations 到 head
    3. 缓存 ``_TABLES_TO_TRUNCATE`` 里**真正存在**的表名 → 后续 per-test
       truncate 一次性 ``TRUNCATE t1, t2, ...`` 单 SQL 跑完
    """
    url = _resolve_worker_database_url()
    if not url:
        yield
        return
    os.environ["AICRM_TEST_DATABASE_URL"] = url
    try:
        import psycopg
    except ImportError:  # pragma: no cover
        yield
        return
    _run_next_alembic_upgrade(url)

    # 过滤出真存在的表，拼成单条 TRUNCATE。原顺序保留没意义（CASCADE 会自动处理 FK），
    # 但 information_schema 查一次省得每 test 抛 N 个 "relation does not exist"。
    probe = psycopg.connect(url, autocommit=True)
    pcur = probe.cursor()
    placeholders = ", ".join(["%s"] * len(_TABLES_TO_TRUNCATE))
    pcur.execute(
        f"SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN ({placeholders})",
        tuple(_TABLES_TO_TRUNCATE),
    )
    existing = {row[0] for row in pcur.fetchall()}
    pcur.close()
    probe.close()
    ordered = [t for t in _TABLES_TO_TRUNCATE if t in existing]
    _truncate_state["url"] = url
    _truncate_state["tables_sql"] = f"TRUNCATE TABLE {', '.join(ordered)} RESTART IDENTITY CASCADE" if ordered else ""
    if _fixture_default_runtime_enabled():
        os.environ.pop("DATABASE_URL", None)
    yield
    _close_truncate_conn()


@pytest.fixture(autouse=True)
def _truncate_before_each_test():
    """每个 test 起点单条 TRUNCATE 清完所有缓存的表。

    覆盖**所有** test，不管它用顶层 ``app`` fixture 还是自己的 ``app`` fixture。
    复用 session 级别 autocommit 连接（建一次用一辈子）；只在断连后重建。
    """
    url = _truncate_state.get("url") or os.environ.get("DATABASE_URL", "").strip()
    sql = _truncate_state.get("tables_sql", "")
    if not url or not sql:
        yield
        return
    try:
        import psycopg
    except ImportError:  # pragma: no cover
        yield
        return
    conn = _truncate_state.get("conn")
    if conn is None or getattr(conn, "closed", True):
        conn = psycopg.connect(url, autocommit=True)
        cur = conn.cursor()
        try:
            cur.execute("SET lock_timeout = '5s'")
        except Exception:
            pass
        cur.close()
        _truncate_state["conn"] = conn
    cur = conn.cursor()
    try:
        cur.execute(sql)
        cur.execute(_QUEUE_RUNTIME_RESET_SQL)
    except Exception:
        # 断连 / 死锁 / 上个 test 遗留 idle transaction：弃旧连接，清 blocker 后重试。
        try:
            conn.close()
        except Exception:
            pass
        _truncate_state["conn"] = None
        _terminate_idle_test_transactions(url)
        retry_conn = psycopg.connect(url, autocommit=True)
        retry_cur = retry_conn.cursor()
        try:
            retry_cur.execute("SET lock_timeout = '5s'")
            retry_cur.execute(sql)
            retry_cur.execute(_QUEUE_RUNTIME_RESET_SQL)
            _truncate_state["conn"] = retry_conn
        except Exception:
            try:
                retry_conn.close()
            except Exception:
                pass
            raise
        finally:
            try:
                retry_cur.close()
            except Exception:
                pass
    finally:
        try:
            cur.close()
        except Exception:
            pass
    yield


@pytest.fixture
def next_pg_schema(monkeypatch):
    """Explicit opt-in for tests that require the Next/Alembic PG schema."""
    monkeypatch.setenv("DATABASE_URL", _ensure_pg_url())
    return None


@pytest.fixture
def composed_internal_event_registry():
    """Bind one isolated, fully composed consumer registry for a whole test."""
    from aicrm_next.internal_event_composition import build_internal_event_consumer_registry
    from aicrm_next.platform_foundation.internal_events import internal_event_consumer_registry_scope

    registry = build_internal_event_consumer_registry()
    with internal_event_consumer_registry_scope(registry):
        yield registry


@pytest.fixture
def next_app(monkeypatch, request):
    if _fixture_default_runtime_enabled():
        if "next_pg_schema" in request.fixturenames:
            monkeypatch.setenv("DATABASE_URL", _ensure_pg_url())
        else:
            monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    from aicrm_next.main import create_app

    return create_app()


@pytest.fixture
def next_client(next_app):
    from fastapi.testclient import TestClient

    return TestClient(next_app)


@pytest.fixture
def app(next_app):
    """Default app fixture is Next-native; legacy Flask tests must opt in."""
    return next_app


@pytest.fixture
def client(next_client):
    """Default client fixture is Next-native; legacy Flask tests must opt in."""
    return next_client
