from __future__ import annotations

import json
from io import BytesIO
from typing import Any

import pytest

from aicrm_next.platform.platform_foundation import readiness
from scripts.ops.check_runtime_readiness import _validated_url, probe_runtime_readiness, run


pytestmark = pytest.mark.unit
EXACT_HEAD = "expected_test_head"
EXACT_SHA = "a" * 40


class QueryCanceled(Exception):
    pass


class FakeReadinessRepository:
    queue_error: Exception | None = None

    def __init__(self, database_url: str, *, connection_factory: object | None = None) -> None:
        self.database_url = database_url

    def __enter__(self) -> "FakeReadinessRepository":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        return False

    def ping(self) -> bool:
        return True

    def migration_revisions(self) -> tuple[str, ...]:
        return (EXACT_HEAD,)

    def queue_metrics(self, **_: Any) -> dict[str, Any]:
        if self.queue_error is not None:
            raise self.queue_error
        return {}


def _payload(monkeypatch: pytest.MonkeyPatch, error: Exception) -> dict[str, Any]:
    FakeReadinessRepository.queue_error = error
    monkeypatch.setattr(readiness, "RuntimeReadinessRepository", FakeReadinessRepository)
    return readiness.runtime_readiness_payload(
        database_url="postgresql://readiness@db/current",
        expected_heads=(EXACT_HEAD,),
        wecom_diagnostics={
            "enabled": True,
            "execution_mode": "execute",
            "execution_mode_source": "test",
            "conflict": False,
            "blocking_reasons": [],
        },
        release_sha=EXACT_SHA,
        production=True,
    )


def test_queue_probe_budget_exhaustion_is_a_release_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload(monkeypatch, QueryCanceled("statement timeout"))

    assert payload["ok"] is True
    assert payload["http_status"] == 200
    assert payload["failed_components"] == []
    assert payload["warning_components"] == ["queues"]
    assert payload["components"]["migration"]["compatibility"] == "exact"
    assert payload["components"]["queues"] == {
        "status": "warning",
        "critical": True,
        "metrics": {},
        "warnings": ["queue_probe_budget_exhausted"],
        "reason_code": "queue_probe_budget_exhausted",
        "error_class": "QueryCanceled",
        "candidate_related": False,
        "authoritative_gate": "data_health_registry",
        "remediation": "run the read-only data-health release gate",
    }


def test_unknown_queue_probe_error_remains_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload(monkeypatch, RuntimeError("invalid queue query"))

    assert payload["ok"] is False
    assert payload["http_status"] == 503
    assert payload["failed_components"] == ["queues"]
    assert payload["components"]["queues"]["reason_code"] == "queue_probe_failed"
    assert payload["components"]["queues"]["candidate_related"] == "unknown"


class FakeHttpResponse:
    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        self.status = status
        self.body = BytesIO(json_bytes(payload))

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        return False

    def getcode(self) -> int:
        return self.status

    def read(self) -> bytes:
        return self.body.read()


def json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode("utf-8")


def test_runtime_readiness_probe_is_loopback_only_and_preserves_body() -> None:
    assert _validated_url("http://127.0.0.1:5001") == "http://127.0.0.1:5001/api/system/health"
    with pytest.raises(ValueError, match="loopback"):
        _validated_url("https://production.example")

    status, payload = probe_runtime_readiness(
        "http://127.0.0.1:5001",
        open_url=lambda *_args, **_kwargs: FakeHttpResponse(
            200,
            {"ok": True, "failed_components": [], "warning_components": ["queues"]},
        ),
    )
    assert status == 200
    assert payload["ok"] is True


def test_runtime_readiness_probe_fails_closed_with_structured_evidence(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = run(
        "http://127.0.0.1:5001",
        open_url=lambda *_args, **_kwargs: FakeHttpResponse(
            503,
            {"ok": False, "failed_components": ["migration"], "warning_components": []},
        ),
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert json.loads(captured.out)["failed_components"] == ["migration"]
    assert "candidate_runtime_readiness_blocked" in captured.err
