from __future__ import annotations

import json
import os

from sqlalchemy import text

from aicrm_next.platform.shared.db_session import get_session_factory
from tests.admin_auth_test_helpers import admin_session_cookies


def _admin_cookies(next_client) -> dict[str, str]:
    return admin_session_cookies(next_client, "super_admin")


def _insert_package(
    session,
    *,
    package_key: str,
    name: str,
    status: str = "active",
    incremental_enabled: bool = True,
    daily_enabled: bool = False,
    incremental_interval_seconds: int = 180,
    daily_refresh_time: str = "03:00",
    updated_at: str = "2026-06-24 09:00:00+08",
) -> int:
    return int(
        session.execute(
            text(
                """
                INSERT INTO ai_audience_package (
                    package_key, name, status, query_mode, identity_policy,
                    incremental_enabled, daily_enabled, incremental_interval_seconds,
                    daily_refresh_time, timezone, lookback_seconds,
                    created_at, updated_at
                )
                VALUES (
                    :package_key, :name, :status, 'incremental_event', 'external_userid',
                    :incremental_enabled, :daily_enabled, :incremental_interval_seconds,
                    :daily_refresh_time, 'Asia/Shanghai', 600,
                    TIMESTAMPTZ '2026-06-24 08:50:00+08', CAST(:updated_at AS timestamptz)
                )
                RETURNING id
                """
            ),
            {
                "package_key": package_key,
                "name": name,
                "status": status,
                "incremental_enabled": incremental_enabled,
                "daily_enabled": daily_enabled,
                "incremental_interval_seconds": incremental_interval_seconds,
                "daily_refresh_time": daily_refresh_time,
                "updated_at": updated_at,
            },
        ).scalar_one()
    )


def _insert_member(session, *, package_id: int, identity_value: str, status: str = "active") -> None:
    unionid = f"union_{identity_value}"
    session.execute(
        text(
            """
            INSERT INTO crm_user_identity (
                unionid, primary_external_userid, external_userids_json, identity_status, created_at, updated_at
            )
            VALUES (
                :unionid, :external_userid, jsonb_build_array(CAST(:external_userid AS text)), 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT (unionid) DO UPDATE SET
                primary_external_userid = EXCLUDED.primary_external_userid,
                external_userids_json = EXCLUDED.external_userids_json,
                identity_status = EXCLUDED.identity_status,
                updated_at = CURRENT_TIMESTAMP
            """
        ),
        {"unionid": unionid, "external_userid": identity_value},
    )
    session.execute(
        text(
            """
            INSERT INTO ai_audience_member_current (
                package_id, identity_type, identity_value, unionid, status,
                event_source_key, payload_hash, payload_json
            )
            VALUES (
                :package_id, 'unionid', :unionid, :unionid, :status,
                :event_source_key, :payload_hash, '{"hidden":"payload"}'::jsonb
            )
            """
        ),
        {
            "package_id": package_id,
            "unionid": unionid,
            "status": status,
            "event_source_key": f"event:{identity_value}",
            "payload_hash": f"hash:{identity_value}",
        },
    )


def _insert_run(session, *, package_id: int, refresh_finished_at: str, run_status: str = "succeeded") -> None:
    session.execute(
        text(
            """
            INSERT INTO ai_audience_package_run (
                package_id, run_type, status, refresh_started_at, refresh_finished_at,
                returned_count, entered_count, member_event_count
            )
            VALUES (
                :package_id, 'incremental', :run_status,
                CAST(:refresh_finished_at AS timestamptz) - interval '1 minute',
                CAST(:refresh_finished_at AS timestamptz),
                1, 1, 1
            )
            """
        ),
        {"package_id": package_id, "refresh_finished_at": refresh_finished_at, "run_status": run_status},
    )


def test_admin_ai_audience_packages_requires_admin_session(next_client, monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ADMIN_AUTH_ENFORCED", "true")
    response = next_client.get("/api/admin/ai-audience/packages")

    assert response.status_code == 401
    assert response.json()["error"] == "admin_auth_required"


def test_admin_ai_audience_package_create_requires_admin_session(next_client, monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ADMIN_AUTH_ENFORCED", "true")
    response = next_client.post(
        "/api/admin/ai-audience/packages",
        json={"package_key": "no_cookie", "name": "No Cookie", "refresh_mode": "manual"},
    )

    assert response.status_code == 401
    assert response.json()["error"] == "admin_auth_required"


def test_admin_ai_audience_package_create_version_publish_contract(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    monkeypatch.setenv("SECRET_KEY", "ai-audience-create-test")
    monkeypatch.setenv("AICRM_AUDIENCE_READONLY_DATABASE_URL", os.environ["DATABASE_URL"])
    sql_text = """
        SELECT
          'external_userid' AS identity_type,
          qs.external_userid AS identity_value,
          'questionnaire_submission:' || qs.submission_id::text AS event_source_key,
          jsonb_build_object('questionnaire_id', qs.questionnaire_id) AS payload_json,
          qs.external_userid,
          qs.unionid,
          qs.submitted_at AS event_at
        FROM audience_read.questionnaire_submissions_v1 qs
        JOIN audience_read.wecom_contacts_v1 wc ON wc.external_userid = qs.external_userid
        WHERE qs.questionnaire_id = :questionnaire_id
          AND qs.submitted_at >= :last_watermark_at
    """

    invalid_refresh = next_client.post(
        "/api/admin/ai-audience/packages",
        cookies=_admin_cookies(next_client),
        json={"package_key": "create_invalid", "name": "非法刷新", "refresh_mode": "incremental_5m"},
    )
    assert invalid_refresh.status_code == 400
    assert invalid_refresh.json()["error"] == "invalid_refresh_mode"

    active_create = next_client.post(
        "/api/admin/ai-audience/packages",
        cookies=_admin_cookies(next_client),
        json={"package_key": "create_active", "name": "禁止直接 active", "refresh_mode": "manual", "status": "active"},
    )
    assert active_create.status_code == 400
    assert active_create.json()["error"] == "invalid_initial_status"

    created = next_client.post(
        "/api/admin/ai-audience/packages",
        cookies=_admin_cookies(next_client),
        json={
            "package_key": "create_pkg",
            "name": "创建包",
            "status": "paused",
            "refresh_mode": "incremental_3m",
            "natural_language_definition": "提交问卷且已加微",
            "parameters": {"questionnaire_id": 101},
            "incremental_sql_text": sql_text,
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["ok"] is True
    package_id = body["package"]["id"]
    assert body["package"]["status"] == "paused"
    assert body["package"]["refresh_mode"] == "incremental_3m"
    assert body["version"]["parameters"] == {"questionnaire_id": 101}
    response_text = json.dumps(body, ensure_ascii=False)
    for forbidden in ("incremental_sql_text", "snapshot_sql_text", "inbound_webhook_secret", "signing_secret"):
        assert forbidden not in response_text

    session_factory = get_session_factory()
    with session_factory() as session:
        package_row = (
            session.execute(
                text("SELECT status, incremental_enabled, incremental_interval_seconds, next_incremental_refresh_at FROM ai_audience_package WHERE id = :id"),
                {"id": package_id},
            )
            .mappings()
            .one()
        )
        version_row = (
            session.execute(text("SELECT parameters_json FROM ai_audience_package_version WHERE package_id = :id"), {"id": package_id}).mappings().one()
        )
        session.execute(
            text(
                """
                INSERT INTO crm_user_identity (
                    unionid, primary_external_userid, external_userids_json, identity_status, created_at, updated_at
                )
                VALUES (
                    'union_preview_102',
                    'wm_preview_102',
                    jsonb_build_array('wm_preview_102'::text),
                    'active',
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                )
                ON CONFLICT (unionid) DO UPDATE SET
                    primary_external_userid = EXCLUDED.primary_external_userid,
                    external_userids_json = EXCLUDED.external_userids_json,
                    identity_status = EXCLUDED.identity_status,
                    updated_at = CURRENT_TIMESTAMP
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO questionnaire_submissions (
                    questionnaire_id, unionid, follow_user_userid, staff_id, submitted_at
                )
                VALUES (102, 'union_preview_102', 'HuangYouCan', 'HuangYouCan', CURRENT_TIMESTAMP - interval '1 minute')
                """
            )
        )
        session.commit()
    assert package_row["status"] == "paused"
    assert package_row["incremental_enabled"] is True
    assert package_row["incremental_interval_seconds"] == 180
    assert package_row["next_incremental_refresh_at"] is None
    assert version_row["parameters_json"] == {"questionnaire_id": 101}

    version = next_client.post(
        f"/api/admin/ai-audience/packages/{package_id}/versions",
        cookies=_admin_cookies(next_client),
        json={"incremental_sql_text": sql_text, "parameters": {"questionnaire_id": 202}},
    )
    assert version.status_code == 200
    assert version.json()["version"]["parameters"] == {"questionnaire_id": 202}

    preview_sql = """
        SELECT
          'external_userid' AS identity_type,
          qs.external_userid AS identity_value,
          'preview:' || CAST(:package_id AS text) AS event_source_key,
          jsonb_build_object('questionnaire_id', :questionnaire_id, 'lookback_seconds', :lookback_seconds) AS payload_json,
          qs.external_userid,
          qs.unionid,
          :refresh_started_at AS event_at
        FROM audience_read.questionnaire_submissions_v1 qs
        WHERE qs.questionnaire_id = :questionnaire_id
          AND :questionnaire_id = 102
          AND :refresh_started_at >= :last_watermark_at
          AND :lookback_seconds >= 0
    """
    preview_version = next_client.post(
        f"/api/admin/ai-audience/packages/{package_id}/versions",
        cookies=_admin_cookies(next_client),
        json={"incremental_sql_text": preview_sql, "parameters": {"questionnaire_id": 101}},
    )
    assert preview_version.status_code == 200
    preview = next_client.post(
        f"/api/admin/ai-audience/packages/{package_id}/preview",
        cookies=_admin_cookies(next_client),
        json={"version_id": preview_version.json()["version"]["id"], "sql_kind": "incremental", "params": {"questionnaire_id": 102}, "limit": 5},
    )
    assert preview.status_code == 200
    assert preview.json()["ok"] is True
    assert preview.json()["sample_rows"][0]["identity_value"] == "wm_preview_102"
    assert preview.json()["sample_rows"][0]["payload_json"]["lookback_seconds"] == 600

    invalid_preview = next_client.post(
        f"/api/admin/ai-audience/packages/{package_id}/preview",
        cookies=_admin_cookies(next_client),
        json={"sql_text": "SELECT * FROM public.users", "limit": 5},
    )
    assert invalid_preview.status_code == 400
    assert "select_star_forbidden" in invalid_preview.json()["validation_errors"]

    published = next_client.post(
        f"/api/admin/ai-audience/packages/{package_id}/publish",
        cookies=_admin_cookies(next_client),
        json={},
    )
    assert published.status_code == 200
    assert published.json()["package"]["status"] == "active"
    assert "incremental_sql_text" not in json.dumps(published.json(), ensure_ascii=False)


def test_admin_ai_audience_packages_api_returns_lightweight_read_model(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    monkeypatch.setenv("SECRET_KEY", "ai-audience-admin-api-test")
    session_factory = get_session_factory()
    with session_factory() as session:
        no_run_id = _insert_package(
            session,
            package_key="admin_no_run",
            name="无运行记录人群包",
            incremental_interval_seconds=180,
            updated_at="2026-06-24 09:10:00+08",
        )
        counted_id = _insert_package(
            session,
            package_key="admin_counted",
            name="有成员与多次刷新人群包",
            incremental_interval_seconds=300,
            updated_at="2026-06-24 09:09:00+08",
        )
        daily_id = _insert_package(
            session,
            package_key="admin_daily",
            name="每日快照人群包",
            incremental_enabled=False,
            daily_enabled=True,
            daily_refresh_time="03:00",
            updated_at="2026-06-24 09:08:00+08",
        )
        hybrid_id = _insert_package(
            session,
            package_key="admin_hybrid",
            name="增量加每日人群包",
            incremental_enabled=True,
            daily_enabled=True,
            incremental_interval_seconds=180,
            daily_refresh_time="03:00",
            updated_at="2026-06-24 09:07:00+08",
        )
        _insert_package(
            session,
            package_key="admin_archived",
            name="归档不展示",
            status="archived",
            updated_at="2026-06-24 09:11:00+08",
        )
        _insert_member(session, package_id=counted_id, identity_value="wm_active_1")
        _insert_member(session, package_id=counted_id, identity_value="wm_active_2")
        _insert_member(session, package_id=counted_id, identity_value="wm_exited", status="exited")
        _insert_run(session, package_id=counted_id, refresh_finished_at="2026-06-24 09:01:00+08")
        _insert_run(session, package_id=counted_id, refresh_finished_at="2026-06-24 09:05:12+08")
        session.commit()

    response = next_client.get("/api/admin/ai-audience/packages", cookies=_admin_cookies(next_client))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    payload = response.json()
    assert payload["ok"] is True
    assert payload["total"] == 4
    assert [item["package_key"] for item in payload["items"]] == [
        "admin_no_run",
        "admin_counted",
        "admin_daily",
        "admin_hybrid",
    ]
    by_key = {item["package_key"]: item for item in payload["items"]}
    assert by_key["admin_no_run"] == {
        "id": no_run_id,
        "package_key": "admin_no_run",
        "name": "无运行记录人群包",
        "status": "active",
        "member_count": 0,
        "last_refreshed_at": None,
        "refresh_mode": "incremental_3m",
        "refresh_mode_label": "每 3 分钟",
        "group_id": None,
        "group_name": "未分组",
        "template_key": "",
        "template_version": None,
        "template_label": "历史配置",
    }
    assert by_key["admin_counted"] == {
        "id": counted_id,
        "package_key": "admin_counted",
        "name": "有成员与多次刷新人群包",
        "status": "active",
        "member_count": 2,
        "last_refreshed_at": "2026-06-24T09:05:12+08:00",
        "refresh_mode": "incremental_3m",
        "refresh_mode_label": "每 3 分钟",
        "group_id": None,
        "group_name": "未分组",
        "template_key": "",
        "template_version": None,
        "template_label": "历史配置",
    }
    assert by_key["admin_daily"]["id"] == daily_id
    assert by_key["admin_daily"]["refresh_mode"] == "daily_0200"
    assert by_key["admin_daily"]["refresh_mode_label"] == "每日 2:00"
    assert by_key["admin_hybrid"]["id"] == hybrid_id
    assert by_key["admin_hybrid"]["refresh_mode"] == "incremental_3m_plus_daily_0200"
    assert by_key["admin_hybrid"]["refresh_mode_label"] == "每 3 分钟 + 每日 2:00"

    response_text = json.dumps(payload, ensure_ascii=False)
    for forbidden in (
        "sql_text",
        "incremental_sql_text",
        "snapshot_sql_text",
        "inbound_webhook_secret",
        "signing_secret",
        "payload_json",
        "headers_json",
        "wm_active_1",
        "wm_active_2",
        "wm_exited",
        "归档不展示",
    ):
        assert forbidden not in response_text


def test_admin_ai_audience_list_page_matches_management_contract(next_client, monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "ai-audience-list-page-test")

    response = next_client.get("/admin/automation-conversion", cookies=_admin_cookies(next_client))

    assert response.status_code == 200
    html = response.text
    for expected in (
        "AI 自动化运营",
        "人群包名称",
        "人数",
        "最后一次刷新时间",
        "刷新方式",
        "操作",
        "人群包分组",
        "新增",
        "编辑组名",
        "未分组",
        "编辑",
        "复制",
        "删除",
        "群发",
        "/api/admin/ai-audience/packages",
        "/api/admin/user-ops/batch-send/preview",
        "/api/admin/user-ops/batch-send/execute",
        'targetSource:"ai_audience_package"',
        "UserOpsBatchSendModal.open",
        "send_content_composer.css",
        "send_content_composer.js",
        "material_picker.css",
        "material_picker.js",
        "user_ops_batch_send_modal.js",
    ):
        assert expected in html
    assert "/api/ai/audience/packages" not in html


def test_admin_ai_audience_detail_page_has_required_sections_without_top_actions(next_client, monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "ai-audience-detail-page-test")

    response = next_client.get("/admin/automation-conversion/packages/123", cookies=_admin_cookies(next_client))

    assert response.status_code == 200
    html = response.text
    assert 'aria-label="人群包基础摘要"' in html
    assert 'aria-label="人群包配置维度"' in html
    assert html.count('data-panel="') == 5
    assert html.count('<section class="ai-panel') == 5
    for expected in (
        'data-panel="basic"',
        'data-panel="automation"',
        'data-panel="senders"',
        'data-panel="members"',
        'data-panel="records"',
        'id="panel-basic"',
        'id="panel-automation"',
        'id="panel-senders"',
        'id="panel-members"',
        'id="panel-records"',
        'id="saveCurrentDimensionBtn"',
        'id="packageGroupSelect"',
        'id="automationCapabilitySelector"',
        "自动化话术能力",
        "automation_capability_selector.js",
        "send_content_readonly_detail.js",
    ):
        assert expected in html
    for expected in (
        "基础配置",
        "自动化话术能力",
        "发送人白名单",
        "成员列表",
        "发送记录",
        "当前人数",
        "最后一次刷新",
        "刷新方式",
        "状态",
        "返回列表",
        "手动刷新",
        "保存当前维度",
        "人群包名称",
        "筛选逻辑简述",
        "所属分组",
        "未分组",
        "增量刷新",
        "每 3 分钟",
        "每日 2:00",
        "从已有自动化列表中选择一个能力",
        "解除绑定",
        "保存绑定",
        "OperationMemberPicker.open",
        "选择发送人",
        "已选择发送人，请保存白名单",
        "发送人白名单最多 5 个",
        'scope: "ai_audience"',
        "更换",
        "外部联系人 ID",
        "/api/admin/ai-audience/packages/123",
        "/api/admin/ai-audience/packages/123/members",
        "/api/admin/ai-audience/packages/123/send-records",
        "/api/admin/ai-audience/packages/123/automation-binding",
        "/api/admin/ai-audience/package-groups",
        "/api/admin/automation-agents",
        "/api/admin/ai-audience/packages/123/senders",
        "operation_member_picker.js",
        'body: { items: normalized }',
        'body: {\n          name:',
        "createBindingController",
        "发送来源",
        "发送状态",
        "发送时间",
        "失败原因",
        "发送明细",
        "ai_audience_send_records.js",
        "data-send-records-api-url",
        "AICRMAudienceSendRecords.enter",
        "sendRecordPrevBtn",
        "sendRecordNextBtn",
        'aria-label="发送明细"',
    ):
        assert expected in html
    for forbidden in (
        "返回人群包列表",
        "一键群发",
        "每 5 分钟",
        "每 15 分钟",
        "每 30 分钟",
        "cron",
        "自定义时间",
        "手机号",
        "问卷答案",
        "owner 明细",
        "推荐发送人",
        "群发状态",
        "接收 Webhook 地址（系统生成）",
        "外推 Webhook 地址（增量刷新后触发）",
        "/api/admin/ai-audience/packages/123/webhooks",
        "/api/ai/audience/packages",
        "inbound_webhook_secret",
        "signing_secret",
        "window.prompt",
        "请输入发送人 userid",
        "请先填写当前发送人",
        'data-sender-field="sender_userid"',
        "通过 AI 创建 SQL 人群包；查看当前命中人数、最后一次刷新时间与刷新方式。",
        "固定逻辑：不提供 5/15/30 分钟。",
        "外部 Agent 生成内容后回调；地址不可编辑，可重置 secret。",
        "每 3 分钟增量刷新后，如果有新增用户，则外推。",
        "body: JSON.stringify",
        "请求 body 仅为 external_userid 数组，包信息、签名、幂等键走 Header。",
        "用户被多个客服添加时，只在白名单里选；多个命中按优先级取第一个；无命中则跳过。",
        "名称、筛选逻辑、刷新策略",
        "接收地址、外推地址",
        "外发身份与优先级",
        "当前命中人群明细",
        "对应接口",
        "这里保留",
        "这里不混入",
        "用于后续外部 Agent",
        "配置向导",
        "入口渠道",
        "分层规则",
        "入池规则",
        "运营编排",
        "检查并发布",
        "自动化运营方案",
    ):
        assert forbidden not in html


def test_user_ops_batch_send_modal_static_component_uses_standard_endpoints(next_client) -> None:
    response = next_client.get("/static/admin_console/user_ops_batch_send_modal.js")

    assert response.status_code == 200
    script = response.text
    for expected in (
        "window.UserOpsBatchSendModal",
        "/api/admin/user-ops/batch-send/preview",
        "/api/admin/user-ops/batch-send/execute",
        "AICRMSendContentComposer.open",
        "target_source",
        "target_source_id",
        'selection_mode: "all_filtered"',
        "requestImages(contentPackage)",
        "requestAttachments(contentPackage)",
        "image_library_ids",
        "miniprogram_library_ids",
        "attachment_library_ids",
    ):
        assert expected in script
    assert "/api/admin/ai-audience/packages/" not in script
    assert "uops-batch-modal" not in script
    assert "标准私信群发组件" not in script


def test_admin_ai_audience_package_detail_requires_admin_and_redacts_sensitive_fields(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    monkeypatch.setenv("AICRM_ADMIN_AUTH_ENFORCED", "true")
    monkeypatch.setenv("SECRET_KEY", "ai-audience-detail-test")
    session_factory = get_session_factory()
    with session_factory() as session:
        package_id = _insert_package(
            session,
            package_key="detail_pkg",
            name="详情人群包",
            incremental_enabled=True,
            daily_enabled=True,
            daily_refresh_time="02:00",
        )
        session.execute(
            text(
                """
                UPDATE ai_audience_package
                SET natural_language_definition = '近 30 天提交问卷且已加微'
                WHERE id = :package_id
                """
            ),
            {"package_id": package_id},
        )
        _insert_member(session, package_id=package_id, identity_value="wm_hidden")
        session.commit()

    no_cookie = next_client.get(f"/api/admin/ai-audience/packages/{package_id}")
    assert no_cookie.status_code == 401

    response = next_client.get(f"/api/admin/ai-audience/packages/{package_id}", cookies=_admin_cookies(next_client))
    assert response.status_code == 200
    package = response.json()["package"]
    assert package == {
        "id": package_id,
        "package_key": "detail_pkg",
        "name": "详情人群包",
        "status": "active",
        "member_count": 1,
        "last_refreshed_at": None,
        "refresh_mode": "incremental_3m_plus_daily_0200",
        "refresh_mode_label": "每 3 分钟 + 每日 2:00",
        "natural_language_definition": "近 30 天提交问卷且已加微",
        "group_id": None,
        "group_name": "未分组",
        "template_key": "",
        "template_version": None,
        "template_label": "历史配置",
        "current_version_id": None,
        "current_version_number": None,
        "template_parameters": {},
        "is_historical_config": True,
    }
    response_text = json.dumps(response.json(), ensure_ascii=False)
    for forbidden in ("sql_text", "inbound_webhook_secret", "signing_secret", "payload_json", "wm_hidden"):
        assert forbidden not in response_text


def test_admin_ai_audience_package_patch_normalizes_refresh_modes(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    monkeypatch.setenv("SECRET_KEY", "ai-audience-patch-test")
    session_factory = get_session_factory()
    with session_factory() as session:
        package_id = _insert_package(session, package_key="patch_pkg", name="旧名称", incremental_interval_seconds=900)
        session.commit()

    invalid = next_client.patch(
        f"/api/admin/ai-audience/packages/{package_id}",
        cookies=_admin_cookies(next_client),
        json={"refresh_mode": "incremental_15m"},
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"] == "invalid_refresh_mode"

    response = next_client.patch(
        f"/api/admin/ai-audience/packages/{package_id}",
        cookies=_admin_cookies(next_client),
        json={
            "name": "新名称",
            "natural_language_definition": "新定义",
            "refresh_mode": "daily_0200",
        },
    )
    assert response.status_code == 200
    package = response.json()["package"]
    assert package["name"] == "新名称"
    assert package["natural_language_definition"] == "新定义"
    assert package["refresh_mode"] == "daily_0200"
    assert package["refresh_mode_label"] == "每日 2:00"

    with session_factory() as session:
        row = session.execute(text("SELECT * FROM ai_audience_package WHERE id = :package_id"), {"package_id": package_id}).mappings().one()
    assert row["incremental_enabled"] is False
    assert row["daily_enabled"] is True
    assert row["incremental_interval_seconds"] == 180
    assert row["daily_refresh_time"] == "02:00"


def test_admin_ai_audience_activate_incremental_only_package(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    monkeypatch.setenv("SECRET_KEY", "ai-audience-activate-test")
    session_factory = get_session_factory()
    with session_factory() as session:
        package_id = _insert_package(
            session,
            package_key="activate_incremental_only",
            name="仅增量包",
            status="paused",
            incremental_enabled=True,
            daily_enabled=False,
        )
        session.commit()

    response = next_client.post(f"/api/admin/ai-audience/packages/{package_id}/activate", cookies=_admin_cookies(next_client))

    assert response.status_code == 200
    package = response.json()["package"]
    assert package["status"] == "active"
    with session_factory() as session:
        row = (
            session.execute(
                text(
                    """
                SELECT status, next_incremental_refresh_at, next_daily_refresh_at
                FROM ai_audience_package
                WHERE id = :package_id
                """
                ),
                {"package_id": package_id},
            )
            .mappings()
            .one()
        )
    assert row["status"] == "active"
    assert row["next_incremental_refresh_at"] is not None
    assert row["next_daily_refresh_at"] is None


def test_admin_ai_audience_members_api_returns_active_safe_fields(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    monkeypatch.setenv("SECRET_KEY", "ai-audience-members-test")
    session_factory = get_session_factory()
    with session_factory() as session:
        package_id = _insert_package(session, package_key="members_pkg", name="成员人群包")
        _insert_member(session, package_id=package_id, identity_value="wm_active_named")
        _insert_member(session, package_id=package_id, identity_value="wm_active_unnamed")
        _insert_member(session, package_id=package_id, identity_value="wm_exited", status="exited")
        session.execute(
            text(
                """
                INSERT INTO wecom_external_contact_identity_map (
                    external_userid, follow_user_userid, name, status, updated_at
                )
                VALUES ('wm_active_named', 'HuangYouCan', '浅蓝', 'active', CURRENT_TIMESTAMP)
                """
            )
        )
        session.commit()

    response = next_client.get(f"/api/admin/ai-audience/packages/{package_id}/members", cookies=_admin_cookies(next_client))

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["limit"] == 50
    names = {item["external_userid"]: item["nickname"] for item in payload["items"]}
    assert names["wm_active_named"] == "浅蓝"
    assert names["wm_active_unnamed"] == "未命名客户"
    response_text = json.dumps(payload, ensure_ascii=False)
    for forbidden in ("payload_json", "mobile", "tags", "questionnaire", "wm_exited", "owner_userid"):
        assert forbidden not in response_text


def test_admin_ai_audience_webhook_api_is_retired(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    monkeypatch.setenv("SECRET_KEY", "ai-audience-webhook-test")
    monkeypatch.setenv("AICRM_PUBLIC_BASE_URL", "https://www.youcangogogo.com")
    session_factory = get_session_factory()
    with session_factory() as session:
        package_id = _insert_package(session, package_key="webhook_pkg", name="Webhook 包")
        session.commit()

    patch = next_client.patch(
        f"/api/admin/ai-audience/packages/{package_id}/webhooks",
        cookies=_admin_cookies(next_client),
        json={
            "outbound_enabled": True,
            "outbound_webhook_url": "https://agent.example.com/audience/entered",
        },
    )
    assert patch.status_code == 410
    assert patch.json()["error"] == "webhook_configuration_retired"

    get = next_client.get(
        f"/api/admin/ai-audience/packages/{package_id}/webhooks",
        cookies=_admin_cookies(next_client),
    )
    assert get.status_code == 410
    assert get.json()["error"] == "webhook_configuration_retired"
    with session_factory() as session:
        assert session.execute(
            text("SELECT COUNT(*) FROM ai_audience_outbound_subscription WHERE package_id = :package_id"),
            {"package_id": package_id},
        ).scalar_one() == 0

    removed_rotate = next_client.post(
        f"/api/admin/ai-audience/packages/{package_id}/webhooks/rotate-inbound-secret",
        cookies=_admin_cookies(next_client),
    )
    assert removed_rotate.status_code == 404


def test_admin_ai_audience_sender_whitelist_get_put(next_client, next_pg_schema, monkeypatch) -> None:
    del next_pg_schema
    monkeypatch.setenv("SECRET_KEY", "ai-audience-senders-test")
    session_factory = get_session_factory()
    with session_factory() as session:
        package_id = _insert_package(session, package_key="senders_pkg", name="发送人包")
        session.commit()

    put = next_client.put(
        f"/api/admin/ai-audience/packages/{package_id}/senders",
        cookies=_admin_cookies(next_client),
        json={
            "items": [
                {"sender_userid": "HuangYouCan", "display_name": "HuangYouCan", "priority": 2, "status": "active"},
                {"sender_userid": "QianLan", "display_name": "QianLan", "priority": 1, "status": "active"},
            ]
        },
    )
    assert put.status_code == 200
    assert [item["sender_userid"] for item in put.json()["items"]] == ["QianLan", "HuangYouCan"]

    get = next_client.get(f"/api/admin/ai-audience/packages/{package_id}/senders", cookies=_admin_cookies(next_client))
    assert get.status_code == 200
    assert [item["priority"] for item in get.json()["items"]] == [1, 2]

    invalid = next_client.put(
        f"/api/admin/ai-audience/packages/{package_id}/senders",
        cookies=_admin_cookies(next_client),
        json={"items": [{"sender_userid": "Bad", "status": "deleted"}]},
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"] == "invalid_sender_status"
