from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from aicrm_next.extensions.ai.ai_audience_ops.agent_copywriting import generate_member_event_copywriting, render_template_text
from aicrm_next.shared.db_session import get_session_factory


def _session_factory():
    return get_session_factory()


def _ensure_agent_schema() -> None:
    statements = [
        "CREATE TABLE IF NOT EXISTS automation_agent_output (id BIGSERIAL PRIMARY KEY)",
        "CREATE TABLE IF NOT EXISTS automation_agent_llm_call_log (id BIGSERIAL PRIMARY KEY)",
        "ALTER TABLE automation_agent_config ADD COLUMN IF NOT EXISTS published_role_prompt TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_config ADD COLUMN IF NOT EXISTS published_task_prompt TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_config ADD COLUMN IF NOT EXISTS published_variables_json JSONB NOT NULL DEFAULT '[]'::jsonb",
        "ALTER TABLE automation_agent_config ADD COLUMN IF NOT EXISTS published_output_schema_json JSONB NOT NULL DEFAULT '[]'::jsonb",
        "ALTER TABLE automation_agent_config ADD COLUMN IF NOT EXISTS published_version INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE automation_agent_run ADD COLUMN IF NOT EXISTS run_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_run ADD COLUMN IF NOT EXISTS request_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_run ADD COLUMN IF NOT EXISTS batch_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_run ADD COLUMN IF NOT EXISTS userid TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_run ADD COLUMN IF NOT EXISTS unionid TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_run ADD COLUMN IF NOT EXISTS agent_code TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_run ADD COLUMN IF NOT EXISTS agent_type TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_run ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_run ADD COLUMN IF NOT EXISTS input_snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb",
        "ALTER TABLE automation_agent_run ADD COLUMN IF NOT EXISTS variables_snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb",
        "ALTER TABLE automation_agent_run ADD COLUMN IF NOT EXISTS final_prompt_preview TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_run ADD COLUMN IF NOT EXISTS role_prompt_version INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE automation_agent_run ADD COLUMN IF NOT EXISTS task_prompt_version INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE automation_agent_run ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_run ADD COLUMN IF NOT EXISTS error_code TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_run ADD COLUMN IF NOT EXISTS error_message TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_run ADD COLUMN IF NOT EXISTS latency_ms INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE automation_agent_run ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_run ADD COLUMN IF NOT EXISTS parent_run_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_run ADD COLUMN IF NOT EXISTS replay_of_run_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_run ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS output_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS run_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS request_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS userid TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS unionid TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS agent_code TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS output_type TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS raw_output_text TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS normalized_output_json JSONB NOT NULL DEFAULT '{}'::jsonb",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS rendered_output_text TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS target_agent_code TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS target_pool TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS confidence NUMERIC NOT NULL DEFAULT 0",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS reason TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS need_human_review BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS applied_status TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS adopted_by TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS adopted_action TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS outcome_status TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS outcome_value TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS revision_of_output_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS error_code TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_output ADD COLUMN IF NOT EXISTS error_message TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_llm_call_log ADD COLUMN IF NOT EXISTS run_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_llm_call_log ADD COLUMN IF NOT EXISTS agent_code TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_llm_call_log ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_llm_call_log ADD COLUMN IF NOT EXISTS model TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_llm_call_log ADD COLUMN IF NOT EXISTS model_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_llm_call_log ADD COLUMN IF NOT EXISTS request_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_llm_call_log ADD COLUMN IF NOT EXISTS prompt_hash TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_llm_call_log ADD COLUMN IF NOT EXISTS request_summary JSONB NOT NULL DEFAULT '{}'::jsonb",
        "ALTER TABLE automation_agent_llm_call_log ADD COLUMN IF NOT EXISTS response_summary JSONB NOT NULL DEFAULT '{}'::jsonb",
        "ALTER TABLE automation_agent_llm_call_log ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_llm_call_log ADD COLUMN IF NOT EXISTS latency_ms INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE automation_agent_llm_call_log ADD COLUMN IF NOT EXISTS error_code TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE automation_agent_llm_call_log ADD COLUMN IF NOT EXISTS error_message TEXT NOT NULL DEFAULT ''",
    ]
    with _session_factory()() as session:
        for statement in statements:
            session.execute(text(statement))
        session.commit()


def _seed_agent(agent_code: str = "ai_audience_agent", *, role_prompt: str = "你是私域运营助手", task_prompt: str = "需求={{questionnaire.answers.need}}") -> None:
    with _session_factory()() as session:
        session.execute(text("DELETE FROM automation_agent_output"))
        session.execute(text("DELETE FROM automation_agent_llm_call_log"))
        session.execute(text("DELETE FROM automation_agent_run"))
        session.execute(text("DELETE FROM automation_agent_config WHERE agent_code = :agent_code"), {"agent_code": agent_code})
        session.execute(
            text(
                """
                INSERT INTO automation_agent_config (
                    agent_code, display_name, enabled, published_role_prompt, published_task_prompt,
                    published_variables_json, published_output_schema_json, published_version
                )
                VALUES (:agent_code, :agent_code, TRUE, :role_prompt, :task_prompt, '[]'::jsonb, '[]'::jsonb, 1)
                """
            ),
            {"agent_code": agent_code, "role_prompt": role_prompt, "task_prompt": task_prompt},
        )
        session.commit()


def _package() -> dict:
    return {"id": 7, "package_key": "audience_pkg", "name": "问卷激活人群", "natural_language_definition": "提交问卷且已加微"}


def _member_event() -> dict:
    return {
        "id": 11,
        "package_id": 7,
        "event_type": "entered",
        "identity_type": "unionid",
        "identity_value": "union_agent_user",
        "unionid": "union_agent_user",
        "owner_userid": "HuangYouCan",
        "event_source_key": "questionnaire_submission:100",
        "payload_json": {"answers": {"need": "提升私域自动化转化"}, "tags": ["ai_audience"]},
    }


def _count(table: str) -> int:
    with _session_factory()() as session:
        try:
            return int(session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
        except Exception:
            session.rollback()
            return 0


def _latest(table: str) -> dict:
    with _session_factory()() as session:
        row = session.execute(text(f"SELECT * FROM {table} ORDER BY id DESC LIMIT 1")).mappings().fetchone()
        return dict(row or {})


def test_ai_audience_template_renderer_uses_payload_shortcuts() -> None:
    rendered, diagnostics, reason = render_template_text("需求={{need}} 用户={{member.unionid}}", {"payload": {"need": "转化"}, "member": {"unionid": "union_1"}})

    assert reason == ""
    assert rendered == "需求=转化 用户=union_1"
    assert diagnostics["template_variables_used"] == ["member.unionid", "need"]


@pytest.mark.usefixtures("next_pg_schema")
def test_ai_audience_agent_copywriting_writes_agent_audit_without_legacy_queue(monkeypatch) -> None:
    _ensure_agent_schema()
    monkeypatch.setenv("AICRM_AI_AUDIENCE_AGENT_MODE", "fake")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_AGENT_FAKE_ALLOWED", "1")
    _seed_agent(task_prompt="需求={{questionnaire.answers.need}} 人群={{audience.name}}")

    result = generate_member_event_copywriting(
        package=_package(),
        member_event=_member_event(),
        agent_code="ai_audience_agent",
        mock_output="根据你的问卷，我建议先完成激活配置。",
        attachments={"miniprogram_library_ids": [2]},
    )

    assert result["ok"] is True
    assert result["content"]["content_text"] == "根据你的问卷，我建议先完成激活配置。"
    assert result["content"]["attachments"]["miniprogram_library_ids"] == [2]
    assert result["diagnostics"]["fallback_used"] is False
    assert result["diagnostics"]["llm_call_logged"] is True
    assert result["diagnostics"]["questionnaire_answer_count"] == 1
    assert result["real_external_call_executed"] is False
    assert _count("automation_agent_run") == 1
    assert _count("automation_agent_output") == 1
    assert _count("automation_agent_llm_call_log") == 1
    assert _count("automation_task_plan_v2") == 0
    assert _count("broadcast_jobs") == 0

    run = _latest("automation_agent_run")
    assert run["source"] == "ai_audience_ops"
    assert run["agent_type"] == "ai_audience_ops"
    assert run["status"] == "completed"
    output = _latest("automation_agent_output")
    assert output["target_pool"] == "ai_audience_ops"
    assert output["applied_status"] == "generated"
    normalized = output["normalized_output_json"]
    if isinstance(normalized, str):
        normalized = json.loads(normalized)
    assert normalized["runtime_version"] == "ai_audience_ops"
    assert normalized["member_event_id"] == 11
    llm = _latest("automation_agent_llm_call_log")
    assert llm["prompt_hash"]
    assert llm["status"] == "completed"


@pytest.mark.usefixtures("next_pg_schema")
def test_ai_audience_agent_copywriting_falls_back_without_broadcast(monkeypatch) -> None:
    _ensure_agent_schema()
    monkeypatch.setenv("AICRM_AI_AUDIENCE_AGENT_MODE", "production")
    monkeypatch.delenv("AICRM_AI_AUDIENCE_AGENT_API_KEY", raising=False)
    monkeypatch.delenv("AICRM_RUNTIME_V2_AGENT_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    _seed_agent()

    result = generate_member_event_copywriting(
        package=_package(),
        member_event=_member_event(),
        agent_code="ai_audience_agent",
        fallback_content="兜底话术给 {{member.unionid}}",
    )

    assert result["ok"] is True
    assert result["content"]["fallback"] is True
    assert result["content"]["content_text"] == "兜底话术给 union_agent_user"
    assert result["diagnostics"]["fallback_used"] is True
    assert result["diagnostics"]["error_code"] == "agent_gateway_config_missing"
    assert _count("automation_agent_run") == 1
    assert _count("automation_agent_output") == 1
    assert _count("automation_agent_llm_call_log") == 1
    assert _count("automation_task_plan_v2") == 0
    assert _count("broadcast_jobs") == 0
    assert _latest("automation_agent_output")["applied_status"] == "fallback"


@pytest.mark.usefixtures("next_pg_schema")
def test_ai_audience_agent_copywriting_blocks_prompt_leak(monkeypatch) -> None:
    _ensure_agent_schema()
    monkeypatch.setenv("AICRM_AI_AUDIENCE_AGENT_MODE", "fake")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_AGENT_FAKE_ALLOWED", "1")
    task_prompt = "最终只输出一条可直接发送给用户的话术。不要输出 JSON。"
    _seed_agent(task_prompt=task_prompt)

    result = generate_member_event_copywriting(
        package=_package(),
        member_event=_member_event(),
        agent_code="ai_audience_agent",
        mock_output=task_prompt,
    )

    assert result["ok"] is False
    assert result["error"] == "agent_output_looks_like_prompt"
    assert _count("automation_agent_run") == 1
    assert _count("automation_agent_llm_call_log") == 1
    assert _count("automation_agent_output") == 0
    assert _count("broadcast_jobs") == 0


@pytest.mark.usefixtures("next_pg_schema")
def test_ai_audience_agent_copywriting_reports_missing_prompt_variable(monkeypatch) -> None:
    _ensure_agent_schema()
    monkeypatch.setenv("AICRM_AI_AUDIENCE_AGENT_MODE", "fake")
    monkeypatch.setenv("AICRM_AI_AUDIENCE_AGENT_FAKE_ALLOWED", "1")
    _seed_agent(task_prompt="缺失={{questionnaire.answers.missing_field}}")

    result = generate_member_event_copywriting(
        package=_package(),
        member_event=_member_event(),
        agent_code="ai_audience_agent",
        mock_output="不会执行",
    )

    assert result["ok"] is False
    assert result["error"] == "agent_prompt_variable_missing"
    assert result["diagnostics"]["missing_variables"] == ["questionnaire.answers.missing_field"]
    assert _count("automation_agent_run") == 1
    assert _count("automation_agent_llm_call_log") == 1
    assert _count("automation_agent_output") == 0
    assert _count("automation_task_plan_v2") == 0
    assert _count("broadcast_jobs") == 0
