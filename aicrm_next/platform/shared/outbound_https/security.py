from __future__ import annotations

"""Public HTTPS target validation shared by outbound adapters."""

import ipaddress
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit


class WebhookUrlValidationError(ValueError):
    pass


Resolver = Callable[[str, int], Iterable[str]]


@dataclass(frozen=True)
class ValidatedHttpsTarget:
    url: str
    hostname: str
    port: int
    ip_addresses: tuple[str, ...]
    request_target: str

    @property
    def selected_ip(self) -> str:
        return self.ip_addresses[0]

    @property
    def host_header(self) -> str:
        return self.hostname if self.port == 443 else f"{self.hostname}:{self.port}"


def _normalized_text(value: Any) -> str:
    return str(value or "").strip()


def _public_ip(address: str) -> str:
    try:
        parsed = ipaddress.ip_address(_normalized_text(address).strip("[]"))
    except ValueError as exc:
        raise WebhookUrlValidationError("webhook_url DNS resolved to an invalid IP") from exc
    if not parsed.is_global:
        raise WebhookUrlValidationError("webhook_url resolved to a non-public IP")
    return parsed.compressed


def _normalized_url(url: str) -> tuple[str, str, int, str]:
    try:
        parsed = urlsplit(_normalized_text(url))
        port = parsed.port or 443
    except ValueError as exc:
        raise WebhookUrlValidationError("webhook_url is invalid") from exc
    if parsed.scheme.lower() != "https":
        raise WebhookUrlValidationError("webhook_url must be an https URL")
    if parsed.username is not None or parsed.password is not None:
        raise WebhookUrlValidationError("webhook_url credentials are not allowed")
    if parsed.fragment:
        raise WebhookUrlValidationError("webhook_url fragments are not allowed")
    if port != 443:
        raise WebhookUrlValidationError("webhook_url port must be 443")
    raw_hostname = str(parsed.hostname or "").strip().rstrip(".")
    if not raw_hostname:
        raise WebhookUrlValidationError("webhook_url host is required")
    if "%" in raw_hostname:
        raise WebhookUrlValidationError("webhook_url IPv6 zone identifiers are not allowed")
    try:
        hostname = raw_hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise WebhookUrlValidationError("webhook_url host is invalid") from exc
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise WebhookUrlValidationError("webhook_url host is not allowed")
    path = parsed.path or "/"
    request_target = path + (f"?{parsed.query}" if parsed.query else "")
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        normalized_host = hostname
    else:
        normalized_host = f"[{literal.compressed}]" if literal.version == 6 else literal.compressed
    normalized = urlunsplit(("https", normalized_host, path, parsed.query, ""))
    return normalized, hostname, port, request_target


def _system_resolver(hostname: str, port: int) -> list[str]:
    try:
        addr_infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebhookUrlValidationError("webhook_url DNS resolution failed") from exc
    return [str(item[4][0]) for item in addr_infos if item and item[4]]


def validate_webhook_url(url: str) -> str:
    normalized, hostname, _port, _request_target = _normalized_url(url)
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return normalized
    _public_ip(hostname)
    return normalized


def resolve_and_validate_public_https_target(
    url: str,
    *,
    resolver: Resolver | None = None,
) -> ValidatedHttpsTarget:
    normalized, hostname, port, request_target = _normalized_url(url)
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            resolved = list((resolver or _system_resolver)(hostname, port))
        except WebhookUrlValidationError:
            raise
        except OSError as exc:
            raise WebhookUrlValidationError("webhook_url DNS resolution failed") from exc
    else:
        resolved = [literal.compressed]
    if not resolved:
        raise WebhookUrlValidationError("webhook_url DNS resolution returned no IP")
    public_ips = {_public_ip(address) for address in resolved}
    ordered_ips = tuple(
        str(address)
        for address in sorted(
            (ipaddress.ip_address(value) for value in public_ips),
            key=lambda value: (value.version, int(value)),
        )
    )
    return ValidatedHttpsTarget(
        url=normalized,
        hostname=hostname,
        port=port,
        ip_addresses=ordered_ips,
        request_target=request_target,
    )


def resolve_and_validate_public_https_url(url: str) -> str:
    return resolve_and_validate_public_https_target(url).url
