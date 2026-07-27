from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

try:
    from aicrm_next.platform.shared.sensitive_data import redact_sensitive_text
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from script_runtime import ensure_repo_root_on_path

    ensure_repo_root_on_path()
    from aicrm_next.platform.shared.sensitive_data import redact_sensitive_text


UrlOpen = Callable[[urllib.request.Request, int], Any]


def build_json_post_request(
    *,
    host: str,
    port: str,
    path: str,
    payload: dict[str, object],
    token: str = "",
    base_url: str = "",
) -> urllib.request.Request:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(
        f"{base_url.rstrip('/') if base_url else f'http://{host}:{port}'}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )


def post_json(
    *,
    host: str,
    port: str,
    path: str,
    payload: dict[str, object],
    token: str = "",
    base_url: str = "",
    ssl_context: Any | None = None,
    retry_count: int = 1,
    retry_interval_seconds: int = 0,
    timeout_seconds: int = 180,
    urlopen: UrlOpen = urllib.request.urlopen,
) -> dict[str, object]:
    request = build_json_post_request(
        host=host,
        port=port,
        path=path,
        payload=payload,
        token=token,
        base_url=base_url,
    )
    if token and request.full_url.startswith("http://"):
        raise RuntimeError("OAuth access tokens must not be sent over plaintext HTTP")
    attempts = max(1, int(retry_count))
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            kwargs = {"timeout": timeout_seconds}
            if ssl_context is not None:
                kwargs["context"] = ssl_context
            with urlopen(request, **kwargs) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            print(redact_sensitive_text(exc.read().decode("utf-8", errors="replace")))
            raise
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt >= attempts:
                raise
            time.sleep(max(0, int(retry_interval_seconds)))
    assert last_error is not None
    raise last_error
