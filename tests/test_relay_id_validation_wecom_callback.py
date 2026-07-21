from __future__ import annotations

import json
import time
import urllib.parse
from types import SimpleNamespace

import pytest

from scripts.ops import relay_id_validation_wecom_callback as relay


SOURCE_SHA = "a" * 40
TARGET_SHA = "b" * 40
SCENE = "dedicated-canary-scene"
SCENE_SHA256 = relay.hashlib.sha256(SCENE.encode("utf-8")).hexdigest()


def _row(*, row_id: int = 77, scene: str = SCENE) -> dict:
    return {
        "id": row_id,
        "route": relay.EXPECTED_ROUTE,
        "provider": "wecom",
        "event_family": "external_contact",
        "event_type": "change_external_contact",
        "change_type": "add_external_contact",
        "raw_query_json": {
            "timestamp": "1784349492",
            "nonce": "nonce-canary",
            "msg_signature": "c" * 40,
        },
        "raw_body": b"<xml>encrypted-callback</xml>",
        "payload_json": {
            "CreateTime": str(int(time.time())),
            "ExternalUserID": "external-canary",
            "UserID": "owner-canary",
            "State": scene,
            "WelcomeCode": "welcome-secret",
        },
    }


def test_plan_only_redacts_scene_and_never_reads_database(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        relay,
        "wait_for_scene_callback",
        lambda *args, **kwargs: pytest.fail("plan-only relay must not read the database"),
    )

    assert (
        relay.main(
            [
                "--repository-path",
                "/srv/aicrm",
                "--expected-source-release-sha",
                SOURCE_SHA,
                "--expected-target-release-sha",
                TARGET_SHA,
                "--scene-sha256",
                SCENE_SHA256,
                "--target-url",
                relay.EXPECTED_TARGET_URL,
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["applied"] is False
    assert payload["automatic_retry_enabled"] is False
    assert SCENE not in output
    assert "external-canary" not in output


def test_wait_for_scene_callback_skips_other_rows_and_fetches_exact_match() -> None:
    unrelated = _row(row_id=76, scene="another-scene")
    matching = _row(row_id=77)
    statements: list[str] = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def execute(self, statement, parameters=None):
            normalized = " ".join(str(statement).split())
            statements.append(normalized)
            if "MAX(id)" in normalized:
                return SimpleNamespace(fetchone=lambda: {"id": 75})
            if "SELECT id, payload_json" in normalized:
                return SimpleNamespace(fetchall=lambda: [unrelated, matching])
            if "WHERE id = %s" in normalized:
                assert tuple(parameters) == (77,)
                return SimpleNamespace(fetchone=lambda: matching)
            raise AssertionError(normalized)

    result = relay.wait_for_scene_callback(
        "postgresql://source",
        scene_sha256=SCENE_SHA256,
        timeout_seconds=1,
        poll_seconds=0.02,
        maximum_event_age_seconds=12,
        connect=lambda _url: Connection(),
    )

    assert result["id"] == 77
    assert all("UPDATE" not in statement for statement in statements)


def test_wait_for_scene_callback_rejects_two_exact_matches() -> None:
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def execute(self, statement, parameters=None):
            normalized = " ".join(str(statement).split())
            if "MAX(id)" in normalized:
                return SimpleNamespace(fetchone=lambda: {"id": 75})
            return SimpleNamespace(fetchall=lambda: [_row(row_id=76), _row(row_id=77)])

    with pytest.raises(relay.CallbackRelayError, match="more than one"):
        relay.wait_for_scene_callback(
            "postgresql://source",
            scene_sha256=SCENE_SHA256,
            timeout_seconds=1,
            poll_seconds=0.02,
            maximum_event_age_seconds=12,
            connect=lambda _url: Connection(),
        )


def test_callback_request_preserves_only_original_signature_and_ciphertext() -> None:
    request = relay._callback_request(_row(), target_url=relay.EXPECTED_TARGET_URL)
    parsed = urllib.parse.urlparse(request.full_url)
    query = urllib.parse.parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "id-dev.youcangogogo.com"
    assert parsed.path == relay.EXPECTED_ROUTE
    assert set(query) == relay.QUERY_FIELDS
    assert request.data == _row()["raw_body"]


def test_relay_executes_once_and_returns_redacted_receipt() -> None:
    calls = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def read(self, _limit):
            return b"<xml>encrypted-success</xml>"

    result = relay.relay_callback_once(
        _row(),
        target_url=relay.EXPECTED_TARGET_URL,
        open_url=lambda request, timeout: calls.append(request) or Response(),
    )

    assert len(calls) == 1
    assert result["http_status"] == 200
    assert result["callback_relay_executed"] is True
    serialized = json.dumps(result, sort_keys=True)
    for secret_value in (SCENE, "welcome-secret", "nonce-canary", "encrypted-callback"):
        assert secret_value not in serialized


def test_relay_network_error_is_unknown_and_never_retried() -> None:
    calls = []

    def fail_once(request, timeout):
        calls.append(request)
        raise OSError("network outcome unknown")

    with pytest.raises(relay.CallbackRelayError, match="outcome is unknown"):
        relay.relay_callback_once(
            _row(),
            target_url=relay.EXPECTED_TARGET_URL,
            open_url=fail_once,
        )
    assert len(calls) == 1


def test_stale_matching_callback_is_rejected_before_http_relay() -> None:
    stale = _row()
    stale["payload_json"]["CreateTime"] = "1"

    with pytest.raises(relay.CallbackRelayError, match="real-time relay window"):
        relay._assert_fresh(stale, maximum_event_age_seconds=12)
