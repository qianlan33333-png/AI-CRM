from __future__ import annotations

import pytest

from aicrm_next.customer_read_model.sidebar_profile_port import (
    SaveSidebarProfileFieldsRequest,
    build_sidebar_customer_profile_projection_port,
)


class _Result:
    def __init__(self, row: dict | None) -> None:
        self._row = row

    def fetchone(self) -> dict | None:
        return self._row


class _Executor:
    def __init__(self, row: dict | None = None) -> None:
        self.row = row or {
            "source": "朋友圈投放",
            "industry": "教育培训",
            "industry_description": "AI 课程咨询",
            "needs_blockers_followup": "补发资料",
            "updated_by": "sales-81",
            "updated_at": "2026-07-25T20:00:00Z",
        }
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def execute(self, sql: str, params: tuple[str, ...]) -> _Result:
        self.calls.append((" ".join(sql.split()), params))
        return _Result(self.row)


def _request(unionid: str = "union-sidebar-81") -> SaveSidebarProfileFieldsRequest:
    return SaveSidebarProfileFieldsRequest(
        unionid=unionid,
        source="朋友圈投放",
        industry="教育培训",
        industry_description="AI 课程咨询",
        needs_blockers_followup="补发资料",
        updated_by="sales-81",
    )


def test_sidebar_profile_fields_use_customer_read_model_owner_upsert() -> None:
    executor = _Executor()

    row = build_sidebar_customer_profile_projection_port().save_fields_dbapi(
        executor,
        request=_request(" union-sidebar-81 "),
    )

    assert row["industry"] == "教育培训"
    assert len(executor.calls) == 1
    sql, params = executor.calls[0]
    assert sql.startswith("INSERT INTO sidebar_customer_profile_fields")
    assert "ON CONFLICT (unionid) DO UPDATE" in sql
    assert params == (
        "union-sidebar-81",
        "朋友圈投放",
        "教育培训",
        "AI 课程咨询",
        "补发资料",
        "sales-81",
    )


def test_sidebar_profile_fields_reject_noncanonical_identity() -> None:
    executor = _Executor()

    with pytest.raises(ValueError, match="unionid is required"):
        build_sidebar_customer_profile_projection_port().save_fields_dbapi(
            executor,
            request=_request(" "),
        )

    assert executor.calls == []
