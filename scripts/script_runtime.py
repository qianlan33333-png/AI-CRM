from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent


def ensure_repo_root_on_path() -> Path:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from aicrm_next.platform.platform_foundation.error_reporting import install_process_error_reporting

    install_process_error_reporting(component=Path(sys.argv[0] or "python_process").name)
    return REPO_ROOT


def print_json(payload: Any, *, indent: int | None = None, sort_keys: bool = False) -> None:
    ensure_repo_root_on_path()
    from aicrm_next.platform.platform_foundation.error_reporting import report_failed_result
    from aicrm_next.platform.shared.sensitive_data import redact_sensitive_data

    report_failed_result(payload, component=Path(sys.argv[0] or "python_process").name)
    print(dump_json(redact_sensitive_data(payload), indent=indent, sort_keys=sort_keys))


def dump_json(payload: Any, *, indent: int | None = None, sort_keys: bool = False) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str, indent=indent, sort_keys=sort_keys)


def emit_json(payload: Any, *, indent: int | None = None, sort_keys: bool = False) -> str:
    ensure_repo_root_on_path()
    from aicrm_next.platform.shared.sensitive_data import redact_sensitive_data

    body = dump_json(redact_sensitive_data(payload), indent=indent, sort_keys=sort_keys)
    print(body)
    return body


def read_int_env(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


def read_app_host() -> str:
    return os.getenv("APP_HOST", "127.0.0.1").strip() or "127.0.0.1"


def read_app_port() -> str:
    return os.getenv("APP_PORT", "5000").strip() or "5000"


def read_internal_api_base_url() -> str:
    value = os.getenv("AICRM_INTERNAL_API_BASE_URL", "").strip().rstrip("/")
    if not value.startswith("https://"):
        raise RuntimeError("AICRM_INTERNAL_API_BASE_URL must be an HTTPS URL")
    return value


def read_internal_tls_context():
    ensure_repo_root_on_path()
    from aicrm_next.platform.platform_foundation.auth_platform.access_client import build_tls_ssl_context

    return build_tls_ssl_context()


def read_internal_access_token(
    *,
    purpose: str = "automation_worker",
    audience: str = "internal_worker",
    scopes: tuple[str, ...] = ("write",),
) -> str:
    ensure_repo_root_on_path()
    from aicrm_next.platform.platform_foundation.auth_platform.access_client import fetch_internal_access_token

    return fetch_internal_access_token(
        purpose=purpose,
        audience=audience,
        scopes=scopes,
    ).access_token
