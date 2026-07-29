#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


AUTHORIZATION_ENV = "AICRM_ID_VALIDATION_CALLBACK_RELAY_AUTHORIZED"
EXPECTED_SOURCE_REPOSITORY = "qianlan333/AI-CRM"
EXPECTED_TARGET_URL = "https://id-dev.youcangogogo.com/wecom/external-contact/callback"
EXPECTED_TARGET_HEALTH_URL = "https://id-dev.youcangogogo.com/health"
EXPECTED_ROUTE = "/wecom/external-contact/callback"
FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
QUERY_FIELDS = frozenset({"timestamp", "nonce", "msg_signature"})


class CallbackRelayError(RuntimeError):
    """Stable error that never includes callback ciphertext or target identity."""


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ARG002
        return None


_EXACT_TARGET_OPENER = urllib.request.build_opener(_RejectRedirectHandler())


def _open_exact_target(request: urllib.request.Request, *, timeout: float):
    return _EXACT_TARGET_OPENER.open(request, timeout=timeout)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Relay one fresh, exact WeCom callback from the production inbox to "
            "the immutable ID-validation callback ingress."
        )
    )
    parser.add_argument("--repository-path", required=True)
    parser.add_argument("--expected-source-release-sha", required=True)
    parser.add_argument("--expected-target-release-sha", required=True)
    parser.add_argument("--scene-sha256", required=True)
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--poll-seconds", type=float, default=0.05)
    parser.add_argument("--maximum-event-age-seconds", type=float, default=12.0)
    parser.add_argument("--apply", action="store_true", default=False)
    parser.add_argument("--confirmation", default="")
    return parser.parse_args(argv)


def _confirmation(target_release_sha: str) -> str:
    return f"RELAY_ONE_WECOM_CALLBACK_TO_ID_VALIDATION_{target_release_sha}"


def _git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise CallbackRelayError("source repository attestation failed")
    return result.stdout.strip()


def verify_source_checkout(
    repository_path: str,
    *,
    expected_release_sha: str,
) -> dict[str, str]:
    path = Path(str(repository_path or "")).expanduser().resolve()
    if not path.is_dir() or FULL_SHA.fullmatch(expected_release_sha) is None:
        raise CallbackRelayError("source repository identity is invalid")
    origin = _git(path, "remote", "get-url", "origin")
    allowed_origins = {
        f"https://github.com/{EXPECTED_SOURCE_REPOSITORY}",
        f"https://github.com/{EXPECTED_SOURCE_REPOSITORY}.git",
        f"git@github.com:{EXPECTED_SOURCE_REPOSITORY}",
        f"git@github.com:{EXPECTED_SOURCE_REPOSITORY}.git",
    }
    if origin not in allowed_origins:
        raise CallbackRelayError("source checkout is not the AI-CRM repository")
    if _git(path, "rev-parse", "HEAD") != expected_release_sha:
        raise CallbackRelayError("source checkout release SHA mismatch")
    marker = path / ".release-sha"
    if marker.is_symlink() or not marker.is_file():
        raise CallbackRelayError("source release marker is missing")
    if marker.read_text(encoding="utf-8").strip() != expected_release_sha:
        raise CallbackRelayError("source release marker mismatch")
    return {
        "repository": EXPECTED_SOURCE_REPOSITORY,
        "release_sha": expected_release_sha,
    }


def _connect(database_url: str):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(database_url, row_factory=dict_row, autocommit=True)


def _target_release_sha(
    *,
    open_url: Callable[..., Any] = _open_exact_target,
) -> str:
    request = urllib.request.Request(
        EXPECTED_TARGET_HEALTH_URL,
        method="GET",
        headers={"User-Agent": "aicrm-id-validation-callback-relay/1"},
    )
    try:
        with open_url(request, timeout=10.0) as response:
            response.read(65537)
            status = int(response.status)
            values = list(response.headers.get_all("x-aicrm-release-sha") or [])
    except urllib.error.HTTPError as exc:
        raise CallbackRelayError(
            f"ID-validation health returned HTTP {int(exc.code)}"
        ) from None
    except (OSError, urllib.error.URLError) as exc:
        raise CallbackRelayError("ID-validation health was unavailable") from exc
    normalized = [str(value or "").strip() for value in values]
    if status != 200 or len(normalized) != 1 or FULL_SHA.fullmatch(normalized[0]) is None:
        raise CallbackRelayError("ID-validation health release attestation is invalid")
    return normalized[0]


def _provider_event_time(row: dict[str, Any]) -> datetime:
    payload = dict(row.get("payload_json") or {})
    try:
        return datetime.fromtimestamp(
            int(str(payload.get("CreateTime") or "0")),
            tz=timezone.utc,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise CallbackRelayError("source callback provider timestamp is invalid") from exc


def _assert_fresh(row: dict[str, Any], *, maximum_event_age_seconds: float) -> float:
    age_seconds = (datetime.now(timezone.utc) - _provider_event_time(row)).total_seconds()
    maximum_age = max(1.0, min(float(maximum_event_age_seconds), 18.0))
    if age_seconds < -5.0 or age_seconds > maximum_age:
        raise CallbackRelayError("matching callback is outside the real-time relay window")
    return age_seconds


def _scene_digest(row: dict[str, Any]) -> str:
    payload = dict(row.get("payload_json") or {})
    scene = str(payload.get("State") or "")
    return hashlib.sha256(scene.encode("utf-8")).hexdigest()


def wait_for_scene_callback(
    database_url: str,
    *,
    scene_sha256: str,
    timeout_seconds: float,
    poll_seconds: float,
    maximum_event_age_seconds: float,
    connect: Callable[[str], Any] = _connect,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    deadline = monotonic() + max(1.0, min(float(timeout_seconds), 1800.0))
    poll = max(0.02, min(float(poll_seconds), 1.0))
    with connect(database_url) as connection:
        baseline = connection.execute(
            "SELECT COALESCE(MAX(id), 0)::BIGINT AS id FROM webhook_inbox"
        ).fetchone()
        cursor = int((baseline or {}).get("id") or 0)
        while monotonic() < deadline:
            rows = connection.execute(
                """
                SELECT id, payload_json
                FROM webhook_inbox
                WHERE id > %s
                  AND provider = 'wecom'
                  AND event_family = 'external_contact'
                  AND route = %s
                  AND event_type = 'change_external_contact'
                  AND change_type = 'add_external_contact'
                  AND COALESCE(payload_json->>'State', '') <> ''
                  AND COALESCE(payload_json->>'WelcomeCode', '') <> ''
                ORDER BY id ASC
                LIMIT 500
                """,
                (cursor, EXPECTED_ROUTE),
            ).fetchall()
            matches = [dict(row) for row in rows if _scene_digest(dict(row)) == scene_sha256]
            if len(matches) > 1:
                raise CallbackRelayError(
                    "more than one matching callback arrived after the armed boundary"
                )
            if matches:
                callback = connection.execute(
                    """
                    SELECT id, route, provider, event_family, event_type, change_type,
                           raw_query_json, raw_body, payload_json, received_at
                    FROM webhook_inbox
                    WHERE id = %s
                    """,
                    (int(matches[0]["id"]),),
                ).fetchone()
                if not callback:
                    raise CallbackRelayError("matching callback disappeared")
                result = dict(callback)
                if _scene_digest(result) != scene_sha256:
                    raise CallbackRelayError("matching callback changed during relay selection")
                _assert_fresh(
                    result,
                    maximum_event_age_seconds=maximum_event_age_seconds,
                )
                return result
            if rows:
                cursor = max(int(row["id"]) for row in rows)
            sleep(poll)
    raise CallbackRelayError("no matching callback arrived before the relay timeout")


def _callback_request(row: dict[str, Any], *, target_url: str) -> urllib.request.Request:
    query = row.get("raw_query_json")
    if not isinstance(query, dict) or not QUERY_FIELDS.issubset(query):
        raise CallbackRelayError("source callback query contract is incomplete")
    exact_query = {field: str(query.get(field) or "").strip() for field in QUERY_FIELDS}
    if (
        not exact_query["timestamp"].isdigit()
        or not exact_query["nonce"]
        or len(exact_query["nonce"]) > 256
        or re.fullmatch(r"[0-9a-fA-F]{40}", exact_query["msg_signature"]) is None
    ):
        raise CallbackRelayError("source callback query contract is invalid")
    raw_body = row.get("raw_body")
    if isinstance(raw_body, memoryview):
        raw_body = raw_body.tobytes()
    if not isinstance(raw_body, bytes) or not raw_body or len(raw_body) > 1024 * 1024:
        raise CallbackRelayError("source callback ciphertext body is invalid")
    target = target_url + "?" + urllib.parse.urlencode(exact_query)
    return urllib.request.Request(
        target,
        data=raw_body,
        method="POST",
        headers={
            "Content-Type": "application/xml",
            "User-Agent": "aicrm-id-validation-callback-relay/1",
        },
    )


def relay_callback_once(
    row: dict[str, Any],
    *,
    target_url: str,
    open_url: Callable[..., Any] = _open_exact_target,
) -> dict[str, Any]:
    request = _callback_request(row, target_url=target_url)
    started = time.monotonic()
    try:
        with open_url(request, timeout=10.0) as response:
            response_body = response.read(65537)
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raise CallbackRelayError(
            f"ID-validation callback ingress returned HTTP {int(exc.code)}; relay was not retried"
        ) from None
    except (OSError, urllib.error.URLError) as exc:
        raise CallbackRelayError(
            "ID-validation callback relay outcome is unknown; automatic retry is forbidden"
        ) from exc
    if status != 200 or not response_body or len(response_body) > 65536:
        raise CallbackRelayError(
            "ID-validation callback acknowledgement was invalid; relay was not retried"
        )
    return {
        "source_inbox_id": int(row.get("id") or 0),
        "http_status": status,
        "ack_body_sha256": hashlib.sha256(response_body).hexdigest(),
        "relay_elapsed_ms": int((time.monotonic() - started) * 1000),
        "callback_relay_executed": True,
        "provider_external_call_executed": False,
        "target_values_redacted": True,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    source_release_sha = str(args.expected_source_release_sha or "").strip()
    target_release_sha = str(args.expected_target_release_sha or "").strip()
    scene_sha256 = str(args.scene_sha256 or "").strip()
    if str(args.target_url or "").strip() != EXPECTED_TARGET_URL:
        raise ValueError("target URL must be the exact ID-validation callback ingress")
    if (
        FULL_SHA.fullmatch(source_release_sha) is None
        or FULL_SHA.fullmatch(target_release_sha) is None
        or SHA256.fullmatch(scene_sha256) is None
    ):
        raise ValueError("source SHA, target SHA and scene SHA-256 are required")
    plan = {
        "ok": True,
        "applied": False,
        "source_repository": EXPECTED_SOURCE_REPOSITORY,
        "source_release_sha": source_release_sha,
        "target_release_sha": target_release_sha,
        "target_values_redacted": True,
        "callback_relay_executed": False,
        "provider_external_call_executed": False,
        "automatic_retry_enabled": False,
    }
    if not args.apply:
        print(json.dumps(plan, sort_keys=True))
        return 0
    if str(os.getenv(AUTHORIZATION_ENV) or "").strip() != "1":
        raise CallbackRelayError(f"{AUTHORIZATION_ENV}=1 is required")
    if str(args.confirmation or "").strip() != _confirmation(target_release_sha):
        raise CallbackRelayError(
            f"--confirmation must equal {_confirmation(target_release_sha)}"
        )
    verify_source_checkout(
        args.repository_path,
        expected_release_sha=source_release_sha,
    )
    if _target_release_sha() != target_release_sha:
        raise CallbackRelayError("ID-validation target release SHA mismatch before arm")
    database_url = str(os.getenv("DATABASE_URL") or "").strip()
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise CallbackRelayError("source PostgreSQL DATABASE_URL is required")
    row = wait_for_scene_callback(
        database_url,
        scene_sha256=scene_sha256,
        timeout_seconds=float(args.timeout_seconds),
        poll_seconds=float(args.poll_seconds),
        maximum_event_age_seconds=float(args.maximum_event_age_seconds),
    )
    if _target_release_sha() != target_release_sha:
        raise CallbackRelayError("ID-validation target release SHA changed before relay")
    _assert_fresh(
        row,
        maximum_event_age_seconds=float(args.maximum_event_age_seconds),
    )
    result = relay_callback_once(row, target_url=EXPECTED_TARGET_URL)
    print(json.dumps({**plan, **result, "applied": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CallbackRelayError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
