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
