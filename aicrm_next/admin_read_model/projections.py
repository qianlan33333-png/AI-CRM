from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from aicrm_next.shared.release import current_release_sha
from aicrm_next.shared.runtime_settings import runtime_setting

from .repo import AdminReadRepository


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _base_payload(repo: AdminReadRepository, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "degraded": False,
        "error_code": "",
        "page_error": "",
        "diagnostics": {"source_status": repo.source_status},
        "source_status": repo.source_status,
        **payload,
    }


def _local_rows(prefix: str, count: int = 1) -> list[dict[str, Any]]:
    return [
        {
            "id": f"{prefix}_contract_{index + 1}",
            "name": f"{prefix} contract probe {index + 1}",
            "status": "available",
            "updated_at": _now_iso(),
        }
        for index in range(count)
    ]


def ai_assistant_payload(repo: AdminReadRepository) -> dict[str, Any]:
    config_count = repo.count("automation_agent_config")
    run_count = repo.count("automation_agent_run")
    output_count = repo.count("automation_agent_output")
    llm_call_count = repo.count("automation_agent_llm_call_log")
    configs = repo.rows(
        """
        SELECT id, agent_code, display_name, scenario_code, enabled, updated_at
        FROM automation_agent_config
        ORDER BY updated_at DESC, id DESC
        LIMIT 10
        """
    )
    runs = repo.rows(
        """
        SELECT id, run_id, agent_code, status, unionid, created_at, updated_at
        FROM automation_agent_run
        ORDER BY created_at DESC, id DESC
        LIMIT 10
        """
    )
    outputs = repo.rows(
        """
        SELECT id, output_id, run_id, agent_code, output_type, applied_status, created_at
        FROM automation_agent_output
        ORDER BY created_at DESC, id DESC
        LIMIT 10
        """
    )
    calls = repo.rows(
        """
        SELECT id, agent_code, model_name, status, latency_ms, created_at
        FROM automation_agent_llm_call_log
        ORDER BY created_at DESC, id DESC
        LIMIT 10
        """
    )
    if not repo.is_production and not configs and not runs:
        configs = _local_rows("agent_config")
    cards = [
        {"label": "Agent 配置", "value": config_count or len(configs), "description": "automation_agent_config"},
        {"label": "最近运行", "value": run_count or len(runs), "description": "automation_agent_run"},
        {"label": "最近输出", "value": output_count or len(outputs), "description": "automation_agent_output"},
        {"label": "LLM 调用", "value": llm_call_count or len(calls), "description": "automation_agent_llm_call_log"},
    ]
    return _base_payload(
        repo,
        {
            "cards": cards,
            "sections": [
                {"title": "Agent 配置", "headers": ["agent_code", "名称", "场景", "启用", "更新时间"], "rows": [[r.get("agent_code") or r.get("id"), r.get("display_name") or r.get("name"), r.get("scenario_code") or "-", r.get("enabled", r.get("status")), r.get("updated_at")] for r in configs]},
                {"title": "最近运行", "headers": ["run_id", "agent_code", "状态", "客户", "时间"], "rows": [[r.get("run_id") or r.get("id"), r.get("agent_code"), r.get("status"), r.get("unionid"), r.get("created_at")] for r in runs]},
                {"title": "最近输出", "headers": ["output_id", "run_id", "agent_code", "状态", "时间"], "rows": [[r.get("output_id") or r.get("id"), r.get("run_id"), r.get("agent_code"), r.get("applied_status"), r.get("created_at")] for r in outputs]},
                {"title": "LLM 调用", "headers": ["agent_code", "模型", "状态", "延迟", "时间"], "rows": [[r.get("agent_code"), r.get("model_name"), r.get("status"), r.get("latency_ms"), r.get("created_at")] for r in calls]},
            ],
        },
    )


def funnel_payload(repo: AdminReadRepository) -> dict[str, Any]:
    counts = {
        "客户总数": repo.count("wecom_external_contact_identity_map"),
        "问卷提交": repo.count("questionnaire_submissions"),
        "订单数": repo.count("wechat_pay_orders"),
        "AI 人群包成员": repo.count("ai_audience_member_current"),
        "内部事件": repo.count("internal_event"),
        "外推任务": repo.count("external_effect_job"),
    }
    if not repo.is_production:
        counts = {"客户总数": 1, "问卷提交": 1, "订单数": 1, "AI 人群包成员": 1, "内部事件": 1, "外推任务": 1}
    cards = [{"label": key, "value": value, "description": "生产统计" if repo.is_production else "本地结构校验"} for key, value in counts.items()]
    recent_contacts = repo.rows(
        """
        SELECT
            im.external_userid,
            COALESCE(NULLIF(im.name, ''), NULLIF(fu.remark, ''), im.external_userid) AS name,
            COALESCE(NULLIF(fu.user_id, ''), NULLIF(im.follow_user_userid, '')) AS owner_userid,
            im.updated_at
        FROM wecom_external_contact_identity_map im
        LEFT JOIN wecom_external_contact_follow_users fu
          ON fu.corp_id = im.corp_id
         AND fu.external_userid = im.external_userid
         AND COALESCE(fu.relation_status, 'active') = 'active'
        ORDER BY im.updated_at DESC, im.id DESC LIMIT 10
        """
    )
    recent_submissions = repo.rows(
        """
        SELECT qs.unionid, COALESCE(NULLIF(identity.primary_external_userid, ''), qs.unionid) AS customer_identity,
               qs.total_score, qs.submitted_at
        FROM questionnaire_submissions qs
        LEFT JOIN crm_user_identity identity ON identity.unionid = qs.unionid
        ORDER BY qs.submitted_at DESC, qs.id DESC LIMIT 10
        """
    )
    rows = [[r.get("external_userid"), r.get("name"), r.get("owner_userid"), r.get("updated_at")] for r in recent_contacts]
    rows += [[r.get("unionid"), r.get("customer_identity"), r.get("total_score"), r.get("submitted_at")] for r in recent_submissions]
    if not rows and not repo.is_production:
        rows = [["local_contract_contact", "本地结构校验", "system", _now_iso()]]
    return _base_payload(
        repo,
        {"cards": cards, "sections": [{"title": "最近客户 / 问卷事件", "headers": ["标识", "名称/客户", "负责人/分数", "时间"], "rows": rows}]},
    )


def wecom_tags_payload(repo: AdminReadRepository) -> dict[str, Any]:
    rows = repo.rows(
        """
        SELECT tag_id, COALESCE(NULLIF(tag_name, ''), tag_id) AS tag_name, count(*) AS usage_count, max(created_at) AS updated_at
        FROM contact_tags
        GROUP BY tag_id, tag_name
        ORDER BY usage_count DESC, updated_at DESC
        LIMIT 50
        """
    )
    cards = [
        {"label": "本地标签缓存", "value": len(rows), "description": "contact_tags distinct tag_id"},
        {"label": "标签使用记录", "value": sum(int(row.get("usage_count") or 0) for row in rows), "description": "contact_tags rows"},
        {"label": "远程同步", "value": "需配置" if not rows else "有缓存", "description": "远程企微失败时仍展示本地缓存"},
    ]
    if not rows and not repo.is_production:
        rows = [{"tag_id": "local_contract_tag", "tag_name": "本地缓存结构校验", "usage_count": 1, "updated_at": _now_iso()}]
    return _base_payload(
        repo,
        {
            "cards": cards,
            "sections": [{"title": "标签缓存", "headers": ["tag_id", "标签名", "使用人数", "最近同步/写入"], "rows": [[r.get("tag_id"), r.get("tag_name"), r.get("usage_count"), r.get("updated_at")] for r in rows]}],
            "empty_note": "生产数据为空：本地 contact_tags 暂无标签缓存，请检查企微标签同步配置和最近同步错误。",
        },
    )


def products_payload(repo: AdminReadRepository) -> dict[str, Any]:
    rows = repo.rows(
        """
        SELECT p.id, p.product_code, p.name, p.amount_total, p.currency, p.status, p.enabled,
               p.created_at, p.updated_at, count(s.id) AS slice_count
        FROM wechat_pay_products p
        LEFT JOIN wechat_pay_product_page_slices s ON s.product_id = p.id AND s.enabled = TRUE
        GROUP BY p.id
        ORDER BY p.updated_at DESC, p.id DESC
        LIMIT 100
        """
    )
    if not rows and not repo.is_production:
        rows = [{"product_code": "local_contract_product", "name": "本地结构校验商品", "amount_total": 1, "currency": "CNY", "status": "available", "enabled": True, "slice_count": 1, "updated_at": _now_iso()}]
    return _base_payload(
        repo,
        {
            "cards": [{"label": "商品数量", "value": len(rows), "description": "wechat_pay_products"}],
            "sections": [{"title": "商品列表", "headers": ["编码", "名称", "价格", "状态", "页面切片", "更新时间"], "rows": [[r.get("product_code"), r.get("name"), f"{int(r.get('amount_total') or 0) / 100:.2f} {r.get('currency') or 'CNY'}", r.get("status") or r.get("enabled"), r.get("slice_count"), r.get("updated_at")] for r in rows]}],
        },
    )


def transactions_payload(repo: AdminReadRepository) -> dict[str, Any]:
    rows = repo.rows(
        """
        SELECT o.out_trade_no, o.transaction_id,
               COALESCE(NULLIF(o.payer_name_snapshot, ''), NULLIF(identity.primary_external_userid, ''), o.unionid) AS customer,
               product_name, product_code, amount_total, currency, status, trade_state, created_at
        FROM wechat_pay_orders o
        LEFT JOIN crm_user_identity identity ON identity.unionid = o.unionid
        ORDER BY o.created_at DESC, o.id DESC
        LIMIT 50
        """
    )
    if not rows and not repo.is_production:
        rows = [{"out_trade_no": "local_contract_order", "transaction_id": "local_contract_txn", "customer": "本地结构校验", "product_name": "结构校验商品", "product_code": "local_contract_product", "amount_total": 1, "currency": "CNY", "status": "available", "trade_state": "", "created_at": _now_iso()}]
    return _base_payload(
        repo,
        {
            "cards": [{"label": "交易订单", "value": len(rows), "description": "wechat_pay_orders"}],
            "sections": [{"title": "交易列表", "headers": ["创建时间", "商户单号", "微信单号", "客户", "商品", "金额", "状态"], "rows": [[r.get("created_at"), r.get("out_trade_no"), r.get("transaction_id") or "-", r.get("customer") or "-", f"{r.get('product_name') or ''} / {r.get('product_code') or ''}", f"{int(r.get('amount_total') or 0) / 100:.2f} {r.get('currency') or 'CNY'}", r.get("status") or r.get("trade_state")] for r in rows]}],
        },
    )


def media_payload(repo: AdminReadRepository, kind: str) -> dict[str, Any]:
    if kind == "image":
        rows = repo.rows("SELECT id, name, file_name, category, tags, enabled, updated_at FROM image_library ORDER BY updated_at DESC, id DESC LIMIT 100")
        headers = ["ID", "名称", "文件", "分类", "标签", "状态", "更新时间"]
        table_rows = [[r.get("id"), r.get("name"), r.get("file_name"), r.get("category") or "-", r.get("tags") or [], "启用" if r.get("enabled") else "停用", r.get("updated_at")] for r in rows]
        label = "图片素材"
    elif kind == "miniprogram":
        rows = repo.rows("SELECT id, COALESCE(NULLIF(title, ''), name) AS title, appid, pagepath, enabled, updated_at FROM miniprogram_library ORDER BY updated_at DESC, id DESC LIMIT 100")
        headers = ["ID", "标题", "appid", "页面路径", "状态", "更新时间"]
        table_rows = [[r.get("id"), r.get("title"), r.get("appid"), r.get("pagepath"), "启用" if r.get("enabled") else "停用", r.get("updated_at")] for r in rows]
        label = "小程序素材"
    else:
        rows = repo.rows("SELECT id, name, file_name, mime_type, file_size, tags, enabled, updated_at FROM attachment_library ORDER BY updated_at DESC, id DESC LIMIT 100")
        headers = ["ID", "名称", "文件", "类型", "大小", "标签", "状态", "更新时间"]
        table_rows = [[r.get("id"), r.get("name"), r.get("file_name"), r.get("mime_type"), r.get("file_size"), r.get("tags") or [], "启用" if r.get("enabled") else "停用", r.get("updated_at")] for r in rows]
        label = "附件素材"
    if not rows and not repo.is_production:
        table_rows = [["local_contract_media", f"{label}结构校验", "local_contract", "-", [], "启用", _now_iso()]]
    return _base_payload(
        repo,
        {
            "cards": [{"label": label, "value": len(rows) or len(table_rows), "description": f"{kind}_library"}],
            "sections": [{"title": f"{label}列表", "headers": headers, "rows": table_rows}],
            "empty_note": f"生产数据为空：当前 {label} 表没有可显示记录。",
        },
    )


def jobs_payload(repo: AdminReadRepository) -> dict[str, Any]:
    sync_count = repo.count("sync_runs")
    callback_count = repo.count("wecom_external_contact_event_logs")
    batch_count = repo.count("reply_message_batch")
    outbound_count = repo.count("outbound_tasks")
    timer_rows = [
        ["aicrm-campaign-run-due.timer", "scheduled_safe_mode", "safe mode payload required"],
    ]
    cards = [
        {"label": "同步记录", "value": sync_count, "description": "sync_runs"},
        {"label": "回调事件", "value": callback_count, "description": "wecom_external_contact_event_logs"},
        {"label": "消息批次", "value": batch_count, "description": "reply_message_batch"},
        {"label": "出站任务", "value": outbound_count, "description": "outbound_tasks"},
    ]
    if not repo.is_production:
        cards = [{**card, "value": card["value"] or 1, "description": "本地结构校验"} for card in cards]
    return _base_payload(repo, {"cards": cards, "sections": [{"title": "Timer 状态", "headers": ["timer", "状态", "说明"], "rows": timer_rows}]})


def config_payload(repo: AdminReadRepository) -> dict[str, Any]:
    health = repo.runtime_health()
    db_label = health.get("database_mode")
    if db_label == "fixture":
        db_label = "local_contract_probe"
    rows = [
        ["database_mode", db_label],
        ["production_data_ready", health.get("production_data_ready")],
        ["release_sha", current_release_sha()],
        ["wechat_callback_token", "configured" if runtime_setting("WECOM_CALLBACK_TOKEN") else "missing"],
        ["wechat_pay_config", "configured" if runtime_setting("WECHAT_PAY_MCH_ID") else "missing"],
        ["oauth_config", "configured" if os.getenv("WECHAT_OAUTH_APPID") or os.getenv("WECHAT_MP_APPID") else "missing"],
    ]
    return _base_payload(
        repo,
        {
            "cards": [
                {"label": "数据库", "value": db_label, "description": "runtime health"},
                {"label": "生产数据", "value": str(health.get("production_data_ready")), "description": "production_data_ready"},
            ],
            "sections": [{"title": "运行配置", "headers": ["项目", "状态"], "rows": rows}],
        },
    )


def page_row_count(payload: dict[str, Any]) -> int:
    return sum(len(section.get("rows") or []) for section in payload.get("sections") or [])
