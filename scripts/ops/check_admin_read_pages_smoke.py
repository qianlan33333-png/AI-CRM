from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.app.admin_console.navigation import ADMIN_NAV_GROUPS, admin_path_for  # noqa: E402
from aicrm_next.platform.release_governance.manifest import load_release_gate_manifest  # noqa: E402
from scripts.script_runtime import print_json  # noqa: E402

REQUIRED_OPENAPI_PATHS = (
    "/api/admin/push-center/stats",
    "/api/admin/push-center/jobs",
    "/api/admin/internal-events",
    "/api/admin/wecom/tags",
    "/api/admin/wecom/tag-groups",
    "/api/admin/automation-conversion/group-ops/plans",
    "/api/admin/automation-conversion/group-ops/groups",
    "/api/admin/ai-audience/packages",
    "/api/admin/automation-agents",
    "/api/admin/user-ops/send-records",
    "/api/admin/data-health/summary",
    "/api/admin/operation-cycles/strategies",
    "/api/admin/operation-cycles/strategies/{strategy_key}",
    "/api/admin/operation-cycles/strategies/{strategy_key}/runs",
    "/api/admin/operation-cycles/runs/{run_key}",
)

DATA_HEALTH_SUMMARY_PATH = "/api/admin/data-health/summary"
DATA_HEALTH_RESPONSE_MAX_BYTES = 65536
AI_AUTOMATION_PRE_CUTOVER_CHECK_ID = "ai_automation_lane_readiness"

SMOKE_PATHS = (
    "/admin/customers",
    "/admin/automation-conversion",
    "/admin/automation-conversion/group-ops/ui",
    "/admin/wecom-tags",
    "/admin/automation-agents",
    "/api/admin/push-center/stats",
    "/api/admin/push-center/jobs?limit=1",
    "/api/admin/internal-events?limit=1",
    "/api/admin/wecom/tags",
    "/api/admin/wecom/tag-groups",
    "/api/admin/automation-conversion/group-ops/plans?limit=1",
    "/api/admin/automation-conversion/group-ops/groups?limit=1",
    "/api/admin/ai-audience/packages",
    "/api/admin/automation-agents",
    "/api/admin/user-ops/send-records?limit=1",
    "/admin/operation-cycles",
    "/api/admin/operation-cycles/strategies?limit=1",
)
SIDEBAR_PATHS = tuple(
    dict.fromkeys(
        admin_path_for(str(item["endpoint"]))
        for group in ADMIN_NAV_GROUPS
        for item in group["items"]
    )
)
DEFAULT_TIMEOUT_SECONDS = 45.0


@dataclass(frozen=True)
class ProbeResult:
    path: str
    status_code: int
    ok: bool
    duration_ms: int
    error: str = ""
    body_prefix: str = ""


def _admin_cookie_header(cookie_file: Path | None) -> tuple[str, str]:
    if cookie_file is None:
        return "", "admin_cookie_file_required"
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(cookie_file, flags)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("cookie file must be regular")
            if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
                raise PermissionError("cookie file permissions are not private")
            raw_header = os.read(fd, 4097)
            if len(raw_header) > 4096:
                raise ValueError("cookie file is invalid")
            header = raw_header.decode("utf-8")
        finally:
            os.close(fd)
        if "\n" in header or "\r" in header or ";" in header or "=" not in header:
            raise ValueError("cookie file is invalid")
        name, value = header.split("=", 1)
        if name != "aicrm_next_admin_session" or not value.startswith("ss_"):
            raise ValueError("cookie file is invalid")
        return header, ""
    except Exception as exc:
        return "", f"admin_cookie_file_failed:{exc.__class__.__name__}"


def _fetch(
    base_url: str,
    path: str,
    *,
    timeout: float,
    max_bytes: int = 4096,
    cookie_header: str = "",
) -> tuple[int, dict[str, str], str]:
    url = f"{base_url.rstrip('/')}{path}"
    headers = {"User-Agent": "aicrm-admin-read-smoke/1"}
    if cookie_header:
        headers["Cookie"] = cookie_header
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw_body = response.read(max_bytes) if max_bytes > 0 else response.read()
            body = raw_body.decode("utf-8", "replace")
            return int(response.status), dict(response.headers.items()), body
    except HTTPError as exc:
        raw_body = exc.read(max_bytes) if max_bytes > 0 else exc.read()
        body = raw_body.decode("utf-8", "replace")
        return int(exc.code), dict(exc.headers.items()), body


def _is_exact_ai_automation_pre_cutover(checks: list[Any], counts: dict[str, Any]) -> bool:
    non_green = [
        check
        for check in checks
        if isinstance(check, dict) and check.get("status") != "ok"
    ]
    if counts != {
        "ok": len(checks) - 1,
        "warn": 0,
        "fail": 1,
        "not_applicable": 0,
    }:
        return False
    if len(non_green) != 1:
        return False
    check = non_green[0]
    if check.get("check_id") != AI_AUTOMATION_PRE_CUTOVER_CHECK_ID or check.get("status") != "fail":
        return False
    evidence = check.get("evidence")
    if not isinstance(evidence, dict):
        return False
    open_job_counts = evidence.get("open_job_counts")
    if not isinstance(open_job_counts, dict):
        return False
    try:
        generation_queued_item_count = int(evidence.get("generation_queued_item_count") or 0)
        generation_open_job_count = int(open_job_counts.get("ai_generation") or 0)
    except (TypeError, ValueError):
        return False
    return bool(
        evidence.get("pre_cutover_dark_deploy") is True
        and evidence.get("lane_modes")
        == {"ai_generation": "blocked", "wecom_ai_assistant_bulk": "blocked"}
        and evidence.get("lane_updated_by")
        == {"ai_generation": "migration", "wecom_ai_assistant_bulk": "migration"}
        and evidence.get("rollout_audit_count") == 0
        and generation_queued_item_count > 0
        and generation_open_job_count > 0
    )


def _admin_api_payload_error(
    path: str,
    body: str,
    *,
    allow_ai_automation_pre_cutover: bool = False,
) -> str:
    if not path.startswith("/api/admin/"):
        return ""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    if payload.get("degraded") is True:
        reason = str(payload.get("error_code") or payload.get("read_model_status") or payload.get("source_status") or "degraded")
        return f"admin_api_degraded:{reason}"
    if payload.get("read_model_status") == "unavailable":
        return "admin_api_read_model_unavailable"
    if payload.get("source_status") == "production_unavailable":
        return "admin_api_production_unavailable"
    if path.split("?", 1)[0] == DATA_HEALTH_SUMMARY_PATH:
        counts = payload.get("counts")
        checks = payload.get("checks")
        if not isinstance(counts, dict) or not isinstance(checks, list):
            return "data_health_summary_invalid"
        expected_ids = set(load_release_gate_manifest().data_health_check_ids)
        actual_ids = {
            str(check.get("check_id") or "")
            for check in checks
            if isinstance(check, dict)
        }
        if actual_ids != expected_ids or len(actual_ids) != len(checks) or payload.get("registry_matches_manifest") is not True:
            return "data_health_registry_mismatch"
        if sum(int(counts.get(key) or 0) for key in ("ok", "warn", "fail", "not_applicable")) != len(checks):
            return "data_health_count_mismatch"
        blocking_checks = [
            check
            for check in checks
            if isinstance(check, dict)
            and str(check.get("gate_decision") or ("block" if check.get("status") == "fail" else "pass")) == "block"
        ]
        if blocking_checks or payload.get("ok") is not True:
            if (
                allow_ai_automation_pre_cutover
                and _is_exact_ai_automation_pre_cutover(checks, counts)
            ):
                return ""
            non_green_checks = [
                f"{str(check.get('check_id') or 'unknown')}:{str(check.get('reason_code') or check.get('status') or 'unknown')}"
                for check in blocking_checks
            ]
            diagnostic = ",".join(non_green_checks) or "count_mismatch"
            return f"data_health_release_blocked:{diagnostic}"
    return ""


def _probe(
    base_url: str,
    path: str,
    *,
    timeout: float,
    cookie_header: str = "",
    allow_ai_automation_pre_cutover: bool = False,
) -> ProbeResult:
    started = time.monotonic()
    try:
        max_bytes = DATA_HEALTH_RESPONSE_MAX_BYTES if path.split("?", 1)[0] == DATA_HEALTH_SUMMARY_PATH else 4096
        status_code, _headers, body = _fetch(
            base_url,
            path,
            timeout=timeout,
            max_bytes=max_bytes,
            cookie_header=cookie_header,
        )
    except (URLError, TimeoutError, OSError) as exc:
        return ProbeResult(
            path=path,
            status_code=0,
            ok=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            error=exc.__class__.__name__,
        )
    ok = status_code < 500
    error = ""
    if cookie_header:
        if path.startswith("/api/admin/") and status_code in {401, 403}:
            ok = False
            error = f"admin_cookie_rejected:{status_code}"
        if path.startswith("/admin/") and ("后台登录" in body or "admin_auth_required" in body):
            ok = False
            error = "admin_login_page_returned"
    if ok:
        payload_error = _admin_api_payload_error(
            path,
            body,
            allow_ai_automation_pre_cutover=allow_ai_automation_pre_cutover,
        )
        if payload_error:
            ok = False
            error = payload_error
    return ProbeResult(
        path=path,
        status_code=status_code,
        ok=ok,
        duration_ms=int((time.monotonic() - started) * 1000),
        error=error,
        body_prefix=body[:180].replace("\n", " "),
    )


def _openapi_paths(base_url: str, *, timeout: float, cookie_header: str = "") -> set[str]:
    status_code, _headers, body = _fetch(base_url, "/openapi.json", timeout=timeout, max_bytes=0, cookie_header=cookie_header)
    if status_code >= 500:
        raise RuntimeError(f"openapi returned {status_code}")
    payload = json.loads(body)
    paths = payload.get("paths")
    if not isinstance(paths, dict):
        raise RuntimeError("openapi paths is not an object")
    return set(paths)


def run(
    base_url: str,
    *,
    timeout: float,
    require_admin_cookie: bool = False,
    admin_cookie_file: Path | None = None,
    include_all_sidebar: bool = False,
    require_all_data_health_green: bool = False,
    allow_ai_automation_pre_cutover: bool = False,
) -> dict[str, Any]:
    cookie_header, cookie_error = _admin_cookie_header(admin_cookie_file) if require_admin_cookie else ("", "")
    paths = _openapi_paths(base_url, timeout=timeout, cookie_header=cookie_header)
    missing_paths = [path for path in REQUIRED_OPENAPI_PATHS if path not in paths]
    probe_paths = tuple(
        dict.fromkeys(
            (
                *SMOKE_PATHS,
                *((DATA_HEALTH_SUMMARY_PATH,) if require_all_data_health_green else ()),
                *(SIDEBAR_PATHS if include_all_sidebar else ()),
            )
        )
    )
    probes = [
        _probe(
            base_url,
            path,
            timeout=timeout,
            cookie_header=cookie_header,
            allow_ai_automation_pre_cutover=allow_ai_automation_pre_cutover,
        )
        for path in probe_paths
    ]
    failed_probes = [probe for probe in probes if not probe.ok]
    missing_required_cookie = require_admin_cookie and not cookie_header
    return {
        "ok": not missing_paths and not failed_probes and not missing_required_cookie,
        "admin_cookie_supplied": bool(cookie_header),
        "admin_cookie_required": require_admin_cookie,
        "admin_cookie_error": cookie_error,
        "all_sidebar_required": include_all_sidebar,
        "all_data_health_green_required": require_all_data_health_green,
        "ai_automation_pre_cutover_allowed": allow_ai_automation_pre_cutover,
        "sidebar_path_count": len(SIDEBAR_PATHS) if include_all_sidebar else 0,
        "base_url": base_url.rstrip("/"),
        "openapi_path_count": len(paths),
        "missing_openapi_paths": missing_paths,
        "probes": [probe.__dict__ for probe in probes],
        "failed_paths": [*([":admin_cookie_missing"] if missing_required_cookie else []), *[probe.path for probe in failed_probes]],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke test production admin read pages and APIs.")
    parser.add_argument("--base-url", default="http://127.0.0.1:5001")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--require-admin-cookie",
        action="store_true",
        help="Fail when a private admin smoke cookie file is missing or protected routes reject it.",
    )
    parser.add_argument(
        "--admin-cookie-file",
        type=Path,
        help="Private mode-0600 file containing the temporary admin Cookie header.",
    )
    parser.add_argument(
        "--include-all-sidebar",
        action="store_true",
        help="Probe every production admin sidebar destination in addition to the core read/API smoke set.",
    )
    parser.add_argument(
        "--require-all-data-health-green",
        action="store_true",
        help="Fail unless the production data-health summary contains exactly seventeen green checks.",
    )
    parser.add_argument(
        "--allow-ai-automation-pre-cutover",
        action="store_true",
        help="Allow only the exact unaudited migration-owned dark-deploy AI lane state.",
    )
    args = parser.parse_args(argv)
    payload = run(
        args.base_url,
        timeout=max(1.0, float(args.timeout)),
        require_admin_cookie=args.require_admin_cookie,
        admin_cookie_file=args.admin_cookie_file,
        include_all_sidebar=bool(args.include_all_sidebar),
        require_all_data_health_green=bool(args.require_all_data_health_green),
        allow_ai_automation_pre_cutover=bool(args.allow_ai_automation_pre_cutover),
    )
    print_json(payload, indent=2, sort_keys=True)
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
