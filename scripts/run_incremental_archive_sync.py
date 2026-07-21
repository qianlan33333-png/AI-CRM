from __future__ import annotations

import os
import urllib.request

from scripts import internal_http
from scripts.script_runtime import (
    emit_json,
    ensure_repo_root_on_path,
    read_app_host,
    read_app_port,
    read_int_env,
    read_internal_access_token,
    read_internal_api_base_url,
    read_internal_tls_context,
)


DEFAULT_PATH = "/api/archive/sync"


def run() -> str:
    if os.getenv("WECOM_ARCHIVE_SYNC_MODE", "direct").strip().lower() == "http":
        return run_http()
    return run_direct()


def _payload() -> dict[str, object]:
    owner_userid = os.getenv("WECOM_DEFAULT_OWNER_USERID", "")
    return {
        "start_time": os.getenv("WECOM_ARCHIVE_SYNC_START_TIME", "2000-01-01 00:00:00"),
        "end_time": os.getenv("WECOM_ARCHIVE_SYNC_END_TIME", "2099-12-31 23:59:59"),
        "owner_userid": owner_userid,
        "cursor": os.getenv("WECOM_ARCHIVE_SYNC_CURSOR", ""),
        "limit": read_int_env("WECOM_ARCHIVE_SYNC_LIMIT", 100),
        "max_pages": read_int_env("WECOM_ARCHIVE_SYNC_MAX_PAGES", 1000),
    }


def run_direct() -> str:
    ensure_repo_root_on_path()
    from aicrm_next.admin_jobs_archive_sync_gateway import execute_archive_sync

    payload = _payload()
    response_payload = execute_archive_sync(
        start_time=str(payload["start_time"]),
        end_time=str(payload["end_time"]),
        owner_userid=str(payload["owner_userid"]),
        cursor=str(payload["cursor"]),
        limit=int(payload["limit"]),
        max_pages=int(payload["max_pages"]),
    )
    return emit_json(response_payload)


def run_http() -> str:
    host = read_app_host()
    port = read_app_port()
    token = read_internal_access_token(purpose="archive", scopes=("write",))
    response_payload = internal_http.post_json(
        host=host,
        port=port,
        token=token,
        base_url=read_internal_api_base_url(),
        ssl_context=read_internal_tls_context(),
        path=DEFAULT_PATH,
        payload=_payload(),
        timeout_seconds=read_int_env("WECOM_ARCHIVE_SYNC_TIMEOUT_SECONDS", 600),
        urlopen=urllib.request.urlopen,
    )
    return emit_json(response_payload)


def main() -> None:
    run()


if __name__ == "__main__":
    main()
