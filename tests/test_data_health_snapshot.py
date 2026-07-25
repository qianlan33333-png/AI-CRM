from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

import pytest

from aicrm_next.data_health.dto import DataHealthCheckResult
from aicrm_next.data_health.snapshot_repository import DataHealthSnapshotRecord
from aicrm_next.data_health.snapshot_service import capture_data_health_snapshot


def _check(check_id: str, status: str = "ok") -> DataHealthCheckResult:
    severity = {"ok": "green", "warn": "yellow", "fail": "red", "not_applicable": "gray"}[status]
    return DataHealthCheckResult(
        check_id=check_id,
        title=check_id,
        status=status,
        severity=severity,
        summary=f"{check_id} summary",
        evidence={"count": 1},
    )


class _RecordingRepository:
    def __init__(self) -> None:
        self.snapshots: list[DataHealthSnapshotRecord] = []

    def replace_latest(self, snapshot: DataHealthSnapshotRecord) -> None:
        self.snapshots.append(snapshot)


class _LoadingRepository:
    def __init__(self, snapshot: DataHealthSnapshotRecord | None) -> None:
        self.snapshot = snapshot
        self.load_count = 0

    def load_latest(self) -> DataHealthSnapshotRecord | None:
        self.load_count += 1
        return self.snapshot


class _FailingLoadingRepository:
    def load_latest(self) -> DataHealthSnapshotRecord | None:
        raise RuntimeError("postgresql://user:secret@database/customer-id")


def _snapshot(*, captured_at: datetime) -> DataHealthSnapshotRecord:
    return DataHealthSnapshotRecord(
        generation_id="12345678-1234-5678-1234-567812345678",
        source_release_sha="c" * 40,
        overall_status="warn",
        checks=(_check("first"), _check("second", "warn")),
        duration_ms=42,
        captured_at=captured_at,
    )


def test_capture_data_health_snapshot_writes_one_complete_generation() -> None:
    repository = _RecordingRepository()
    moments = iter((10.0, 10.125))
    captured_at = datetime(2026, 7, 25, 0, 0, tzinfo=timezone.utc)
    generation_id = UUID("12345678-1234-5678-1234-567812345678")

    result = capture_data_health_snapshot(
        repository=repository,  # type: ignore[arg-type]
        check_runner=lambda: [_check("first"), _check("second", "warn")],
        release_sha="A" * 40,
        generation_id=generation_id,
        now=lambda: captured_at,
        monotonic=lambda: next(moments),
    )

    assert repository.snapshots == [result]
    assert result.generation_id == str(generation_id)
    assert result.source_release_sha == "a" * 40
    assert result.overall_status == "warn"
    assert result.duration_ms == 125
    assert result.captured_at == captured_at
    assert [check.check_id for check in result.checks] == ["first", "second"]


def test_capture_pins_release_sha_before_long_running_checks(monkeypatch) -> None:
    from aicrm_next.data_health import snapshot_service

    repository = _RecordingRepository()
    order: list[str] = []

    def _release_sha() -> str:
        order.append("release")
        return "d" * 40

    def _checks() -> list[DataHealthCheckResult]:
        assert order == ["release"]
        order.append("checks")
        return [_check("safe")]

    monkeypatch.setattr(snapshot_service, "current_release_sha", _release_sha)

    result = capture_data_health_snapshot(
        repository=repository,  # type: ignore[arg-type]
        check_runner=_checks,
    )

    assert order == ["release", "checks"]
    assert result.source_release_sha == "d" * 40


@pytest.mark.parametrize(
    ("checks", "error_code"),
    [
        ([], "data_health_snapshot_checks_empty"),
        ([_check("duplicate"), _check("duplicate")], "data_health_snapshot_check_ids_not_unique"),
    ],
)
def test_capture_data_health_snapshot_rejects_incomplete_generations(
    checks: list[DataHealthCheckResult],
    error_code: str,
) -> None:
    repository = _RecordingRepository()

    with pytest.raises(ValueError, match=error_code):
        capture_data_health_snapshot(
            repository=repository,  # type: ignore[arg-type]
            check_runner=lambda: checks,
        )

    assert repository.snapshots == []


def test_capture_data_health_snapshot_rejects_naive_capture_time() -> None:
    repository = _RecordingRepository()

    with pytest.raises(ValueError, match="captured_at_must_be_timezone_aware"):
        capture_data_health_snapshot(
            repository=repository,  # type: ignore[arg-type]
            check_runner=lambda: [_check("one")],
            now=lambda: datetime(2026, 7, 25),
        )

    assert repository.snapshots == []


def test_capture_data_health_snapshot_rejects_raw_identity_evidence() -> None:
    repository = _RecordingRepository()
    unsafe = _check("unsafe").model_copy(update={"evidence": {"external_userid": "wm-secret"}})

    with pytest.raises(ValueError, match="raw_identity_key_forbidden"):
        capture_data_health_snapshot(
            repository=repository,  # type: ignore[arg-type]
            check_runner=lambda: [unsafe],
        )

    assert repository.snapshots == []


def test_refresh_cli_prints_only_snapshot_metadata(monkeypatch, capsys) -> None:
    from scripts.ops import refresh_data_health_snapshot

    snapshot = DataHealthSnapshotRecord(
        generation_id="12345678-1234-5678-1234-567812345678",
        source_release_sha="b" * 40,
        overall_status="ok",
        checks=(_check("safe"),),
        duration_ms=42,
        captured_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(refresh_data_health_snapshot, "capture_data_health_snapshot", lambda: snapshot)

    assert refresh_data_health_snapshot.run() == 0

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": True,
        "generation_id": snapshot.generation_id,
        "source_release_sha": snapshot.source_release_sha,
        "overall_status": "ok",
        "check_count": 1,
        "duration_ms": 42,
        "captured_at": "2026-07-25T00:00:00+00:00",
    }
    assert "summary" not in output
    assert "evidence" not in output


def test_refresh_cli_rejects_generation_from_another_release(monkeypatch, capsys) -> None:
    from scripts.ops import refresh_data_health_snapshot

    snapshot = DataHealthSnapshotRecord(
        generation_id="12345678-1234-5678-1234-567812345678",
        source_release_sha="b" * 40,
        overall_status="ok",
        checks=(_check("safe"),),
        duration_ms=42,
        captured_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(refresh_data_health_snapshot, "capture_data_health_snapshot", lambda: snapshot)

    assert refresh_data_health_snapshot.run(expected_release_sha="c" * 40) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"ok": False, "error": "ValueError"}


def test_refresh_cli_redacts_exception_details(monkeypatch, capsys) -> None:
    from scripts.ops import refresh_data_health_snapshot

    def _fail() -> DataHealthSnapshotRecord:
        raise RuntimeError("postgresql://user:secret@database/customer-id")

    monkeypatch.setattr(refresh_data_health_snapshot, "capture_data_health_snapshot", _fail)

    assert refresh_data_health_snapshot.run() == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"ok": False, "error": "RuntimeError"}
    assert "secret" not in captured.err
    assert "customer-id" not in captured.err


def test_online_data_health_summary_reads_one_snapshot_with_compatible_body(monkeypatch) -> None:
    from aicrm_next.data_health import application

    captured_at = datetime(2026, 7, 25, 1, 0, tzinfo=timezone.utc)
    repository = _LoadingRepository(_snapshot(captured_at=captured_at))
    monkeypatch.setattr(application, "production_repository_required", lambda: True)
    monkeypatch.setattr(
        application,
        "run_all_checks",
        lambda: pytest.fail("online summary must not execute live checks"),
    )

    payload = application.data_health_summary(
        snapshot_repository=repository,  # type: ignore[arg-type]
        now=lambda: datetime(2026, 7, 25, 1, 10, tzinfo=timezone.utc),
    )

    assert repository.load_count == 1
    assert payload == {
        "ok": True,
        "overall_status": "warn",
        "counts": {"ok": 1, "warn": 1, "fail": 0, "not_applicable": 0},
        "checks": [
            _check("first").model_dump(),
            _check("second", "warn").model_dump(),
        ],
    }


def test_online_list_and_detail_reuse_only_loaded_aggregate_results(monkeypatch) -> None:
    from aicrm_next.data_health import application

    captured_at = datetime(2026, 7, 25, 1, 0, tzinfo=timezone.utc)
    repository = _LoadingRepository(_snapshot(captured_at=captured_at))
    monkeypatch.setattr(application, "production_repository_required", lambda: True)
    monkeypatch.setattr(
        application,
        "run_all_checks",
        lambda: pytest.fail("online detail must not execute check prefixes"),
    )
    now = lambda: datetime(2026, 7, 25, 1, 10, tzinfo=timezone.utc)

    listed = application.data_health_checks(
        snapshot_repository=repository,  # type: ignore[arg-type]
        now=now,
    )
    detail = application.data_health_check_detail(
        "second",
        snapshot_repository=repository,  # type: ignore[arg-type]
        now=now,
    )
    missing = application.data_health_check_detail(
        "not-a-check",
        snapshot_repository=repository,  # type: ignore[arg-type]
        now=now,
    )

    assert repository.load_count == 3
    assert listed == {
        "ok": True,
        "checks": [
            _check("first").model_dump(),
            _check("second", "warn").model_dump(),
        ],
    }
    assert detail == {"ok": True, "check": _check("second", "warn").model_dump()}
    assert missing == {
        "ok": False,
        "status_code": 404,
        "error_code": "data_health_check_not_found",
        "check_id": "not-a-check",
    }


def test_online_data_health_fails_closed_for_missing_or_stale_snapshot(monkeypatch) -> None:
    from aicrm_next.data_health import application

    monkeypatch.setattr(application, "production_repository_required", lambda: True)
    monkeypatch.setattr(
        application,
        "run_all_checks",
        lambda: pytest.fail("production must not fall back to live checks"),
    )
    now = datetime(2026, 7, 25, 1, 30, tzinfo=timezone.utc)
    missing_repository = _LoadingRepository(None)
    stale_repository = _LoadingRepository(
        _snapshot(captured_at=datetime(2026, 7, 25, 1, 4, 59, tzinfo=timezone.utc))
    )

    assert application.data_health_summary(
        snapshot_repository=missing_repository,  # type: ignore[arg-type]
        now=lambda: now,
    ) == {
        "ok": False,
        "status_code": 503,
        "error_code": "data_health_snapshot_unavailable",
    }
    assert application.data_health_checks(
        snapshot_repository=stale_repository,  # type: ignore[arg-type]
        now=lambda: now,
    ) == {
        "ok": False,
        "status_code": 503,
        "error_code": "data_health_snapshot_stale",
    }
    assert missing_repository.load_count == 1
    assert stale_repository.load_count == 1


def test_offline_data_health_contract_keeps_explicit_direct_runner(monkeypatch) -> None:
    from aicrm_next.data_health import application

    repository = _LoadingRepository(None)
    monkeypatch.setattr(application, "production_repository_required", lambda: False)
    monkeypatch.setattr(application, "run_all_checks", lambda: [_check("offline")])

    payload = application.data_health_summary(
        snapshot_repository=repository,  # type: ignore[arg-type]
    )

    assert repository.load_count == 0
    assert payload["ok"] is True
    assert payload["counts"] == {"ok": 1, "warn": 0, "fail": 0, "not_applicable": 0}
    assert payload["checks"] == [_check("offline").model_dump()]


def test_online_data_health_load_failure_is_safe_and_does_not_fallback(monkeypatch, caplog) -> None:
    from aicrm_next.data_health import application

    monkeypatch.setattr(application, "production_repository_required", lambda: True)
    monkeypatch.setattr(
        application,
        "run_all_checks",
        lambda: pytest.fail("production must not fall back after a repository error"),
    )

    with caplog.at_level("ERROR"):
        payload = application.data_health_summary(
            snapshot_repository=_FailingLoadingRepository(),  # type: ignore[arg-type]
        )

    assert payload == {
        "ok": False,
        "status_code": 503,
        "error_code": "data_health_snapshot_unavailable",
    }
    assert "secret" not in caplog.text
    assert "customer-id" not in caplog.text


def test_online_summary_http_response_keeps_success_contract(monkeypatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from aicrm_next.data_health import application
    from aicrm_next.main import create_app

    captured_at = datetime.now(timezone.utc)
    repository = _LoadingRepository(_snapshot(captured_at=captured_at))
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "0")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setattr(application, "production_repository_required", lambda: True)
    monkeypatch.setattr(application, "DataHealthSnapshotRepository", lambda: repository)
    monkeypatch.setattr(
        application,
        "run_all_checks",
        lambda: pytest.fail("online HTTP route must not execute live checks"),
    )

    response = TestClient(create_app(), raise_server_exceptions=False).get(
        "/api/admin/data-health/summary"
    )

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.json() == {
        "ok": True,
        "overall_status": "warn",
        "counts": {"ok": 1, "warn": 1, "fail": 0, "not_applicable": 0},
        "checks": [
            _check("first").model_dump(mode="json"),
            _check("second", "warn").model_dump(mode="json"),
        ],
    }
    assert repository.load_count == 1
