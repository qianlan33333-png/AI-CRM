#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.platform.shared.sensitive_data import redact_sensitive_text  # noqa: E402


OpenUrl = Callable[..., Any]


def _validated_url(base_url: str) -> str:
    parsed = urlparse(str(base_url or "").strip())
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("runtime_readiness_base_url_must_be_loopback_http")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("runtime_readiness_base_url_must_be_origin")
    return f"{parsed.scheme}://{parsed.netloc}/api/system/health"


def probe_runtime_readiness(
    base_url: str,
    *,
    timeout_seconds: float = 20.0,
    open_url: OpenUrl = urlopen,
) -> tuple[int, dict[str, Any]]:
    url = _validated_url(base_url)
    try:
        with open_url(url, timeout=timeout_seconds) as response:
            status = int(response.getcode())
            body = response.read()
    except HTTPError as exc:
        status = int(exc.code)
        body = exc.read()
    except Exception as exc:
        return 0, {
            "ok": False,
            "status": "probe_failed",
            "reason_code": "runtime_readiness_request_failed",
            "error_class": exc.__class__.__name__,
            "pii_in_output": False,
            "secrets_in_output": False,
        }

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {
            "ok": False,
            "status": "invalid_response",
            "reason_code": "runtime_readiness_response_invalid",
            "pii_in_output": False,
            "secrets_in_output": False,
        }
    if not isinstance(payload, dict):
        payload = {
            "ok": False,
            "status": "invalid_response",
            "reason_code": "runtime_readiness_response_not_object",
            "pii_in_output": False,
            "secrets_in_output": False,
        }
    return status, payload


def run(base_url: str, *, timeout_seconds: float = 20.0, open_url: OpenUrl = urlopen) -> int:
    status, payload = probe_runtime_readiness(
        base_url,
        timeout_seconds=timeout_seconds,
        open_url=open_url,
    )
    print(
        redact_sensitive_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    )
    if status == 200 and payload.get("ok") is True:
        return 0
    evidence = {
        "error": "candidate_runtime_readiness_blocked",
        "http_status": status,
        "failed_components": list(payload.get("failed_components") or []),
        "warning_components": list(payload.get("warning_components") or []),
        "reason_code": str(payload.get("reason_code") or "runtime_readiness_not_ready"),
    }
    print(redact_sensitive_text(json.dumps(evidence, ensure_ascii=False, sort_keys=True)), file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture bounded candidate runtime readiness evidence.")
    parser.add_argument("--base-url", default="http://127.0.0.1:5001")
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    args = parser.parse_args()
    return run(args.base_url, timeout_seconds=max(1.0, float(args.timeout_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
