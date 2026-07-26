from __future__ import annotations

from pathlib import Path

from aicrm_next.platform_foundation.internal_events.consumer_run_write_port import (
    build_internal_event_consumer_run_write_port,
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
        "id": 51,
        "execution_id": "exe-internal-51",
        "lane": "internal_general",
        "status": status,
        "hold_reason": "",
        "version_token": "101",
        "fairness_key": "consumer-1",
    }


def test_internal_event_consumer_owner_supports_command_and_quarantine_paths() -> None:
    port = build_internal_event_consumer_run_write_port()
    immediate = _SQLAlchemyExecutor(row=_row())
    retry = _SQLAlchemyExecutor(row=_row("pending"))
    skip = _SQLAlchemyExecutor(row=_row("skipped"))
    quarantine = _SQLAlchemyExecutor(rows=[{"id": 51}])

    immediate_row = port.make_eligible_now_sqlalchemy(
        immediate,
        item_id=51,
        expected_status="pending",
        expected_version="100",
    )
    retry_row = port.manual_action_sqlalchemy(
        retry,
        action="retry",
        item_id=51,
        expected_status="failed_terminal",
        expected_version="100",
        actor_ref_hash="actor-hash",
        reason="operator retry",
        attempt_id="iea-retry-51",
    )
    skip_row = port.manual_action_sqlalchemy(
        skip,
        action="skip",
        item_id=51,
        expected_status="pending",
        expected_version="100",
        actor_ref_hash="actor-hash",
        reason="operator skip",
        attempt_id="iea-skip-51",
    )
    quarantined = port.quarantine_superseded_signal_owner_sqlalchemy(
        quarantine,
        tenant_id="aicrm",
        idempotency_key="customer_read_model.refresh.requested:7",
        consumer_name="customer_read_model_refresh_intent_consumer",
    )

    assert immediate_row == _row()
    assert "xmin::text = :expected_version" in immediate.calls[0][0]
    assert retry_row == _row("pending")
    assert "INSERT INTO internal_event_consumer_attempt" in retry.calls[0][0]
    assert retry.calls[0][1]["actor_ref_hash"] == "actor-hash"
    assert skip_row == _row("skipped")
    assert "last_error_code = 'manual_skip'" in skip.calls[0][0]
    assert quarantined == [{"id": 51}]
    assert "hold_reason = 'superseded_missing_signal_owner'" in quarantine.calls[0][0]


def test_internal_event_consumer_owner_supports_claim_recovery_and_renew_without_commit() -> None:
    port = build_internal_event_consumer_run_write_port()
    claim = _DBAPIExecutor(_row("running"))
    recover = _DBAPIExecutor()
    renew = _DBAPIExecutor({"id": 51})

    claimed = port.claim_dbapi(
        claim,
        lane="internal_general",
        generation=9,
        worker_id="internal-worker-1",
        lease_token="lease-51",
        lease_seconds=30,
    )
    port.recover_expired_dbapi(recover, lane="internal_general")
    renewed = port.renew_lease_dbapi(
        renew,
        item_id=51,
        lease_token="lease-51",
        generation=9,
        lease_seconds=30,
    )

    assert claimed == _row("running")
    assert claim.calls[0][0].startswith("WITH candidate AS")
    assert "UPDATE internal_event_consumer_run run" in claim.calls[0][0]
    assert recover.calls[0][0].startswith("UPDATE internal_event_consumer_run")
    assert renewed is True
    assert "AND status = 'running'" in renew.calls[0][0]
    assert not hasattr(claim, "commit")
    assert not hasattr(recover, "commit")
    assert not hasattr(renew, "commit")


def test_internal_event_consumer_run_write_sql_is_confined_to_owner_package() -> None:
    offenders: list[str] = []
    for path in sorted((ROOT / "aicrm_next").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if not any(
            marker in source
            for marker in (
                "INSERT INTO internal_event_consumer_run",
                "UPDATE internal_event_consumer_run",
                "DELETE FROM internal_event_consumer_run",
            )
        ):
            continue
        relative = path.relative_to(ROOT).as_posix()
        if not relative.startswith("aicrm_next/platform_foundation/internal_events/"):
            offenders.append(relative)

    assert offenders == []
