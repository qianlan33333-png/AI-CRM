from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs

from aicrm_next.platform_foundation.auth_platform import access_client
from aicrm_next.platform_foundation.auth_platform.access_client import (
    INTERNAL_CLIENT_ID_KEYS,
    INTERNAL_CLIENT_SECRET_REFERENCE_KEYS,
    fetch_internal_access_token,
)
from aicrm_next.shared.route_ownership import load_route_manifest


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(
            {
                "access_token": "header.payload.signature",
                "token_type": "Bearer",
                "expires_in": 1800,
                "scope": "read write",
            }
        ).encode()


def test_machine_purposes_use_distinct_client_ids_and_secret_references() -> None:
    assert set(INTERNAL_CLIENT_ID_KEYS) == {
        "automation_worker",
        "archive",
        "callback",
        "group_broadcast",
        "identity",
        "mcp",
        "external_agent",
        "campaign_agent",
        "ops_reporter",
    }
    assert len(set(INTERNAL_CLIENT_ID_KEYS.values())) == len(INTERNAL_CLIENT_ID_KEYS)
    assert len(set(INTERNAL_CLIENT_SECRET_REFERENCE_KEYS.values())) == len(INTERNAL_CLIENT_SECRET_REFERENCE_KEYS)


def test_internal_access_token_request_uses_tls_basic_and_exact_secret_reference(monkeypatch) -> None:
    captured = {}
    tls_context = object()
    monkeypatch.setattr(access_client, "build_tls_ssl_context", lambda *, environ: tls_context)

    def urlopen(request, *, timeout, context):
        captured.update(
            {
                "url": request.full_url,
                "body": parse_qs(request.data.decode("ascii")),
                "authorization": request.headers["Authorization"],
                "timeout": timeout,
                "context": context,
            }
        )
        return _Response()

    lease = fetch_internal_access_token(
        purpose="archive",
        audience="internal_worker",
        scopes=("write", "read"),
        urlopen=urlopen,
        environ={
            "AICRM_AUTH_ISSUER": "https://crm.example.test/oauth",
            "AICRM_AUTH_ARCHIVE_WORKER_CLIENT_ID": "archive-worker-client",
            "AICRM_AUTH_ARCHIVE_WORKER_CLIENT_SECRET_REF": "secretref:file:ARCHIVE:v1_test",
        },
        secret_resolver=lambda reference: "resolved-secret" if reference.endswith("v1_test") else "",
    )

    assert captured["url"] == "https://crm.example.test/oauth/token"
    assert captured["body"] == {
        "grant_type": ["client_credentials"],
        "audience": ["internal_worker"],
        "scope": ["read write"],
    }
    assert base64.b64decode(captured["authorization"].removeprefix("Basic ")).decode() == (
        "archive-worker-client:resolved-secret"
    )
    assert captured["timeout"] == 30
    assert captured["context"] is tls_context
    assert lease.access_token == "header.payload.signature"


def test_internal_access_token_reads_published_runtime_settings_as_one_snapshot(monkeypatch) -> None:
    values = {
        "AICRM_AUTH_ISSUER": "https://crm.example.test/oauth",
        "AICRM_AUTH_ARCHIVE_WORKER_CLIENT_ID": "archive-worker-client",
        "AICRM_AUTH_ARCHIVE_WORKER_CLIENT_SECRET_REF": "secretref:file:ARCHIVE:v1_test",
        "AICRM_AUTH_CA_FILE": "",
    }
    lookups: list[str] = []
    scopes_entered: list[str] = []

    class _Scope:
        def __enter__(self):
            scopes_entered.append("enter")

        def __exit__(self, *_args):
            scopes_entered.append("exit")

    monkeypatch.setattr(access_client, "runtime_settings_request_scope", _Scope)
    monkeypatch.setattr(
        access_client,
        "managed_runtime_setting",
        lambda key: lookups.append(key) or values.get(key, ""),
    )
    monkeypatch.setattr(access_client.ssl, "create_default_context", lambda *, cafile: object())

    lease = fetch_internal_access_token(
        purpose="archive",
        audience="internal_worker",
        scopes=("read",),
        urlopen=lambda *_args, **_kwargs: _Response(),
        secret_resolver=lambda _reference: "resolved-secret",
    )

    assert lease.access_token == "header.payload.signature"
    assert scopes_entered == ["enter", "exit"]
    assert lookups == [
        "AICRM_AUTH_ISSUER",
        "AICRM_AUTH_ARCHIVE_WORKER_CLIENT_ID",
        "AICRM_AUTH_ARCHIVE_WORKER_CLIENT_SECRET_REF",
        "AICRM_AUTH_CA_FILE",
    ]


def test_machine_routes_use_signed_jwt_with_exact_purpose_and_capability() -> None:
    entries = load_route_manifest("docs/architecture/route_ownership_manifest.yml")
    by_route = {(entry["path"], entry["route_name"]): entry for entry in entries}
    expected = {
        ("/api/archive/health", "archive_health"): ("internal_worker", "archive_read", "archive"),
        ("/api/archive/sync", "archive_sync"): ("internal_worker", "archive_execute", "archive"),
        ("/api/automation/group-ops/broadcast", "execute_group_ops_token_broadcast"): (
            "external_integration",
            "group_broadcast_execute",
            "group_broadcast",
        ),
        ("/api/identity/resolve", "resolve_identity"): ("external_integration", "identity_resolve", "identity"),
        ("/api/system/runtime-route-map", "runtime_route_map"): (
            "internal_worker",
            "runtime_route_read",
            "automation_worker",
        ),
        ("/api/operation-cycles/reports", "report_operation_cycle_snapshot"): (
            "external_integration",
            "operation_cycle_report_write",
            "ops_reporter",
        ),
        ("/mcp", "mcp_metadata"): ("external_integration", "mcp_read", "mcp"),
        ("/mcp", "mcp_rpc"): ("external_integration", "mcp_execute", "mcp"),
    }

    for route, expected_policy in expected.items():
        entry = by_route[route]
        assert entry["auth_scheme"] == "api_client_jwt"
        assert (entry["audience"], entry["capability"], entry["client_purpose"]) == expected_policy
