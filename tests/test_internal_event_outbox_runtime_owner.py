from __future__ import annotations

from pathlib import Path

from aicrm_next.platform.platform_foundation.internal_events.outbox_runtime_write_port import (
    build_internal_event_outbox_runtime_write_port,
)


ROOT = Path(__file__).resolve().parents[1]


class _Result:
    def __init__(self, row=None, rows=None) -> None:
        self._row = row
        self._rows = list(rows or [])

    def mappings(self):
        return self

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _SQLAlchemyExecutor:
    def __init__(self, *, row=None, rows=None) -> None:
        self.row = row
        self.rows = list(rows or [])
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, params):
        self.calls.append((" ".join(str(statement).split()), dict(params)))
        return _Result(self.row, self.rows)


class _DBAPIExecutor:
    def __init__(self, row=None) -> None:
        self.row = row
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, statement, params):
        self.calls.append((" ".join(statement.split()), tuple(params)))
        return _Result(self.row)


def _row(status: str = "pending") -> dict:
    return {
        "id": 61,
        "execution_id": "exe-outbox-61",
        "lane": "internal_general",
        "status": status,
        "hold_reason": "",
        "version_token": "111",
        "fairness_key": "event-type-1",
    }


def test_internal_event_outbox_owner_supports_command_and_quarantine_paths() -> None:
    port = build_internal_event_outbox_runtime_write_port()
    immediate = _SQLAlchemyExecutor(row=_row())
    quarantine = _SQLAlchemyExecutor(rows=[{"id": 61}])

    immediate_row = port.make_eligible_now_sqlalchemy(
        immediate,
        item_id=61,
        expected_status="pending",
        expected_version="110",
    )
    quarantined = port.quarantine_superseded_signal_owner_sqlalchemy(
        quarantine,
        tenant_id="aicrm",
        idempotency_key="customer_read_model.refresh.requested:9",
    )

    assert immediate_row == _row()
    assert "xmin::text = :expected_version" in immediate.calls[0][0]
    assert quarantined == [{"id": 61}]
    assert "hold_reason = 'superseded_missing_signal_owner'" in quarantine.calls[0][0]


def test_internal_event_outbox_owner_supports_claim_recovery_and_renew_without_commit() -> None:
    port = build_internal_event_outbox_runtime_write_port()
    claim = _DBAPIExecutor(_row("running"))
    recover = _DBAPIExecutor()
    renew = _DBAPIExecutor({"id": 61})

    claimed = port.claim_dbapi(
        claim,
        lane="internal_general",
        generation=11,
        worker_id="outbox-worker-1",
        lease_token="outbox-lease-61",
        lease_seconds=30,
    )
    port.recover_expired_dbapi(recover, lane="internal_general")
    renewed = port.renew_lease_dbapi(
        renew,
        item_id=61,
        lease_token="outbox-lease-61",
        generation=11,
        lease_seconds=30,
    )

    assert claimed == _row("running")
    assert claim.calls[0][0].startswith("WITH candidate AS")
    assert "UPDATE internal_event_outbox outbox" in claim.calls[0][0]
    assert recover.calls[0][0].startswith("UPDATE internal_event_outbox")
    assert renewed is True
    assert "AND status = 'running'" in renew.calls[0][0]
    assert not hasattr(claim, "commit")
    assert not hasattr(recover, "commit")
    assert not hasattr(renew, "commit")


def test_internal_event_outbox_write_sql_is_confined_to_owner_package() -> None:
    offenders: list[str] = []
    for path in sorted((ROOT / "aicrm_next").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if not any(
            marker in source
            for marker in (
                "INSERT INTO internal_event_outbox",
                "UPDATE internal_event_outbox",
                "DELETE FROM internal_event_outbox",
            )
        ):
            continue
        relative = path.relative_to(ROOT).as_posix()
        if not relative.startswith("aicrm_next/platform/platform_foundation/internal_events/"):
            offenders.append(relative)

    assert offenders == []
