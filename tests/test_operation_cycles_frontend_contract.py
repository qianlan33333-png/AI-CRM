from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "aicrm_next" / "admin_shell" / "templates" / "admin_shell"
STYLESHEET = ROOT / "aicrm_next" / "extensions" / "hxc" / "operation_cycles" / "static" / "operation_cycles.css"
DETAIL_SCRIPT = ROOT / "aicrm_next" / "extensions" / "hxc" / "operation_cycles" / "static" / "operation_cycles_detail.js"
AGENT_GUIDE = ROOT / "docs" / "operation_cycles" / "agent_usage_guide.md"
AGENT_ENTRY = ROOT / "AGENTS.md"


def _funnel() -> dict:
    return {
        "candidate_count": {"status": "observed", "value": 1275, "data_source": "identity audit"},
        "audited_count": {"status": "observed", "value": 895, "data_source": "decision snapshot"},
        "recommended_send_count": {"status": "observed", "value": 848, "data_source": "review snapshot"},
        "planned_target_count": {"status": "observed", "value": 848, "data_source": "plan snapshot"},
        "effective_sent_count": {"status": "observed", "value": 845, "data_source": "delivery fact"},
        "failed_count": {
            "status": "observed",
            "value": 3,
            "data_source": "delivery fact",
            "classification": "failed_retryable",
            "limitation": "生产状态 failed_retryable",
        },
    }


def _strategy() -> dict:
    return {
        "strategy_key": "monday_activation",
        "title": "每周一全量用户激活",
        "description": "对全量可运营用户完成审计、判断、发送与结果复盘。",
        "cadence": "每周一",
        "timezone": "Asia/Shanghai",
        "status": "active",
        "current_version": 2,
        "run_count": 1,
        "latest_run_key": "monday_activation_20260713",
        "latest_run_label": "2026-07-13 周一激活",
        "latest_run_at": "2026-07-13T09:00:00+08:00",
        "execution_stage": "postmortem",
        "review_status": "approved",
        "delivery_status": "completed",
        "data_status": "attribution_gap",
        "optimization_status": "pending_confirmation",
        "artifact_status": "partial",
        "funnel": _funnel(),
        "conclusion": "发送事实已形成，行为结果仍需补齐归因证据",
        "next_iteration_summary": "补齐追踪后验证召回内容差异。",
    }


def _run() -> dict:
    return {
        "run_key": "monday_activation_20260713",
        "strategy_key": "monday_activation",
        "label": "2026-07-13 周一激活",
        "objective": "验证全量用户激活闭环。",
        "started_at": "2026-07-13T09:00:00+08:00",
        "completed_at": None,
        "intended_send_at": "2026-07-13T12:00:00+08:00",
        "plan_scheduled_for": "2026-07-13T12:00:00+08:00",
        "plan_version": "plan-v1",
        "plan_status": "draft",
        "plan_source": "campaign plan aggregate",
        "first_sent_at": "2026-07-13T12:01:00+08:00",
        "last_sent_at": "2026-07-13T12:11:00+08:00",
        "execution_stage": "postmortem",
        "review_status": "approved",
        "delivery_status": "completed",
        "data_status": "attribution_gap",
        "optimization_status": "pending_confirmation",
        "artifact_status": "partial",
        "funnel": _funnel(),
        "conclusion": "845 条发送事实可核验，结果只作为观察信号。",
        "snapshot_revision": 3,
        "received_at": "2026-07-13T22:10:00+08:00",
        "fact_conflict": True,
    }


def _client(monkeypatch) -> TestClient:
    from aicrm_next.extensions.hxc.operation_cycles import admin_pages

    strategy = _strategy()
    run = _run()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "operation-cycles-frontend")
    monkeypatch.setattr(
        admin_pages,
        "_utc_now",
        lambda: datetime(2026, 7, 14, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    monkeypatch.setattr(
        admin_pages,
        "list_strategies",
        lambda **_: {"ok": True, "items": [strategy], "limit": 100, "offset": 0},
    )
    monkeypatch.setattr(
        admin_pages,
        "get_strategy",
        lambda key: (
            {
                "ok": True,
                "strategy": strategy,
                "versions": [
                    {
                        "version": 2,
                        "label": "v2 · 归因补强",
                        "objective": "补齐结果证据",
                        "definition": {},
                        "effective_from": "2026-07-13T00:00:00+08:00",
                    }
                ],
                "trend": [run],
                "sources": [
                    {
                        "reference_key": "broadcast-job:20260713",
                        "reference_type": "broadcast_job",
                        "label": "本轮发送聚合",
                        "source_system": "broadcast_jobs",
                        "source_id": "safe-plan-key",
                        "href": "/admin/broadcast-jobs?source_type=cloud_plan",
                        "evidence_hash": "a" * 64,
                        "data_status": "observed",
                    }
                ],
                "documents": {
                    "broadcast_details": {
                        "markdown": (
                            "# 本周群发结果\n\n"
                            "| 指标 | 数值 |\n| --- | ---: |\n| 有效发送 | 845 |\n\n"
                            "```chart\n"
                            '{"type":"funnel","title":"发送漏斗","unit":"人","labels":["候选","审计","发送"],'
                            '"series":[{"name":"人数","data":[1275,895,845]}]}\n'
                            "```"
                        ),
                        "generated_at": "2026-07-14T09:00:00+08:00",
                    },
                    "retrospective_details": {
                        "markdown": ("# 本周复盘明细\n\n## 结论\n\n发送事实可核验，行为归因仍需补齐。\n\n- 已完成：发送结果核验\n- 待解决：分窗口行为数据"),
                        "generated_at": "2026-07-14T09:05:00+08:00",
                    },
                    "execution_strategy": {
                        "markdown": "# 下周执行策略\n\n- [x] 完成人群审计\n- [ ] 确认下一轮优化",
                        "generated_at": "2026-07-14T09:10:00+08:00",
                    },
                },
                "assistant_plans": [
                    {
                        "reference_key": "ai-assistant-plan:monday-20260713",
                        "reference_type": "other",
                        "label": "2026-07-13 周一激活计划",
                        "source_system": "cloud_orchestrator_plan",
                        "source_id": "hxc-monday-20260713-plan",
                        "href": "/admin/cloud-orchestrator/plans/hxc-monday-20260713-plan",
                        "evidence_hash": "",
                        "data_status": "unknown",
                    }
                ],
            }
            if key == strategy["strategy_key"]
            else None
        ),
    )
    monkeypatch.setattr(
        admin_pages,
        "get_run",
        lambda key: (
            {
                "ok": True,
                "run": run,
                "attempts": [
                    {
                        "attempt_key": "attempt_blocked_0900",
                        "parent_attempt_key": None,
                        "status": "blocked",
                        "started_at": "2026-07-13T09:00:00+08:00",
                        "ended_at": "2026-07-13T09:05:00+08:00",
                        "blocked_reason": "正式模板和只读证据未齐备。",
                        "summary": {},
                    },
                    {
                        "attempt_key": "attempt_recovered_1130",
                        "parent_attempt_key": "attempt_blocked_0900",
                        "status": "completed",
                        "started_at": "2026-07-13T11:30:00+08:00",
                        "ended_at": "2026-07-13T22:00:00+08:00",
                        "blocked_reason": "",
                        "summary": {"summary": "恢复后完成发送与早期复盘。"},
                    },
                ],
                "stages": [
                    {
                        "stage_key": "delivery",
                        "attempt_key": "attempt_recovered_1130",
                        "stage": "delivery",
                        "status": "completed",
                        "started_at": "2026-07-13T12:00:00+08:00",
                        "ended_at": "2026-07-13T12:11:00+08:00",
                        "blocked_reason": "",
                        "summary": {"summary": "发送事实已核验。"},
                    }
                ],
                "metrics": [
                    {
                        "metric_key": "active_message_count_24h",
                        "label": "主动消息",
                        "numerator": 14,
                        "denominator": 845,
                        "value": 14,
                        "unit": "人",
                        "observation_window": "T+24h",
                        "data_source": "message event aggregate",
                        "data_quality": "partial",
                        "limitations": ["只能作为下限观察"],
                        "is_causal": False,
                        "value_status": "partial_lower_bound",
                    },
                    {
                        "metric_key": "target_behavior_72h",
                        "label": "目标行为",
                        "numerator": None,
                        "denominator": None,
                        "value": None,
                        "unit": "人",
                        "observation_window": "T+72h",
                        "data_source": "tracking aggregate",
                        "data_quality": "instrumentation_missing",
                        "limitations": ["埋点缺失"],
                        "is_causal": False,
                        "value_status": "instrumentation_missing",
                    },
                ],
                "retrospective": {
                    "conclusion": "第一轮从决策推进到可核验发送事实。",
                    "observations": ["实际发送分母已核验"],
                    "limitations": ["行为归因仍不完整"],
                    "data_conflicts": ["计划状态与发送事实冲突"],
                },
                "next_iteration": {
                    "summary": "先补齐追踪，再验证内容差异。",
                    "hypothesis": "更完整的追踪可以缩小归因缺口。",
                    "actions": ["补齐行为追踪", "统一计划与发送状态"],
                    "status": "pending_confirmation",
                    "confirmation_note": "",
                    "applied_strategy_version": None,
                },
                "references": [
                    {
                        "reference_key": "broadcast_job:20260713",
                        "reference_type": "broadcast_job",
                        "label": "Broadcast Jobs",
                        "source_system": "aicrm",
                        "source_id": "batch-20260713",
                        "href": "/admin/broadcast-jobs?batch=20260713",
                        "evidence_hash": "sha256:safe-aggregate",
                        "data_status": "available",
                    }
                ],
                "snapshot": {
                    "report_id": "report-20260713",
                    "snapshot_revision": 3,
                    "snapshot_hash": "sha256:safe-snapshot",
                    "schema_version": "operation_cycle_snapshot.v1",
                    "reporter_id": "ops-reporter",
                },
            }
            if key == run["run_key"]
            else None
        ),
    )
    return TestClient(create_app(), raise_server_exceptions=False)


def test_operation_cycle_templates_are_readonly_and_use_three_level_ia() -> None:
    sources = {
        path.name: path.read_text(encoding="utf-8")
        for path in (
            TEMPLATE_DIR / "operation_cycles_list.html",
            TEMPLATE_DIR / "operation_cycles_strategy.html",
            TEMPLATE_DIR / "operation_cycles_run.html",
        )
    }

    assert 'data-operation-cycle-surface="strategy-list"' in sources["operation_cycles_list.html"]
    assert 'data-operation-cycle-surface="strategy-detail"' in sources["operation_cycles_strategy.html"]
    assert 'data-operation-cycle-surface="run-detail"' in sources["operation_cycles_run.html"]
    assert "operation-cycle-detail-nav" in sources["operation_cycles_strategy.html"]
    assert "active_document" in sources["operation_cycles_strategy.html"]
    assert "operationCycleAssistantPlans" in sources["operation_cycles_strategy.html"]
    for index in range(1, 9):
        assert f'id="cycle-step-{index}"' in sources["operation_cycles_run.html"]
    for source in sources.values():
        assert 'data-readonly="true"' in source
        assert "<form" not in source
        assert 'method="post"' not in source.lower()
    assert sources["operation_cycles_list.html"].count("<button") == 2
    assert sources["operation_cycles_list.html"].count('disabled aria-disabled="true"') == 2
    assert "<button" not in sources["operation_cycles_strategy.html"]
    assert "<button" not in sources["operation_cycles_run.html"]


def test_operation_cycle_agent_guide_is_discoverable_and_covers_the_report_contract() -> None:
    guide = AGENT_GUIDE.read_text(encoding="utf-8")
    entry = AGENT_ENTRY.read_text(encoding="utf-8")

    assert "docs/operation_cycles/agent_usage_guide.md" in entry
    for document_key in ("broadcast_details", "retrospective_details", "execution_strategy"):
        assert f"`{document_key}`" in guide
    for required_contract in (
        "POST /api/operation-cycles/reports",
        "operation_cycle_snapshot.v1",
        "operation_cycle_report_write",
        "external_effects",
        "Idempotency-Key",
        "历史发送记录",
        "cloud_orchestrator_plan",
        "is_causal=false",
    ):
        assert required_contract in guide


def test_operation_cycle_pages_render_dense_readonly_evidence(monkeypatch) -> None:
    client = _client(monkeypatch)

    listing = client.get("/admin/operation-cycles")
    strategy = client.get("/admin/operation-cycles/monday_activation")
    run = client.get("/admin/operation-cycles/monday_activation/runs/monday_activation_20260713")
    stylesheet = client.get("/static/operation-cycles/operation_cycles.css")
    detail_script = client.get("/static/operation-cycles/operation_cycles_detail.js")

    assert listing.status_code == strategy.status_code == run.status_code == 200
    assert stylesheet.status_code == detail_script.status_code == 200
    assert ".operation-cycle-step" in stylesheet.text
    assert "/static/operation-cycles/operation_cycles.css?v=20260715-v1" in listing.text
    assert "每周一全量用户激活" in listing.text
    assert "任务列表" in listing.text
    assert "本周进度" in listing.text
    assert "任务准备" in listing.text
    assert "发送执行" in listing.text
    assert "结果复盘" in listing.text
    assert "优化确认" in listing.text
    assert "暂停" in listing.text
    assert "删除" in listing.text
    assert "明细数据" in listing.text
    assert "已复盘，待确认优化" not in listing.text
    assert "07/13–07/19" not in listing.text
    assert "1275" not in listing.text
    assert "归因缺口" not in listing.text
    assert 'href="/admin/operation-cycles/monday_activation"' in listing.text

    assert "已循环轮次" in strategy.text
    assert "本次循环进度" in strategy.text
    assert "本周发送数据" in strategy.text
    assert "本周复盘明细" in strategy.text
    assert "下周执行策略" in strategy.text
    assert "历史发送记录" in strategy.text
    assert "本周群发结果" in strategy.text
    assert "有效发送" in strategy.text
    assert "data-operation-cycle-chart=" in strategy.text
    assert "状态与核心漏斗" not in strategy.text
    assert "历次运行对照" not in strategy.text
    assert "版本记录" not in strategy.text
    assert 'href="/admin/operation-cycles/monday_activation?section=execution_strategy"' in strategy.text.replace("&amp;", "&")

    retrospective = client.get("/admin/operation-cycles/monday_activation?section=retrospective_details")
    assert retrospective.status_code == 200
    assert "发送事实可核验" in retrospective.text
    assert 'data-operation-cycle-markdown="retrospective_details"' in retrospective.text

    execution_strategy = client.get("/admin/operation-cycles/monday_activation?section=execution_strategy")
    assert execution_strategy.status_code == 200
    assert "下周执行策略" in execution_strategy.text
    assert 'type="checkbox" disabled' in execution_strategy.text

    history = client.get("/admin/operation-cycles/monday_activation?section=history")
    assert history.status_code == 200
    assert "历史发送记录" in history.text
    assert "hxc-monday-20260713-plan" in history.text
    assert "data-operation-cycle-plan-history" in history.text
    assert "/api/admin/cloud-orchestrator/plans/" in detail_script.text

    for label in ("任务目标与时间", "前置检查与执行尝试", "人群分层与去留判断", "人审与计划版本", "实际发送事实", "分窗口结果", "结果复盘与限制", "下一轮优化"):
        assert label in run.text
    assert "事实待核对" in run.text
    assert "draft" in run.text
    assert "campaign plan aggregate" in run.text
    assert "T+24h" in run.text
    assert "下限" in run.text
    assert "埋点缺失" in run.text
    assert "观察信号 · 不作为因果提升结论" in run.text
    assert re.search(r"可重试失败.*?3", run.text, re.DOTALL)
    assert 'href="/admin/broadcast-jobs?batch=20260713"' in run.text.replace("&amp;", "&")
    assert "<button" not in run.text


def test_operation_cycle_weekly_progress_resets_outside_current_week() -> None:
    from aicrm_next.extensions.hxc.operation_cycles.admin_pages import _strategy_summaries

    payload = {"items": [_strategy()]}
    current_week = _strategy_summaries(
        payload,
        now=datetime(2026, 7, 14, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )[0]["weekly_progress"]
    next_week = _strategy_summaries(
        payload,
        now=datetime(2026, 7, 20, 0, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
    )[0]["weekly_progress"]

    assert [step["state"] for step in current_week["steps"]] == [
        "completed",
        "completed",
        "completed",
        "active",
    ]
    assert {step["state"] for step in next_week["steps"]} == {"pending"}
    assert "summary" not in current_week
    assert "week_label" not in current_week


def test_operation_cycle_empty_state_never_presents_missing_as_zero(monkeypatch) -> None:
    from aicrm_next.extensions.hxc.operation_cycles import admin_pages

    client = _client(monkeypatch)
    monkeypatch.setattr(
        admin_pages,
        "list_strategies",
        lambda **_: {"ok": True, "items": [], "limit": 100, "offset": 0},
    )

    response = client.get("/admin/operation-cycles")

    assert response.status_code == 200
    assert "暂无运营任务" in response.text
    assert "Agent 创建并上报任务后会显示在这里" in response.text


def test_operation_cycle_styles_are_namespaced_and_responsive() -> None:
    css = STYLESHEET.read_text(encoding="utf-8")
    marker = "/* Operation cycles · read-only execution ledger */"
    operation_cycle_css = css[css.index(marker) :]

    assert ".operation-cycle-ledger" in operation_cycle_css
    assert ".operation-cycle-funnel" in operation_cycle_css
    assert ".operation-cycle-task-table" in operation_cycle_css
    assert ".operation-cycle-progress-track" in operation_cycle_css
    assert ".operation-cycle-task-button:disabled" in operation_cycle_css
    assert ".operation-cycle-step-index" in operation_cycle_css
    assert ".operation-cycle-risk-note" in operation_cycle_css
    assert "@media (max-width: 760px)" in operation_cycle_css
    assert "@media (prefers-reduced-motion: reduce)" in operation_cycle_css
    assert "--operation-cycle-amber" in operation_cycle_css
    base = (TEMPLATE_DIR / "operation_cycles_base.html").read_text(encoding="utf-8")
    assert "/static/operation-cycles/operation_cycles.css?v=20260715-v1" in base


def test_operation_cycle_templates_do_not_name_sensitive_identity_fields() -> None:
    combined = "\n".join(
        (TEMPLATE_DIR / name).read_text(encoding="utf-8")
        for name in (
            "operation_cycles_list.html",
            "operation_cycles_strategy.html",
            "operation_cycles_run.html",
            "operation_cycles_macros.html",
        )
    ).lower()

    for forbidden in ("external_userid", "unionid", "openid", "手机号", "原始消息", "authorization: bearer"):
        assert forbidden not in combined
