from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os

import pytest
from fastapi.testclient import TestClient

from aicrm_next.commerce.repo import reset_commerce_fixture_state
from aicrm_next.main import create_app
from aicrm_next.service_period.application import (
    ApplyServicePeriodRefundCommand,
    CreateServicePeriodProductCommand,
    GrantOrRenewEntitlementCommand,
)
from aicrm_next.service_period.dto import ServicePeriodProductCreateRequest
from aicrm_next.service_period.huangyoucan_usage import huangyoucan_usage_match_joins
from aicrm_next.service_period.member_grid import (
    MemberViewConflictError,
    empty_view_config,
    member_grid_schema,
    normalize_view_config,
    query_in_memory_rows,
)
from aicrm_next.service_period.repo import (
    PostgresServicePeriodRepository,
    build_service_period_repository,
    reset_service_period_fixture_state,
)
from aicrm_next.shared.errors import ContractError
from tests.admin_auth_test_helpers import install_admin_action_tokens


def _reset() -> None:
    reset_commerce_fixture_state()
    reset_service_period_fixture_state()


def _product_payload(code: str = "sp_member_grid") -> dict:
    return {
        "product_code": code,
        "title": "成员网格测试商品",
        "description": "飞书式原生成员网格",
        "price_cents": 99900,
        "currency": "CNY",
        "status": "active",
        "duration_days": 90,
        "membership_config_id": "member_grid_vip",
        "membership_config_name": "成员网格会员",
    }


def _paid_order(index: int, *, product_code: str = "sp_member_grid", unionid: str | None = None) -> dict:
    return {
        "id": 10000 + index,
        "out_trade_no": f"SP_MEMBER_GRID_{index:04d}",
        "product_code": product_code,
        "product_name": "成员网格测试商品",
        "amount_total": 99900,
        "currency": "CNY",
        "unionid": unionid or f"union_grid_{index:04d}",
        "payer_name_snapshot": f"会员 {index:04d}",
        "status": "paid",
        "trade_state": "SUCCESS",
        "paid_at": "2099-01-01T00:00:00+00:00",
        "metadata_json": {"payer_identity": {"external_userid": f"wm_grid_{index:04d}"}},
    }


def _member(index: int, *, now: datetime) -> dict:
    matched = index % 4 != 0
    progress = None if index % 5 == 0 else {"current": index % 6, "total": 5}
    return {
        "record_id": index,
        "unionid": f"union_{index:04d}",
        "display_name": f"会员 {index:04d}",
        "external_userid": f"wm_{index:04d}",
        "end_at": (now + timedelta(days=index % 30)).isoformat(),
        "renewal_count": index % 3,
        "remark": "重点" if index % 7 == 0 else "",
        "alliance": "增长联盟" if index % 9 == 0 else "",
        "huangyoucan_match_status": "matched_unionid" if matched else "not_found",
        "huangyoucan_formally_logged_in": index % 2 == 0,
        "huangyoucan_has_token_usage": index % 3 == 0,
        "huangyoucan_learning_plan_progress": progress,
        "huangyoucan_open_count_7d": index % 11,
        "huangyoucan_last_open_at": (now - timedelta(hours=index)).isoformat() if matched else None,
    }


def test_member_grid_schema_is_code_owned_and_fixed_to_ten_fields() -> None:
    schema = member_grid_schema()

    assert [field["id"] for field in schema["fields"]] == [
        "member",
        "remaining_days",
        "formally_logged_in",
        "token_usage",
        "learning_plan_progress",
        "open_count_7d",
        "last_open_at",
        "renewal_count",
        "remark",
        "alliance",
    ]
    assert schema["limits"] == {
        "filter_conditions": 20,
        "sorts": 8,
        "groups": 2,
        "page_size": 100,
    }
    assert [field["id"] for field in schema["fields"] if field["editable"]] == ["remark", "alliance"]


def test_postgres_member_grid_page_query_does_not_compute_global_exact_total() -> None:
    class CapturedResult:
        @staticmethod
        def fetchall() -> list[dict]:
            return []

    class CapturedConnection:
        statement = ""
        params: tuple = ()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def execute(self, statement: str, params: tuple):
            self.statement = statement
            self.params = params
            return CapturedResult()

    connection = CapturedConnection()

    class CapturedRepository(PostgresServicePeriodRepository):
        def __init__(self) -> None:
            self.connection = connection

        @staticmethod
        def get_product(service_product_id: str) -> dict:
            return {"id": service_product_id}

        def _connect(self):
            return self.connection

    repository = CapturedRepository()

    payload = repository.query_member_grid(
        "1",
        config=empty_view_config(),
        limit=20,
        cursor="",
    )

    assert "COUNT(*) OVER () AS total_count" not in connection.statement
    assert "LIMIT %s" in connection.statement
    assert connection.params[-1] == 21
    assert payload["total"] is None
    assert payload["has_more"] is False


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda config: config["sorts"].append({"field": "unknown", "direction": "asc"}), "不支持的字段"),
        (
            lambda config: config["filter"]["conditions"].append(
                {"field": "member", "operator": "raw_sql", "value": "1=1"}
            ),
            "不支持操作符",
        ),
        (
            lambda config: config["sorts"].extend(
                [{"field": "member", "direction": "asc"}, {"field": "member", "direction": "desc"}]
            ),
            "不能重复",
        ),
        (
            lambda config: (
                config["sorts"].append({"field": "member", "direction": "asc"}),
                config["groups"].append({"field": "member", "direction": "asc"}),
            ),
            "不能重复参与排序",
        ),
    ),
)
def test_member_grid_rejects_unknown_fields_operators_and_duplicate_ordering(mutate, message: str) -> None:
    config = empty_view_config()
    mutate(config)

    with pytest.raises(ContractError, match=message):
        normalize_view_config(config)


def test_member_grid_and_or_filter_sort_two_level_group_and_progress_semantics(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    now = datetime(2026, 7, 15, 4, 0, tzinfo=timezone.utc)
    members = [_member(index, now=now) for index in range(1, 61)]
    config = empty_view_config()
    config["filter"] = {
        "logic": "and",
        "conditions": [
            {"field": "remaining_days", "operator": "gte", "value": 10},
            {"field": "learning_plan_progress", "operator": "state_in", "value": ["in_progress", "complete"]},
        ],
    }
    config["groups"] = [
        {"field": "formally_logged_in", "direction": "asc"},
        {"field": "token_usage", "direction": "asc"},
    ]
    config["sorts"] = [{"field": "learning_plan_progress", "direction": "desc"}]

    payload = query_in_memory_rows(members, config=config, limit=100)

    assert payload["rows"]
    assert all(row["values"]["remaining_days"] >= 10 for row in payload["rows"])
    assert all(row["values"]["learning_plan_progress"]["state"] in {"in_progress", "complete"} for row in payload["rows"])
    assert all(len(row["group_path"]) == 2 for row in payload["rows"])
    assert all(path["count"] > 0 for row in payload["rows"] for path in row["group_path"])

    or_config = empty_view_config()
    or_config["filter"] = {
        "logic": "or",
        "conditions": [
            {"field": "member", "operator": "equals", "value": "会员 0001"},
            {"field": "remark", "operator": "contains", "value": "重点"},
            {"field": "alliance", "operator": "equals", "value": "增长联盟"},
        ],
    }
    or_payload = query_in_memory_rows(members, config=or_config, limit=100)
    unionids = {row["unionid"] for row in or_payload["rows"]}
    assert "union_0001" in unionids
    assert "union_0007" in unionids
    assert "union_0009" in unionids

    renewal_config = empty_view_config()
    renewal_config["filter"]["conditions"] = [
        {"field": "renewal_count", "operator": "gte", "value": 2},
    ]
    renewal_config["sorts"] = [{"field": "renewal_count", "direction": "desc"}]
    renewal_payload = query_in_memory_rows(members, config=renewal_config, limit=100)
    assert renewal_payload["rows"]
    assert all(row["values"]["renewal_count"] >= 2 for row in renewal_payload["rows"])


def test_signed_keyset_cursor_has_no_cross_page_duplicates_and_rejects_tampering(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    now = datetime(2026, 7, 15, 4, 0, tzinfo=timezone.utc)
    members = [_member(index, now=now) for index in range(1, 236)]
    config = empty_view_config()
    config["groups"] = [{"field": "formally_logged_in", "direction": "asc"}]
    config["sorts"] = [{"field": "member", "direction": "asc"}]

    first = query_in_memory_rows(members, config=config, limit=100)
    second = query_in_memory_rows(members, config=config, limit=100, cursor=first["next_cursor"])
    third = query_in_memory_rows(members, config=config, limit=100, cursor=second["next_cursor"])
    record_ids = [row["record_id"] for page in (first, second, third) for row in page["rows"]]

    assert len(record_ids) == 235
    assert len(set(record_ids)) == 235
    assert first["total"] is None
    assert first["has_more"] is True
    assert third["has_more"] is False
    assert third["next_cursor"] == ""

    tampered = first["next_cursor"][:-1] + ("A" if first["next_cursor"][-1] != "A" else "B")
    with pytest.raises(ContractError, match="分页游标无效"):
        query_in_memory_rows(members, config=config, cursor=tampered)
    changed = empty_view_config()
    changed["sorts"] = [{"field": "member", "direction": "desc"}]
    with pytest.raises(ContractError, match="视图配置已变化"):
        query_in_memory_rows(members, config=changed, cursor=first["next_cursor"])


def test_shared_view_crud_name_conflict_optimistic_lock_and_copy_default_only(next_client) -> None:
    _reset()
    created = next_client.post("/api/admin/service-period-products", json=_product_payload())
    product = created.json()["product"]

    initial = next_client.get(f"/api/admin/service-period-products/{product['id']}/member-views")
    assert initial.status_code == 200
    assert [(item["name"], item["is_default"]) for item in initial.json()["items"]] == [("表格", True)]

    config = empty_view_config()
    config["sorts"] = [{"field": "remaining_days", "direction": "asc"}]
    custom = next_client.post(
        f"/api/admin/service-period-products/{product['id']}/member-views",
        json={"name": "到期优先", "config": config},
    )
    assert custom.status_code == 201
    view = custom.json()["view"]
    assert view["version"] == 1

    duplicate = next_client.post(
        f"/api/admin/service-period-products/{product['id']}/member-views",
        json={"name": "到期优先".upper(), "config": config},
    )
    assert duplicate.status_code == 409

    updated_config = empty_view_config()
    updated_config["groups"] = [{"field": "formally_logged_in", "direction": "asc"}]
    updated = next_client.put(
        f"/api/admin/service-period-products/{product['id']}/member-views/{view['id']}",
        json={"name": "登录分组", "config": updated_config, "version": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["view"]["version"] == 2

    stale = next_client.put(
        f"/api/admin/service-period-products/{product['id']}/member-views/{view['id']}",
        json={"name": "过期保存", "config": config, "version": 1},
    )
    assert stale.status_code == 409

    default_view = initial.json()["items"][0]
    default_delete = next_client.request(
        "DELETE",
        f"/api/admin/service-period-products/{product['id']}/member-views/{default_view['id']}",
        json={"version": default_view["version"]},
    )
    assert default_delete.status_code == 400

    copied = next_client.post(f"/api/admin/service-period-products/{product['id']}/copy")
    assert copied.status_code == 201
    copied_id = copied.json()["product"]["id"]
    copied_views = next_client.get(f"/api/admin/service-period-products/{copied_id}/member-views").json()["items"]
    assert [(item["name"], item["is_default"]) for item in copied_views] == [("表格", True)]


def test_member_grid_api_exposes_renewal_count_and_editable_admin_text_fields(next_client) -> None:
    _reset()
    product = CreateServicePeriodProductCommand()(ServicePeriodProductCreateRequest(**_product_payload()))["product"]
    GrantOrRenewEntitlementCommand()(order=_paid_order(1))

    queried = next_client.post(
        f"/api/admin/service-period-products/{product['id']}/member-grid/query",
        json={"config": empty_view_config(), "limit": 100},
    )
    assert queried.status_code == 200
    row = queried.json()["rows"][0]
    assert row["unionid"] == "union_grid_0001"
    assert row["values"]["member"]["primary"] == "会员 0001"
    assert row["values"]["member"]["secondary"] == "wm_grid_0001"
    assert row["values"]["renewal_count"] == 0
    assert list(row["values"]) == [
        "member",
        "remaining_days",
        "formally_logged_in",
        "token_usage",
        "learning_plan_progress",
        "open_count_7d",
        "last_open_at",
        "renewal_count",
        "remark",
        "alliance",
    ]

    remark = next_client.put(
        f"/api/admin/service-period-products/{product['id']}/members/union_grid_0001/remark",
        json={"remark": "网格内备注"},
    )
    assert remark.status_code == 200
    alliance = next_client.put(
        f"/api/admin/service-period-products/{product['id']}/members/union_grid_0001/alliance",
        json={"alliance": "增长联盟"},
    )
    assert alliance.status_code == 200

    GrantOrRenewEntitlementCommand()(order=_paid_order(2, unionid="union_grid_0001"))
    refreshed = next_client.post(
        f"/api/admin/service-period-products/{product['id']}/member-grid/query",
        json={"config": empty_view_config(), "limit": 100},
    )
    assert refreshed.json()["rows"][0]["values"]["remark"] == "网格内备注"
    assert refreshed.json()["rows"][0]["values"]["alliance"] == "增长联盟"
    assert refreshed.json()["rows"][0]["values"]["renewal_count"] == 1

    ApplyServicePeriodRefundCommand()(out_trade_no="SP_MEMBER_GRID_0002", refund={"refund_status": "full_refunded"})
    after_refund = next_client.post(
        f"/api/admin/service-period-products/{product['id']}/member-grid/query",
        json={"config": empty_view_config(), "limit": 100},
    )
    assert after_refund.json()["rows"][0]["values"]["renewal_count"] == 0


def test_viewer_can_query_drafts_but_cannot_manage_views_or_edit_remarks(monkeypatch) -> None:
    _reset()
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)
    product = CreateServicePeriodProductCommand()(ServicePeriodProductCreateRequest(**_product_payload("sp_grid_permissions")))["product"]
    build_service_period_repository().create_member_grid_collaborator(
        product["id"],
        admin_user_id="test",
        wecom_userid="viewer_test",
        display_name="只读测试账号",
        avatar_url="",
        permission="read",
        actor="pytest",
    )
    GrantOrRenewEntitlementCommand()(order=_paid_order(1, product_code="sp_grid_permissions"))
    query_target = "/api/admin/service-period-products/{service_product_id}/member-grid/query"
    query_token = install_admin_action_tokens(client, ("POST", query_target), roles=("viewer",))[("POST", query_target)]

    schema = client.get(f"/api/admin/service-period-products/{product['id']}/member-grid/schema")
    views = client.get(f"/api/admin/service-period-products/{product['id']}/member-views")
    query = client.post(
        f"/api/admin/service-period-products/{product['id']}/member-grid/query",
        headers={"X-Admin-Action-Token": query_token},
        json={"config": empty_view_config()},
    )
    denied_create = client.post(
        f"/api/admin/service-period-products/{product['id']}/member-views",
        headers={"X-Admin-Action-Token": query_token},
        json={"name": "viewer", "config": empty_view_config()},
    )
    denied_remark = client.put(
        f"/api/admin/service-period-products/{product['id']}/members/union_grid_0001/remark",
        headers={"X-Admin-Action-Token": query_token},
        json={"remark": "viewer cannot write"},
    )
    denied_alliance = client.put(
        f"/api/admin/service-period-products/{product['id']}/members/union_grid_0001/alliance",
        headers={"X-Admin-Action-Token": query_token},
        json={"alliance": "viewer cannot write"},
    )

    assert [schema.status_code, views.status_code, query.status_code] == [200, 200, 200]
    assert denied_create.status_code == 403
    assert denied_remark.status_code == 403
    assert denied_alliance.status_code == 403

    page = client.get(f"/admin/service-period-products/{product['id']}/data")
    grants_text = page.text.split('id="aicrmAdminActionGrants"', 1)[1]
    assert query_target in grants_text
    assert "POST /api/admin/service-period-products/{service_product_id}/member-views" not in grants_text
    assert "PUT /api/admin/service-period-products/{service_product_id}/members/{unionid}/remark" not in grants_text
    assert "PUT /api/admin/service-period-products/{service_product_id}/members/{unionid}/alliance" not in grants_text


def test_huangyoucan_usage_match_joins_hit_existing_partial_indexes(next_pg_schema) -> None:
    import psycopg

    database_url = os.environ["DATABASE_URL"]
    usage_joins = huangyoucan_usage_match_joins(
        unionid_sql="identity.unionid",
        mobile_sql="identity.mobile",
    )
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            INSERT INTO service_period_huangyoucan_usage_snapshot (
                huangyoucan_user_id, unionid, mobile_md5, formally_logged_in, has_token_usage,
                learning_plan_id, open_count_7d, refreshed_at
            )
            SELECT
                'hyc_partial_index_noise_' || series,
                'union_partial_index_noise_' || series,
                md5((10000000000 + series)::text),
                FALSE,
                FALSE,
                '',
                0,
                CURRENT_TIMESTAMP
            FROM generate_series(1, 5000) AS series
            """
        )
        connection.execute(
            """
            INSERT INTO service_period_huangyoucan_usage_snapshot (
                huangyoucan_user_id, unionid, mobile_md5, formally_logged_in, has_token_usage,
                learning_plan_id, open_count_7d, refreshed_at
            ) VALUES (
                'hyc_partial_index_target',
                'union_partial_index_target',
                md5('13800138000'),
                TRUE,
                TRUE,
                '',
                0,
                CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute("ANALYZE service_period_huangyoucan_usage_snapshot")
        plan_payload = connection.execute(
            f"""
            EXPLAIN (COSTS OFF, FORMAT JSON)
            SELECT
                huangyoucan_match.match_status,
                huangyoucan_usage.huangyoucan_user_id
            FROM (
                VALUES ('union_partial_index_target'::text, '13800138000'::text)
            ) AS identity(unionid, mobile)
            {usage_joins}
            """
        ).fetchone()[0][0]

    index_names: set[str] = set()

    def collect_indexes(node: dict) -> None:
        if node.get("Index Name"):
            index_names.add(str(node["Index Name"]))
        for child in node.get("Plans") or []:
            collect_indexes(child)

    collect_indexes(plan_payload["Plan"])
    assert "idx_service_period_hyc_usage_unionid" in index_names
    assert "idx_service_period_hyc_usage_mobile_md5" in index_names


def test_postgres_grid_query_and_view_repository_contract(next_pg_schema) -> None:
    import psycopg

    database_url = os.environ["DATABASE_URL"]
    repo = PostgresServicePeriodRepository(database_url)
    with psycopg.connect(database_url) as connection:
        trade_product_id = connection.execute(
            """
            INSERT INTO wechat_pay_products (product_code, name, amount_total, currency, status, enabled)
            VALUES ('sp_grid_pg', 'PG 成员网格', 99900, 'CNY', 'active', TRUE)
            RETURNING id
            """
        ).fetchone()[0]
    product = repo.create_service_product(
        trade_product={"id": trade_product_id, "product_code": "sp_grid_pg", "title": "PG 成员网格"},
        duration_days=90,
        membership_config_id="pg_grid",
        membership_config_name="PG 网格会员",
        link_slug="sp-grid-pg",
    )
    with psycopg.connect(database_url) as connection:
        for index in range(1, 231):
            unionid = f"union_grid_pg_{index:04d}"
            connection.execute(
                """
                INSERT INTO service_period_entitlements (
                    service_product_id, trade_product_id, unionid, external_userid_snapshot,
                    membership_config_id, status, start_at, end_at, renewal_count, metadata_json
                ) VALUES (%s, %s, %s, %s, 'pg_grid', 'active', CURRENT_TIMESTAMP,
                          CURRENT_TIMESTAMP + (%s * INTERVAL '1 day'), %s, %s::jsonb)
                """,
                (
                    int(product["id"]),
                    trade_product_id,
                    unionid,
                    f"wm_grid_pg_{index:04d}",
                    index % 90 + 1,
                    index % 4,
                    json.dumps(
                        {
                            "payer_name": f"PG 会员 {index:04d}",
                            "admin_alliance": "PG 联盟" if index % 10 == 0 else "",
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            connection.execute(
                """
                INSERT INTO service_period_huangyoucan_usage_snapshot (
                    huangyoucan_user_id, unionid, mobile_md5, formally_logged_in, has_token_usage,
                    learning_plan_id, learning_plan_current, learning_plan_total,
                    open_count_7d, last_open_at, refreshed_at
                ) VALUES (%s, %s, '', %s, %s, 'pg_plan', %s, 5, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (f"hyc_grid_pg_{index:04d}", unionid, index % 2 == 0, index % 3 == 0, index % 6, index % 11),
            )

        def insert_paid_order_event(
            *,
            member_index: int,
            order_index: int,
            refunded_amount_total: int = 0,
            refund_status: str = "",
            add_refund_event: bool = False,
        ) -> None:
            unionid = f"union_grid_pg_{member_index:04d}"
            entitlement_id = connection.execute(
                "SELECT id FROM service_period_entitlements WHERE service_product_id = %s AND unionid = %s",
                (int(product["id"]), unionid),
            ).fetchone()[0]
            out_trade_no = f"SP_GRID_PG_{member_index:04d}_{order_index:02d}"
            order_id = connection.execute(
                """
                INSERT INTO wechat_pay_orders (
                    out_trade_no, order_source, product_code, product_name,
                    amount_total, unionid, status, trade_state, paid_at,
                    refunded_amount_total, refund_status
                ) VALUES (%s, 'service_period_checkout', 'sp_grid_pg', 'PG 成员网格',
                          99900, %s, 'paid', 'SUCCESS', CURRENT_TIMESTAMP, %s, %s)
                RETURNING id
                """,
                (out_trade_no, unionid, refunded_amount_total, refund_status),
            ).fetchone()[0]
            event_type = "activated" if order_index == 1 else "renewed"
            connection.execute(
                """
                INSERT INTO service_period_events (
                    event_id, service_product_id, entitlement_id, trade_product_id,
                    order_id, out_trade_no, unionid, event_type, duration_days
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 90)
                """,
                (
                    f"pytest:{event_type}:{out_trade_no}",
                    int(product["id"]),
                    entitlement_id,
                    trade_product_id,
                    order_id,
                    out_trade_no,
                    unionid,
                    event_type,
                ),
            )
            if add_refund_event:
                connection.execute(
                    """
                    INSERT INTO service_period_events (
                        event_id, service_product_id, entitlement_id, trade_product_id,
                        order_id, out_trade_no, unionid, event_type, duration_days
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'refunded', 90)
                    """,
                    (
                        f"pytest:refunded:{out_trade_no}",
                        int(product["id"]),
                        entitlement_id,
                        trade_product_id,
                        order_id,
                        out_trade_no,
                        unionid,
                    ),
                )

        for member_index, valid_order_count in ((1, 1), (2, 2), (3, 3), (4, 4)):
            for order_index in range(1, valid_order_count + 1):
                insert_paid_order_event(member_index=member_index, order_index=order_index)
        insert_paid_order_event(member_index=5, order_index=1)
        insert_paid_order_event(
            member_index=5,
            order_index=2,
            refunded_amount_total=99900,
            refund_status="full_refunded",
        )
        insert_paid_order_event(member_index=6, order_index=1)
        insert_paid_order_event(member_index=6, order_index=2, add_refund_event=True)

    views = repo.list_member_views(product["id"])["items"]
    assert [(view["name"], view["is_default"]) for view in views] == [("表格", True)]
    created_view = repo.create_member_view(product["id"], name="PG 分组", config=empty_view_config(), actor="pytest")["view"]
    updated_view = repo.update_member_view(
        product["id"],
        created_view["id"],
        name="PG 两层分组",
        config={
            **empty_view_config(),
            "groups": [
                {"field": "formally_logged_in", "direction": "asc"},
                {"field": "token_usage", "direction": "asc"},
            ],
            "sorts": [{"field": "member", "direction": "asc"}],
        },
        expected_version=1,
        actor="pytest",
    )["view"]
    assert updated_view["version"] == 2
    with pytest.raises(MemberViewConflictError):
        repo.update_member_view(
            product["id"],
            created_view["id"],
            name="stale",
            config=empty_view_config(),
            expected_version=1,
            actor="pytest",
        )

    updated_alliance = repo.update_member_alliance(product["id"], "union_grid_pg_0010", "PG 联盟已更新")
    assert updated_alliance["member"]["alliance"] == "PG 联盟已更新"
    updated_remark = repo.update_member_remark(product["id"], "union_grid_pg_0010", "PG 备注")
    assert updated_remark["member"]["remark"] == "PG 备注"

    config = updated_view["config"]
    pages = []
    cursor = ""
    while True:
        page = repo.query_member_grid(product["id"], config=config, limit=100, cursor=cursor)
        pages.append(page)
        cursor = page["next_cursor"]
        if not cursor:
            break
    rows = [row for page in pages for row in page["rows"]]
    assert len(rows) == 230
    assert len({row["record_id"] for row in rows}) == 230
    assert pages[0]["total"] is None
    assert pages[0]["has_more"] is True
    assert pages[-1]["has_more"] is False
    assert all(len(row["group_path"]) == 2 for row in rows)
    assert {row["values"]["renewal_count"] for row in rows} == {0, 1, 2, 3}
    rows_by_unionid = {row["unionid"]: row for row in rows}
    # EVER regression: the stored aggregate is 1, but one valid enrollment order means zero renewals.
    assert rows_by_unionid["union_grid_pg_0001"]["values"]["renewal_count"] == 0
    assert rows_by_unionid["union_grid_pg_0002"]["values"]["renewal_count"] == 1
    assert rows_by_unionid["union_grid_pg_0005"]["values"]["renewal_count"] == 0
    assert rows_by_unionid["union_grid_pg_0006"]["values"]["renewal_count"] == 0
    assert any(row["values"]["alliance"] == "PG 联盟已更新" for row in rows)
    assert any(row["values"]["remark"] == "PG 备注" for row in rows)

    renewal_page = repo.query_member_grid(
        product["id"],
        config={
            **empty_view_config(),
            "filter": {
                "logic": "and",
                "conditions": [{"field": "renewal_count", "operator": "gte", "value": 1}],
            },
            "sorts": [{"field": "renewal_count", "direction": "desc"}],
        },
        limit=100,
        cursor="",
    )
    assert [row["values"]["renewal_count"] for row in renewal_page["rows"]] == [3, 2, 1]
