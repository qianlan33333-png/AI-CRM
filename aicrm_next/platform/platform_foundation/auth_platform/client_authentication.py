from __future__ import annotations

import base64
import ipaddress
from typing import Any, Mapping


class ClientAuthenticationError(ValueError):
    def __init__(self, error: str, *, status_code: int = 401) -> None:
        super().__init__(error)
        self.error = error
        self.status_code = status_code


def client_credentials(
    *,
    headers: Mapping[str, Any],
    form: Mapping[str, Any],
) -> tuple[str, str]:
    authorization = _header(headers, "authorization")
    basic_client_id = ""
    basic_secret = ""
    if authorization:
        if not authorization.startswith("Basic "):
            raise ClientAuthenticationError("invalid_client")
        try:
            decoded = base64.b64decode(authorization[6:].strip(), validate=True).decode("utf-8")
            basic_client_id, basic_secret = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ClientAuthenticationError("invalid_client") from exc
    form_client_id = str(form.get("client_id") or "").strip()
    form_secret = str(form.get("client_secret") or "")
    if basic_client_id and form_client_id and basic_client_id != form_client_id:
        raise ClientAuthenticationError("invalid_client")
    client_id = basic_client_id or form_client_id
    secret = basic_secret or form_secret
    if not client_id or not secret:
        raise ClientAuthenticationError("invalid_client")
    return client_id, secret


def request_source_ip(
    *,
    peer_ip: str,
    headers: Mapping[str, Any],
    trusted_proxy_cidrs: tuple[str, ...] = (),
) -> str:
    peer = str(peer_ip or "").strip()
    if not _ip_in_cidrs(peer, trusted_proxy_cidrs):
        return peer
    forwarded = _header(headers, "x-forwarded-for").split(",", 1)[0].strip()
    try:
        return str(ipaddress.ip_address(forwarded)) if forwarded else peer
    except ValueError:
        raise ClientAuthenticationError("invalid_forwarded_client_ip", status_code=400) from None


def _header(headers: Mapping[str, Any], name: str) -> str:
    target = name.lower()
    for key, value in headers.items():
        if str(key or "").lower() == target:
            return str(value or "").strip()
    return ""


def _ip_in_cidrs(value: str, cidrs: tuple[str, ...]) -> bool:
    if not cidrs:
        return False
    try:
        address = ipaddress.ip_address(str(value or "").strip())
        return any(address in ipaddress.ip_network(cidr, strict=False) for cidr in cidrs)
    except ValueError:
        return False
