from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

from aicrm_next.customer_read_model.sidebar_profile_port import (
    SaveSidebarProfileFieldsRequest,
    build_sidebar_customer_profile_projection_port,
)


def _request(industry: str) -> SaveSidebarProfileFieldsRequest:
    return SaveSidebarProfileFieldsRequest(
        unionid="union-sidebar-profile-owner",
        source="企微侧边栏",
        industry=industry,
        industry_description="owner port integration",
        needs_blockers_followup="",
        updated_by="sales-owner",
    )


def test_sidebar_profile_owner_upsert_is_idempotent_in_real_postgres(next_pg_schema) -> None:
    port = build_sidebar_customer_profile_projection_port()
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as connection:
        first = port.save_fields_dbapi(connection, request=_request("教育"))
        second = port.save_fields_dbapi(connection, request=_request("企业服务"))
        count = connection.execute(
            "SELECT count(*) AS count FROM sidebar_customer_profile_fields WHERE unionid = %s",
            ("union-sidebar-profile-owner",),
        ).fetchone()["count"]
        connection.rollback()

    assert first["industry"] == "教育"
    assert second["industry"] == "企业服务"
    assert count == 1
