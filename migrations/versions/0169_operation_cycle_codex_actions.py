"""Add versioned operation skills and local Codex action requests.

Revision ID: 0169_operation_cycle_codex_actions
Revises: 0168_lead_qr_copy_config
"""

from __future__ import annotations

import hashlib
import json

from alembic import op
from sqlalchemy import text


revision = "0169_operation_cycle_codex_actions"
down_revision = "0168_lead_qr_copy_config"
branch_labels = None
depends_on = None


def _pilot_skill() -> tuple[dict, str]:
    skill = {
        "schema_version": "operation_cycle_skill.v1",
        "skill_key": "hxc_monday_broadcast.v1",
        "actions": [
            {
                "action_key": "prepare_broadcast",
                "title": "启动周一群发准备",
                "objective": "基于当下真实母集和跟进关系生成经人工确认的群发明细，并只通过 Campaign preparation 提交待审核计划。",
                "codex_prompt": (
                    "你正在执行周一群发准备。先读取任务附带的已确认策略、执行指南、话术指南和度量指南，"
                    "再从本机逻辑绑定 hxc_knowledge_vault、huangyoucan_data、excel_workspace 读取正式来源。"
                    "必须重新查询当下真实 HuangYouCan 母集和实时跟进关系，不得复用历史名单；完成 ABCD 分层、逐层话术和本地发送明细 Excel。"
                    "人与 Codex 可以反复修改；在人明确确认 Excel 前，不得向 CRM 创建 preparation、AI 助手计划或发送任务。"
                    "确认后使用独立 campaign_agent 身份调用现有 preparation/create 与 commit，md_source_hash 必须等于最终 Excel 文件 SHA-256；"
                    "preparation 有任何 blocker 时不得 commit。commit 回执必须是 review_status=pending_review、run_status=draft、broadcast_jobs=0。"
                    "最后只提交人数汇总、ABCD 分层汇总、Excel SHA-256、preparation_id、plan_id 和 AI 助手链接；"
                    "不得提交个人标识、本地路径、Excel 内容、凭据或原始对话。真实发送只能由人到 AI 助手点击确认并发送。"
                ),
                "required_local_bindings": [
                    "excel_workspace",
                    "huangyoucan_data",
                    "hxc_knowledge_vault",
                ],
                "completion_type": "campaign_preparation_commit",
                "prerequisites": [],
                "result_schema": {
                    "schema_version": "operation_cycle_action_result.v1",
                    "required": [
                        "conclusion",
                        "total_count",
                        "segment_counts",
                        "excel_sha256",
                        "preparation_id",
                        "plan_id",
                    ],
                },
            },
            {
                "action_key": "post_send_review",
                "title": "启动发送后复盘",
                "objective": "读取聚合发送终态，形成复盘结论并按需提交下一版正式 Skill 提案。",
                "codex_prompt": (
                    "这是独立的发送后复盘任务。只读取 CRM 提供的聚合发送终态、已确认策略和本机逻辑绑定 hxc_knowledge_vault；"
                    "不得读取或回传逐人发送明细。核对有效发送数、失败数、限制和观察窗口，形成复盘结论。"
                    "如果需要调整下一轮做法，通过现有 operation_cycle_strategy_change_proposal.v1 提交完整目标版本，"
                    "其中 operation_skill 必须是完整 operation_cycle_skill.v1；正式 Skill 只有管理员人工确认后生效。"
                    "最终只提交聚合发送数、聚合失败数、复盘结论和可选 proposal_id/Skill 哈希，不得提交本地路径、凭据或原始对话。"
                ),
                "required_local_bindings": ["hxc_knowledge_vault"],
                "completion_type": "operation_cycle_review",
                "prerequisites": ["prepare_broadcast"],
                "result_schema": {
                    "schema_version": "operation_cycle_action_result.v1",
                    "required": ["conclusion", "sent_count", "failed_count"],
                },
            },
        ],
        "result_schema": {
            "allowed_completion_actions": ["prepare_broadcast", "post_send_review"]
        },
        "safety": {
            "schema_version": "operation_cycle_skill_safety.v1",
            "crm_stores_intermediate_artifacts": False,
            "crm_stores_local_paths": False,
            "crm_stores_raw_conversation": False,
            "start_external_effects": "none",
            "send_requires_ai_assistant_approval": True,
            "auto_approve_allowed": False,
            "direct_broadcast_jobs_allowed": False,
        },
    }
    canonical = json.dumps(skill, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    skill_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    skill["skill_hash"] = skill_hash
    return skill, skill_hash


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE operation_cycle_strategy_versions
        ADD COLUMN IF NOT EXISTS operation_skill_json JSONB NOT NULL DEFAULT '{}'::jsonb
            CHECK (jsonb_typeof(operation_skill_json) = 'object'),
        ADD COLUMN IF NOT EXISTS operation_skill_hash TEXT NOT NULL DEFAULT ''
            CHECK (operation_skill_hash = '' OR length(operation_skill_hash) = 64)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS operation_cycle_runners (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'aicrm',
            runner_id TEXT NOT NULL,
            principal_id TEXT NOT NULL DEFAULT '',
            connector_version TEXT NOT NULL,
            codex_version TEXT NOT NULL,
            app_server_protocol TEXT NOT NULL
                CHECK (app_server_protocol = 'codex_app_server_jsonrpc_v2'),
            compatibility_status TEXT NOT NULL
                CHECK (compatibility_status IN ('ready','incompatible','unavailable')),
            binding_keys_json JSONB NOT NULL DEFAULT '[]'::jsonb
                CHECK (jsonb_typeof(binding_keys_json) = 'array'),
            max_concurrency INTEGER NOT NULL DEFAULT 1 CHECK (max_concurrency = 1),
            last_heartbeat_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_operation_cycle_runner UNIQUE (tenant_id, runner_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_operation_cycle_runners_heartbeat "
        "ON operation_cycle_runners (tenant_id, last_heartbeat_at DESC)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS operation_cycle_action_requests (
            request_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'aicrm',
            strategy_key TEXT NOT NULL,
            run_key TEXT NOT NULL,
            action_key TEXT NOT NULL,
            action_title TEXT NOT NULL,
            strategy_version INTEGER NOT NULL CHECK (strategy_version > 0),
            context_hash TEXT NOT NULL CHECK (length(context_hash) = 64),
            skill_key TEXT NOT NULL,
            skill_hash TEXT NOT NULL CHECK (length(skill_hash) = 64),
            runner_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','claimed','thread_bound','turn_started','completed','failed')),
            idempotency_key TEXT NOT NULL,
            request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
            parent_request_id TEXT REFERENCES operation_cycle_action_requests(request_id),
            thread_id TEXT NOT NULL DEFAULT '',
            turn_id TEXT NOT NULL DEFAULT '',
            lease_token_hash TEXT NOT NULL DEFAULT '',
            lease_expires_at TIMESTAMPTZ,
            final_result_json JSONB,
            failure_code TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL DEFAULT '',
            claimed_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_operation_cycle_action_idempotency UNIQUE (tenant_id, idempotency_key)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_operation_cycle_strategy_active_action "
        "ON operation_cycle_action_requests (tenant_id, strategy_key) "
        "WHERE status IN ('queued','claimed','thread_bound','turn_started')"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_operation_cycle_action_claim "
        "ON operation_cycle_action_requests (tenant_id, runner_id, status, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_operation_cycle_action_history "
        "ON operation_cycle_action_requests (tenant_id, strategy_key, created_at DESC)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS operation_cycle_action_request_events (
            id BIGSERIAL PRIMARY KEY,
            request_id TEXT NOT NULL
                REFERENCES operation_cycle_action_requests(request_id) ON DELETE CASCADE,
            event_id TEXT NOT NULL,
            event_type TEXT NOT NULL
                CHECK (event_type IN ('thread_bound','turn_started','completed','failed')),
            payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
            payload_json JSONB NOT NULL CHECK (jsonb_typeof(payload_json) = 'object'),
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_operation_cycle_action_event UNIQUE (request_id, event_id)
        )
        """
    )

    skill, skill_hash = _pilot_skill()
    op.get_bind().execute(
        text(
            """
            UPDATE operation_cycle_strategy_versions version
            SET operation_skill_json = CAST(:skill_json AS jsonb),
                operation_skill_hash = :skill_hash
            FROM operation_cycle_strategies strategy
            WHERE version.strategy_id = strategy.id
              AND strategy.tenant_id = 'aicrm'
              AND strategy.strategy_key = 'hxc_monday_full_activation'
              AND version.version = strategy.current_version
              AND version.operation_skill_hash = ''
            """
        ),
        {
            "skill_json": json.dumps(skill, ensure_ascii=False, sort_keys=True),
            "skill_hash": skill_hash,
        },
    )


def downgrade() -> None:
    # Rollback is feature-flag and connector shutdown only. Keep immutable action
    # requests, hashes and runner audit metadata for incident reconstruction.
    pass
