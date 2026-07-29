from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import ssl
from typing import Any, Callable
from urllib.parse import urlencode
import urllib.request

from aicrm_next.platform.shared.secret_store import FileSecretStore, is_secret_reference
from aicrm_next.platform.shared.runtime_settings import managed_runtime_setting, runtime_settings_request_scope


INTERNAL_CLIENT_ID_KEYS = {
    "automation_worker": "AICRM_AUTH_AUTOMATION_WORKER_CLIENT_ID",
    "archive": "AICRM_AUTH_ARCHIVE_WORKER_CLIENT_ID",
    "callback": "AICRM_AUTH_CALLBACK_WORKER_CLIENT_ID",
    "group_broadcast": "AICRM_AUTH_GROUP_BROADCAST_CLIENT_ID",
    "identity": "AICRM_AUTH_IDENTITY_CLIENT_ID",
    "mcp": "AICRM_AUTH_MCP_CLIENT_ID",
    "external_agent": "AICRM_AUTH_EXTERNAL_AGENT_CLIENT_ID",
    "campaign_agent": "AICRM_AUTH_CAMPAIGN_AGENT_CLIENT_ID",
    "ops_reporter": "AICRM_AUTH_OPS_REPORTER_CLIENT_ID",
}
INTERNAL_CLIENT_SECRET_REFERENCE_KEYS = {
    purpose: key.removesuffix("_CLIENT_ID") + "_CLIENT_SECRET_REF"
    for purpose, key in INTERNAL_CLIENT_ID_KEYS.items()
}

# Kept explicit so the runtime-contract inventory can audit settings that are
# otherwise accessed indirectly through the purpose-to-setting maps above.
RUNTIME_SETTING_KEYS = {
    "AICRM_AUTH_ISSUER",
    "AICRM_AUTH_AUTOMATION_WORKER_CLIENT_ID",
    "AICRM_AUTH_AUTOMATION_WORKER_CLIENT_SECRET_REF",
    "AICRM_AUTH_ARCHIVE_WORKER_CLIENT_ID",
    "AICRM_AUTH_ARCHIVE_WORKER_CLIENT_SECRET_REF",
    "AICRM_AUTH_CALLBACK_WORKER_CLIENT_ID",
    "AICRM_AUTH_CALLBACK_WORKER_CLIENT_SECRET_REF",
    "AICRM_AUTH_GROUP_BROADCAST_CLIENT_ID",
    "AICRM_AUTH_GROUP_BROADCAST_CLIENT_SECRET_REF",
    "AICRM_AUTH_IDENTITY_CLIENT_ID",
    "AICRM_AUTH_IDENTITY_CLIENT_SECRET_REF",
    "AICRM_AUTH_MCP_CLIENT_ID",
    "AICRM_AUTH_MCP_CLIENT_SECRET_REF",
    "AICRM_AUTH_EXTERNAL_AGENT_CLIENT_ID",
    "AICRM_AUTH_EXTERNAL_AGENT_CLIENT_SECRET_REF",
    "AICRM_AUTH_CAMPAIGN_AGENT_CLIENT_ID",
    "AICRM_AUTH_CAMPAIGN_AGENT_CLIENT_SECRET_REF",
    "AICRM_AUTH_OPS_REPORTER_CLIENT_ID",
    "AICRM_AUTH_OPS_REPORTER_CLIENT_SECRET_REF",
}


@dataclass(frozen=True)
class AccessTokenLease:
    access_token: str
    token_type: str
    expires_in: int
    scope: tuple[str, ...]


UrlOpen = Callable[..., Any]
SecretResolver = Callable[[str], str]


def fetch_internal_access_token(
    *,
    purpose: str,
    audience: str,
    scopes: tuple[str, ...],
    urlopen: UrlOpen = urllib.request.urlopen,
    environ: dict[str, str] | None = None,
    secret_resolver: SecretResolver | None = None,
    timeout_seconds: int = 30,
) -> AccessTokenLease:
    if environ is None:
        with runtime_settings_request_scope():
            return _fetch_internal_access_token(
                purpose=purpose,
                audience=audience,
                scopes=scopes,
                urlopen=urlopen,
                environ=None,
                secret_resolver=secret_resolver,
                timeout_seconds=timeout_seconds,
            )
    return _fetch_internal_access_token(
        purpose=purpose,
        audience=audience,
        scopes=scopes,
        urlopen=urlopen,
        environ=environ,
        secret_resolver=secret_resolver,
        timeout_seconds=timeout_seconds,
    )


def _fetch_internal_access_token(
    *,
    purpose: str,
    audience: str,
    scopes: tuple[str, ...],
    urlopen: UrlOpen,
    environ: dict[str, str] | None,
    secret_resolver: SecretResolver | None,
    timeout_seconds: int,
) -> AccessTokenLease:
    normalized_purpose = str(purpose or "").strip()
    client_key = INTERNAL_CLIENT_ID_KEYS.get(normalized_purpose)
    secret_key = INTERNAL_CLIENT_SECRET_REFERENCE_KEYS.get(normalized_purpose)
    if not client_key or not secret_key:
        raise ValueError(f"unknown auth client purpose: {normalized_purpose or '<empty>'}")
    issuer = _setting("AICRM_AUTH_ISSUER", environ=environ).rstrip("/")
    client_id = _setting(client_key, environ=environ).strip()
    secret_reference = _setting(secret_key, environ=environ).strip()
    normalized_scopes = tuple(sorted({str(scope or "").strip() for scope in scopes if str(scope or "").strip()}))
    if not issuer or not client_id or not secret_reference or not audience or not normalized_scopes:
        raise RuntimeError("internal API client configuration is incomplete")
    resolver = secret_resolver or _resolve_secret
    client_secret = resolver(secret_reference)
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        f"{issuer}/token",
        data=urlencode(
            {
                "grant_type": "client_credentials",
                "audience": str(audience).strip(),
                "scope": " ".join(normalized_scopes),
            }
        ).encode("ascii"),
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds, context=build_tls_ssl_context(environ=environ)) as response:
        payload = json.loads(response.read().decode("utf-8"))
    access_token = str(payload.get("access_token") or "").strip()
    if access_token.count(".") != 2:
        raise RuntimeError("auth endpoint returned an invalid signed access token")
    return AccessTokenLease(
        access_token=access_token,
        token_type=str(payload.get("token_type") or "Bearer"),
        expires_in=int(payload.get("expires_in") or 0),
        scope=tuple(str(payload.get("scope") or "").split()),
    )


def build_tls_ssl_context(*, environ: dict[str, str] | None = None) -> ssl.SSLContext:
    ca_file = _setting("AICRM_AUTH_CA_FILE", environ=environ).strip()
    return ssl.create_default_context(cafile=ca_file or None)


def _setting(key: str, *, environ: dict[str, str] | None) -> str:
    if environ is not None:
        return str(environ.get(key) or "")
    return managed_runtime_setting(key)


def _resolve_secret(reference: str) -> str:
    if not is_secret_reference(reference):
        raise RuntimeError("client secret must be supplied through a secret reference")
    secret = FileSecretStore.from_environment().read(reference).strip()
    if not secret:
        raise RuntimeError("client secret reference is empty")
    return secret
