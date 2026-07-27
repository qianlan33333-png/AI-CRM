from __future__ import annotations

from pathlib import Path

from aicrm_next.platform.platform_foundation.webhook_inbox.runtime_write_port import (
    build_webhook_inbox_runtime_write_port,
)


ROOT = Path(__file__).resolve().parents[1]


class _Result:
    def __init__(self, row) -> None:
        self._row = row

    def mappings(self):
        return self

    def fetchone(self):
        return self._row


class _SQLAlchemyExecutor:
    def __init__(self, row) -> None:
        self.row = row
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, params):
        self.calls.append((" ".join(str(statement).split()), dict(params)))
        return _Result(self.row)


class _DBAPIExecutor:
    def __init__(self, row=None) -> None:
        self.row = row
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, statement, params):
        self.calls.append((" ".join(statement.split()), tuple(params)))
        return _Result(self.row)


def _row(status: str = "received") -> dict:
    return {
        "id": 41,
        "execution_id": "exe-41",
        "lane": "webhook_inbox",
        "status": status,
        "hold_reason": "",
        "version_token": "91",
        "fairness_key": "corp-1",
    }


def test_webhook_runtime_owner_supports_command_cas_paths() -> None:
    port = build_webhook_inbox_runtime_write_port()
    immediate = _SQLAlchemyExecutor(_row())
    retry = _SQLAlchemyExecutor(_row("failed_retryable"))
    skip = _SQLAlchemyExecutor(_row("ignored"))

    immediate_row = port.make_eligible_now_sqlalchemy(
        immediate,
        item_id=41,
        expected_status="received",
        expected_version="90",
    )
    retry_row = port.manual_action_sqlalchemy(
        retry,
        action="retry",
        item_id=41,
        expected_status="failed_terminal",
        expected_version="90",
        reason="operator retry",
    )
    skip_row = port.manual_action_sqlalchemy(
        skip,
        action="skip",
        item_id=41,
        expected_status="received",
        expected_version="90",
        reason="operator skip",
    )

    assert immediate_row == _row()
    assert "xmin::text = :expected_version" in immediate.calls[0][0]
    assert retry_row == _row("failed_retryable")
    assert "last_error_code = 'operator_retry'" in retry.calls[0][0]
    assert retry.calls[0][1]["reason"] == "operator retry"
    assert skip_row == _row("ignored")
    assert "last_error_code = 'operator_skip'" in skip.calls[0][0]


def test_webhook_runtime_owner_supports_claim_recovery_and_renew_without_commit() -> None:
    port = build_webhook_inbox_runtime_write_port()
    claim = _DBAPIExecutor(_row("processing"))
    recover = _DBAPIExecutor()
    renew = _DBAPIExecutor({"id": 41})

    claimed = port.claim_dbapi(
        claim,
        lane="webhook_inbox",
        generation=7,
        worker_id="worker-1",
        lease_token="lease-1",
        lease_seconds=30,
    )
    port.recover_expired_dbapi(recover, lane="webhook_inbox")
    renewed = port.renew_lease_dbapi(
        renew,
        item_id=41,
        lease_token="lease-1",
        generation=7,
        lease_seconds=30,
    )

    assert claimed == _row("processing")
    assert claim.calls[0][0].startswith("WITH candidate AS")
    assert "UPDATE webhook_inbox inbox" in claim.calls[0][0]
    assert recover.calls[0][0].startswith("UPDATE webhook_inbox")
    assert renewed is True
    assert "AND status = 'processing'" in renew.calls[0][0]
    assert not hasattr(claim, "commit")
    assert not hasattr(recover, "commit")
    assert not hasattr(renew, "commit")


def test_webhook_inbox_write_sql_is_confined_to_owner_package() -> None:
    offenders: list[str] = []
    for path in sorted((ROOT / "aicrm_next").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if not any(
            marker in source
            for marker in (
                "INSERT INTO webhook_inbox",
                "UPDATE webhook_inbox",
                "DELETE FROM webhook_inbox",
            )
        ):
            continue
        relative = path.relative_to(ROOT).as_posix()
        if not relative.startswith("aicrm_next/platform/platform_foundation/webhook_inbox/"):
            offenders.append(relative)

    assert offenders == []
