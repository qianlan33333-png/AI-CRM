from __future__ import annotations

import json

from aicrm_next.identity_contact import resolution_effects
from aicrm_next.identity_contact.write_repository import PostgresIdentityWriteRepository


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), tuple(params)))
        return _Result(
            {
                "id": 41,
                "source_type": "sidebar_bind_mobile",
                "source_key": params[0],
                "external_userid": params[1],
                "mobile": params[2],
            }
        )


def test_sidebar_resolution_enqueue_is_owned_by_identity_port(monkeypatch) -> None:
    connection = _Connection()
    planned_calls: list[dict] = []

    def plan_effect(conn, row, **kwargs):
        planned_calls.append({"conn": conn, "row": row, **kwargs})
        return {"external_effect_job_id": 92, "execution_id": "exec-92"}

    monkeypatch.setattr(resolution_effects, "plan_identity_resolution_effect", plan_effect)

    result = PostgresIdentityWriteRepository().enqueue_sidebar_identity_resolution(
        connection,
        command_id="cmd-41",
        external_userid="wm-41",
        mobile="13800138041",
        owner_userid="sales-41",
        bind_by_userid="sales-41",
    )

    assert result == {
        "status": "pending",
        "reason": "identity_pending_unionid",
        "source_key": "sidebar_bind_mobile:wm-41:cmd-41",
        "external_effect_job_id": 92,
        "execution_id": "exec-92",
    }
    sql, params = connection.calls[0]
    assert sql.startswith("INSERT INTO crm_user_identity_resolution_queue")
    assert params[:3] == ("sidebar_bind_mobile:wm-41:cmd-41", "wm-41", "13800138041")
    assert json.loads(params[3]) == {
        "bind_by_userid": "sales-41",
        "external_userid": "wm-41",
        "mobile": "13800138041",
        "owner_userid": "sales-41",
        "source": "sidebar_bind_mobile",
    }
    assert planned_calls == [
        {
            "conn": connection,
            "row": {
                "id": 41,
                "source_type": "sidebar_bind_mobile",
                "source_key": "sidebar_bind_mobile:wm-41:cmd-41",
                "external_userid": "wm-41",
                "mobile": "13800138041",
            },
            "parent_execution_id": "cmd-41",
            "source_route": "sidebar_write.identity_resolution.enqueue",
        }
    ]
