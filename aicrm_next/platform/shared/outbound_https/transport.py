from __future__ import annotations

"""DNS-pinned HTTPS transport shared by outbound adapters."""

import inspect
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import requests
import urllib3

from .security import ValidatedHttpsTarget


MAX_RESPONSE_BYTES = 64 * 1024


class HttpsTransportError(RuntimeError):
    pass


class HttpsTransportTimeout(HttpsTransportError):
    pass


@dataclass(frozen=True)
class HttpsTransportResponse:
    status_code: int
    text: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        return json.loads(self.text)


class HttpsTransport(Protocol):
    def post(
        self,
        target: ValidatedHttpsTarget,
        *,
        body: bytes,
        headers: Mapping[str, str],
        timeout: float,
    ) -> HttpsTransportResponse: ...


class PinnedHttpsTransport:
    def __init__(self, *, pool_factory: Callable[..., Any] | None = None) -> None:
        self._pool_factory = pool_factory or urllib3.HTTPSConnectionPool

    def post(
        self,
        target: ValidatedHttpsTarget,
        *,
        body: bytes,
        headers: Mapping[str, str],
        timeout: float,
    ) -> HttpsTransportResponse:
        request_headers = {str(key): str(value) for key, value in headers.items()}
        request_headers["Host"] = target.host_header
        request_body = bytes(body or b"")
        pool = self._pool_factory(
            target.selected_ip,
            port=target.port,
            assert_hostname=target.hostname,
            server_hostname=target.hostname,
        )
        try:
            response = pool.request(
                "POST",
                target.request_target,
                body=request_body,
                headers=request_headers,
                timeout=urllib3.Timeout(total=float(timeout)),
                redirect=False,
                retries=False,
                preload_content=True,
            )
        except urllib3.exceptions.TimeoutError as exc:
            raise HttpsTransportTimeout("webhook request timed out") from exc
        except urllib3.exceptions.HTTPError as exc:
            raise HttpsTransportError("webhook HTTPS transport failed") from exc
        finally:
            pool.close()
        response_bytes = bytes(getattr(response, "data", b"") or b"")[:MAX_RESPONSE_BYTES]
        return HttpsTransportResponse(
            status_code=int(getattr(response, "status", 0) or 0),
            text=response_bytes.decode("utf-8", errors="replace"),
            headers={str(key): str(value) for key, value in dict(getattr(response, "headers", {}) or {}).items()},
        )


class CallableHttpsTransport:
    """Compatibility seam for injected tests; production uses PinnedHttpsTransport."""

    def __init__(self, http_post: Callable[..., Any]) -> None:
        self._http_post = http_post
        try:
            parameters = inspect.signature(http_post).parameters.values()
        except (TypeError, ValueError):
            self._supports_redirect_flag = False
        else:
            self._supports_redirect_flag = any(
                parameter.name == "allow_redirects" or parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )

    def post(
        self,
        target: ValidatedHttpsTarget,
        *,
        body: bytes,
        headers: Mapping[str, str],
        timeout: float,
    ) -> HttpsTransportResponse:
        try:
            request_headers = {str(key): str(value) for key, value in headers.items()}
            request_headers["Host"] = target.host_header
            kwargs: dict[str, Any] = {
                "data": bytes(body or b""),
                "headers": request_headers,
                "timeout": timeout,
            }
            if self._supports_redirect_flag:
                kwargs["allow_redirects"] = False
            response = self._http_post(target.url, **kwargs)
        except requests.Timeout as exc:
            raise HttpsTransportTimeout("webhook request timed out") from exc
        except requests.RequestException as exc:
            raise HttpsTransportError(str(exc)) from exc
        return HttpsTransportResponse(
            status_code=int(getattr(response, "status_code", 0) or 0),
            text=str(getattr(response, "text", "") or "")[:MAX_RESPONSE_BYTES],
            headers={str(key): str(value) for key, value in dict(getattr(response, "headers", {}) or {}).items()},
        )
