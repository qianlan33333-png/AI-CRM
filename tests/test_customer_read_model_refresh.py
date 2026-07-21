from __future__ import annotations

import json
from datetime import timedelta

from aicrm_next.customer_read_model.refresh_intents import CUSTOMER_SOURCE_EVENTS
from aicrm_next.customer_read_model.repo import _coerce_datetime
from aicrm_next.customer_read_model.refresh import CustomerReadModelRefreshService
from scripts import run_customer_read_model_refresh as refresh_cli
from scripts.run_customer_read_model_refresh import wait_for_refresh_completion


class _Source:
    def __init__(self, customers: list[dict]) -> None:
        self.customers = customers

    def count_customers(self, filters=None) -> int:
        return len(self.customers)

    def list_customers(self, filters=None, *, limit=None, offset=0) -> list[dict]:
        return list(self.customers[offset : offset + limit if limit is not None else None])

    def snapshot_recent_messages_by_unionid(self, unionids, *, per_customer_limit=100):
        assert unionids == ["union_1", "union_2"]
        assert per_customer_limit == 100
        return {
            "union_1": [
                {
                    "msgid": "message_1",
                    "unionid": "union_1",
                    "msgtype": "text",
                    "content": "hello",
                    "send_time": "2026-07-13T00:00:00+00:00",
                    "source_id": "10",
                }
            ]
        }


class _Target:
    def __init__(self, count: int = 1) -> None:
        self.count = count
        self.replace_calls: list[dict] = []

    def count_customers(self, filters=None) -> int:
        return self.count

    def replace_all(self, **kwargs) -> None:
        self.replace_calls.append(kwargs)
        self.count = len(kwargs["customers"])


class _RecordingSession:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, statement, params) -> None:
        self.calls.append({"sql": str(statement), "params": dict(params)})


class _BeginContext:
    def __init__(self, session: _RecordingSession) -> None:
        self.session = session

    def __enter__(self) -> _RecordingSession:
        return self.session

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _SessionFactory:
    def __init__(self) -> None:
        self.session = _RecordingSession()

    def begin(self) -> _BeginContext:
        return _BeginContext(self.session)


def _customers() -> list[dict]:
    return [
        {
            "unionid": "union_1",
            "external_userid": "external_1",
            "customer_name": "one",
            "updated_at": "2026-07-13T00:00:00+00:00",
            "created_at": "2026-07-13T00:00:00+00:00",
        },
        {
            "unionid": "union_2",
            "external_userid": "",
            "customer_name": "two",
            "updated_at": "2026-07-13T00:00:00+00:00",
            "created_at": "2026-07-13T00:00:00+00:00",
        },
    ]


def test_customer_read_model_refresh_is_dry_run_by_default() -> None:
    source = _Source(_customers())
    target = _Target(count=1)
    sessions = _SessionFactory()

    result = CustomerReadModelRefreshService(
        source_repo=source,
        target_repo=target,
        session_factory=sessions,
    ).run()

    assert result.ok is True
    assert result.dry_run is True
    assert result.source_count == 2
    assert result.target_count_before == 1
    assert result.target_count_after == 1
    assert target.replace_calls == []
    assert sessions.session.calls == []


def test_customer_read_model_refresh_replaces_projection_and_records_count_only_state() -> None:
    source = _Source(_customers())
    target = _Target(count=1)
    sessions = _SessionFactory()

    result = CustomerReadModelRefreshService(
        source_repo=source,
        target_repo=target,
        session_factory=sessions,
    ).run(dry_run=False)

    assert result.ok is True
    assert result.dry_run is False
    assert result.source_count == result.target_count_after == 2
    assert len(target.replace_calls) == 1
    replacement = target.replace_calls[0]
    assert replacement["messages_by_external_userid"]["external_1"][0]["msgid"] == "message_1"
    assert replacement["messages_by_external_userid"]["union_2"] == []
    assert replacement["timeline_by_external_userid"]["external_1"][0]["event_type"] == "message"
    assert len(sessions.session.calls) == 1
    assert sessions.session.calls[0]["params"]["source_count"] == 2
    assert sessions.session.calls[0]["params"]["target_count"] == 2
    assert "customer_read_model_refresh_state" in sessions.session.calls[0]["sql"]


def test_customer_read_model_refresh_refuses_duplicate_or_empty_unionid() -> None:
    duplicate = _customers()
    duplicate[1]["unionid"] = "union_1"
    empty = _customers()
    empty[1]["unionid"] = ""

    for customers, reason in (
        (duplicate, "customer_read_model_source_contains_duplicate_unionid"),
        (empty, "customer_read_model_source_contains_empty_unionid"),
    ):
        service = CustomerReadModelRefreshService(
            source_repo=_Source(customers),
            target_repo=_Target(),
            session_factory=_SessionFactory(),
        )
        try:
            service.run(dry_run=False)
        except RuntimeError as exc:
            assert str(exc) == reason
        else:  # pragma: no cover - fail closed contract
            raise AssertionError("refresh must reject invalid identity keys")


def test_customer_read_model_accepts_postgres_whole_hour_timezone_offset() -> None:
    whole_hour = _coerce_datetime("2026-07-16 18:02:25.720198+08")
    variable_fraction = _coerce_datetime("2026-07-16 18:01:43.6157+08:00")

    assert whole_hour.utcoffset() == timedelta(hours=8)
    assert whole_hour.isoformat() == "2026-07-16T18:02:25.720198+08:00"
    assert variable_fraction.utcoffset() == timedelta(hours=8)
    assert variable_fraction.isoformat() == "2026-07-16T18:01:43.615700+08:00"


def test_customer_read_model_dirty_sources_cover_every_freshness_guard_source() -> None:
    assert CUSTOMER_SOURCE_EVENTS == (
        "channel_entry.entered",
        "customer.phone_bound",
        "identity.resolved",
        "message_archive.batch_ingested",
        "payment.succeeded",
        "questionnaire.submitted",
    )


class _IntentStateRepository:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = list(rows)
        self._last = dict(rows[-1]) if rows else {}

    def get(self) -> dict:
        if self._rows:
            self._last = dict(self._rows.pop(0))
        return dict(self._last)


def test_deploy_wait_observes_worker_owned_refresh_and_continuation() -> None:
    result = wait_for_refresh_completion(
        _IntentStateRepository(
            [
                {"dirty_generation": 7, "completed_generation": 6, "status": "running"},
                {"dirty_generation": 8, "completed_generation": 7, "status": "waiting"},
                {"dirty_generation": 8, "completed_generation": 8, "status": "idle"},
            ]
        ),
        target_generation=7,
        timeout_seconds=1,
        poll_seconds=0.01,
    )

    assert result == {
        "ok": True,
        "target_generation": 7,
        "dirty_generation": 8,
        "completed_generation": 8,
        "status": "idle",
    }


def test_deploy_wait_fails_closed_for_blocked_refresh() -> None:
    result = wait_for_refresh_completion(
        _IntentStateRepository(
            [{"dirty_generation": 9, "completed_generation": 8, "status": "blocked"}]
        ),
        target_generation=9,
        timeout_seconds=1,
        poll_seconds=0.01,
    )

    assert result["ok"] is False
    assert result["reason"] == "customer_read_model_refresh_blocked"


def test_release_refresh_explicitly_recovers_blocked_intent_with_current_version(
    monkeypatch,
    capsys,
) -> None:
    calls: list[dict] = []

    class Repository:
        def get(self):
            return {"status": "blocked", "row_version": 17}

    class Service:
        def __init__(self, repository):
            assert isinstance(repository, Repository)

        def request_refresh(self, **kwargs):
            calls.append(dict(kwargs))
            return {"ok": True, "generation": 18, "signal_created": True, "blocked_recovered": True}

    monkeypatch.setattr(refresh_cli, "CustomerReadModelRefreshIntentRepository", Repository)
    monkeypatch.setattr(refresh_cli, "CustomerReadModelRefreshIntentService", Service)
    monkeypatch.setenv("AICRM_CUSTOMER_READ_MODEL_RELEASE_REFRESH_AUTHORIZED", "1")

    exit_code = refresh_cli.main(
        [
            "--execute",
            "--release-refresh",
            "--source-key",
            "deploy_runtime:123:1",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["release_refresh"] is True
    assert payload["blocked_recovered"] is True
    assert calls == [
        {
            "source_event_key": "deploy_runtime:123:1",
            "source_event_type": "customer_read_model.release_refresh",
            "recover_blocked": True,
            "expected_row_version": 17,
        }
    ]


def test_release_refresh_fails_closed_without_deploy_authorization(monkeypatch, capsys) -> None:
    monkeypatch.delenv("AICRM_CUSTOMER_READ_MODEL_RELEASE_REFRESH_AUTHORIZED", raising=False)

    exit_code = refresh_cli.main(
        [
            "--execute",
            "--release-refresh",
            "--source-key",
            "deploy_runtime:123:1",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["reason"] == "customer_read_model_release_refresh_not_authorized"
