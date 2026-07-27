from __future__ import annotations

from pathlib import Path

from aicrm_next.platform.platform_foundation.external_effects.claim_policy import (
    external_claim_scope_predicate,
)
from aicrm_next.platform.platform_foundation.external_effects.runtime_write_port import (
    build_external_effect_runtime_write_port,
)


ROOT = Path(__file__).resolve().parents[1]


class _Result:
    def __init__(self, *, row=None, rows=None, scalar=None) -> None:
        self._row = row
        self._rows = list(rows or [])
        self._scalar = scalar

    def mappings(self):
        return self

    def scalars(self):
        return self

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows

    def all(self):
        if self._rows:
            return self._rows
        return [self._scalar] if self._scalar is not None else []

    def scalar_one_or_none(self):
        return self._scalar


class _SQLAlchemyExecutor:
    def __init__(self, result: _Result) -> None:
        self.result = result
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, params):
        self.calls.append((" ".join(str(statement).split()), dict(params)))
        return self.result


class _DBAPIExecutor:
    def __init__(self, result: _Result) -> None:
        self.result = result
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, statement, params):
        self.calls.append((" ".join(str(statement).split()), tuple(params)))
        return self.result


def _row(status: str = "queued") -> dict:
    return {
        "id": 71,
        "execution_id": "exe-effect-71",
        "lane": "wecom_interactive",
        "status": status,
        "hold_reason": "",
        "version_token": "6",
        "fairness_key": "tenant-1",
    }


def test_external_effect_owner_supports_operator_and_graph_mutations() -> None:
    port = build_external_effect_runtime_write_port()
    immediate = _SQLAlchemyExecutor(_Result(row=_row()))
    retry = _SQLAlchemyExecutor(_Result(row=_row()))
    request_cancel = _SQLAlchemyExecutor(_Result(rows=[71]))
    cancel = _SQLAlchemyExecutor(_Result(rows=[_row("cancelled")]))
    release = _SQLAlchemyExecutor(_Result(scalar=71))

    assert port.make_eligible_now_sqlalchemy(
        immediate,
        item_id=71,
        expected_status="queued",
        expected_version="5",
    ) == _row()
    assert port.manual_action_sqlalchemy(
        retry,
        action="retry",
        item_id=71,
        expected_status="failed_terminal",
        expected_version="5",
        actor="operator-1",
        reason="manual retry",
    ) == _row()
    assert port.request_cancel_dispatching_sqlalchemy(
        request_cancel,
        job_ids=[72, 71, 71],
        exclude_job_id=72,
        actor="operator-1",
        reason="graph cancelled",
    ) == [71]
    assert port.cancel_pre_provider_sqlalchemy(
        cancel,
        job_ids=[71],
        exclude_job_id=0,
        actor="operator-1",
        reason="graph cancelled",
    ) == [_row("cancelled")]
    assert port.release_planned_sqlalchemy(
        release,
        job_id=71,
        payload_json='{"text":"ready"}',
        payload_summary_json='{"dependencies_resolved":true}',
        available_at_mode="scheduled",
    ) == 71

    assert "provider_call_started_at IS NULL" in immediate.calls[0][0]
    assert "row_version::text = :expected_version" in retry.calls[0][0]
    assert request_cancel.calls[0][1]["job_ids"] == [71, 72]
    assert "external_effect_attempt" in cancel.calls[0][0]
    assert "available_at = scheduled_at" in release.calls[0][0]


def test_external_effect_owner_supports_claim_and_heartbeat_without_commit() -> None:
    port = build_external_effect_runtime_write_port()
    claim = _DBAPIExecutor(_Result(row=_row("dispatching")))
    renew = _DBAPIExecutor(_Result(row={"id": 71}))

    claimed = port.claim_dbapi(
        claim,
        lane="wecom_interactive",
        generation=12,
        worker_id="external-worker-1",
        lease_token="lease-71",
        lease_seconds=30,
        test_only=True,
    )
    renewed = port.renew_lease_dbapi(
        renew,
        item_id=71,
        lease_token="lease-71",
        generation=12,
        lease_seconds=30,
    )

    assert claimed == _row("dispatching")
    assert claim.calls[0][0].startswith("WITH candidate AS")
    assert "execution_scope" in claim.calls[0][0]
    assert renewed is True
    assert "AND status = 'dispatching'" in renew.calls[0][0]
    assert not hasattr(claim, "commit")
    assert not hasattr(renew, "commit")


def test_external_claim_policy_is_owned_with_the_external_effect_capability() -> None:
    predicate = external_claim_scope_predicate(
        row_alias="effect",
        scope_expression="control.external_claim_scope",
    )

    assert "allowlisted_canary" in predicate
    assert "authorized_job_id" in predicate
    assert "control.external_claim_scope" in predicate


def test_external_effect_write_sql_is_confined_to_owner_package() -> None:
    offenders: list[str] = []
    markers = (
        "INSERT INTO external_effect_job",
        "UPDATE external_effect_job",
        "DELETE FROM external_effect_job",
        "INSERT INTO external_effect_attempt",
        "UPDATE external_effect_attempt",
        "DELETE FROM external_effect_attempt",
    )
    for path in sorted((ROOT / "aicrm_next").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if not any(marker in source for marker in markers):
            continue
        relative = path.relative_to(ROOT).as_posix()
        if not relative.startswith("aicrm_next/platform/platform_foundation/external_effects/"):
            offenders.append(relative)

    assert offenders == []
