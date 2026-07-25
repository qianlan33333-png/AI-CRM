from __future__ import annotations

from aicrm_next.identity_contact.event_log_port import build_identity_event_log_port


class _Cursor:
    def __init__(self, connection, response) -> None:
        self.connection = connection
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, sql, params=()) -> None:
        self.connection.calls.append((" ".join(sql.split()), tuple(params)))

    def fetchone(self):
        if isinstance(self.response, list):
            return self.response[0] if self.response else None
        return self.response

    def fetchall(self):
        return self.response if isinstance(self.response, list) else []


class _Connection:
    def __init__(self, response=None) -> None:
        self.response = response
        self.calls: list[tuple[str, tuple]] = []
        self.commit_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def cursor(self):
        return _Cursor(self, self.response)

    def commit(self) -> None:
        self.commit_count += 1


class _ConnectionFactory:
    def __init__(self, connections) -> None:
        self.connections = list(connections)

    def __call__(self):
        return self.connections.pop(0)


def test_identity_event_log_port_preserves_callback_audit_contract() -> None:
    log_connection = _Connection({"id": 91, "event_key": "event-91", "process_status": "pending"})
    get_connection = _Connection({"id": 91, "event_key": "event-91"})
    status_connection = _Connection()
    identity_connection = _Connection()
    list_connection = _Connection([{"id": 91, "process_status": "success"}])
    port = build_identity_event_log_port(
        connection_factory=_ConnectionFactory(
            [
                log_connection,
                get_connection,
                status_connection,
                identity_connection,
                list_connection,
            ]
        )
    )

    logged = port.log_external_contact_event(
        corp_id="corp-91",
        event_type="change_external_contact",
        change_type="add_external_contact",
        external_userid="external-91",
        user_id="owner-91",
        event_time=91,
        event_key="event-91",
        payload_xml="<xml />",
        payload_json={"State": "scene-91"},
    )
    fetched = port.get_external_contact_event_log(91)
    port.mark_event_status(91, "success")
    port.record_identity_sync_result(
        91,
        status="success",
        response_json={"unionid_present": True},
    )
    recent = port.list_recent_events("scene-91", limit=999)

    assert logged == {"id": 91, "event_key": "event-91", "process_status": "pending"}
    assert fetched == {"id": 91, "event_key": "event-91"}
    assert recent == [{"id": 91, "process_status": "success"}]
    assert log_connection.calls[0][0].startswith("INSERT INTO wecom_external_contact_event_logs")
    assert log_connection.calls[0][1][:8] == (
        "corp-91",
        "change_external_contact",
        "add_external_contact",
        "external-91",
        "owner-91",
        91,
        "event-91",
        "<xml />",
    )
    assert status_connection.calls[0][1] == ("success", "", 91)
    assert identity_connection.calls[0][1][:3] == ("success", "", "")
    assert list_connection.calls[0][1] == ("scene-91", 100)
    assert log_connection.commit_count == 1
    assert status_connection.commit_count == 1
    assert identity_connection.commit_count == 1


def test_identity_event_log_port_returns_empty_rows_without_inventing_state() -> None:
    log_connection = _Connection(None)
    get_connection = _Connection(None)
    port = build_identity_event_log_port(
        connection_factory=_ConnectionFactory([log_connection, get_connection])
    )

    logged = port.log_external_contact_event(
        corp_id="corp-92",
        event_type="change_external_contact",
        change_type="edit_external_contact",
        external_userid="external-92",
        user_id="owner-92",
        event_time=92,
        event_key="event-92",
        payload_xml="",
        payload_json={},
    )

    assert logged == {}
    assert port.get_external_contact_event_log(92) is None
