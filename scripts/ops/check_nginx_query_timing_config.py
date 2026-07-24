#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


LOG_FORMAT_NAME = "aicrm_query_timing"
REQUIRED_VARIABLES = frozenset(
    {
        "request_id",
        "request_method",
        "request_time",
        "status",
        "time_iso8601",
        "upstream_connect_time",
        "upstream_response_time",
        "upstream_status",
    }
)
FORBIDDEN_VARIABLES = frozenset(
    {
        "args",
        "binary_remote_addr",
        "remote_addr",
        "remote_user",
        "request",
        "request_body",
        "request_uri",
        "uri",
    }
)
FORBIDDEN_PREFIXES = ("arg_", "cookie_", "http_", "sent_http_", "upstream_http_")


def _strip_comments(content: str) -> str:
    effective_lines: list[str] = []
    for line in content.splitlines():
        output: list[str] = []
        quote: str | None = None
        escaped = False
        for character in line:
            if escaped:
                output.append(character)
                escaped = False
                continue
            if quote is not None:
                output.append(character)
                if character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
                continue
            if character in {"'", '"'}:
                quote = character
                output.append(character)
            elif character == "#":
                break
            else:
                output.append(character)
        effective_lines.append("".join(output))
    return "\n".join(effective_lines)


def _log_format_body(content: str) -> str | None:
    header = re.search(
        rf"\blog_format\s+{re.escape(LOG_FORMAT_NAME)}\b",
        content,
    )
    if header is None:
        return None
    quote: str | None = None
    escaped = False
    body: list[str] = []
    for character in content[header.end() :]:
        if escaped:
            body.append(character)
            escaped = False
            continue
        if quote is not None:
            body.append(character)
            if character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
            body.append(character)
        elif character == ";":
            return "".join(body)
        else:
            body.append(character)
    return None


def _empty_report() -> dict[str, Any]:
    return {
        "log_format_found": False,
        "required_variables_present": False,
        "missing_variables": sorted(REQUIRED_VARIABLES),
        "forbidden_variables": [],
        "access_log_enabled": False,
        "ready": False,
    }


def analyze_nginx_config(content: str) -> dict[str, Any]:
    effective = _strip_comments(content)
    body = _log_format_body(effective)
    if body is None:
        return _empty_report()

    variables = set(re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", body))
    missing_variables = sorted(REQUIRED_VARIABLES - variables)
    forbidden_variables = sorted(
        variable
        for variable in variables
        if variable in FORBIDDEN_VARIABLES
        or any(variable.startswith(prefix) for prefix in FORBIDDEN_PREFIXES)
    )
    access_log_enabled = bool(
        re.search(
            rf"\baccess_log\s+\S+\s+{re.escape(LOG_FORMAT_NAME)}(?:\s|;)",
            effective,
        )
    )
    required_variables_present = not missing_variables
    return {
        "log_format_found": True,
        "required_variables_present": required_variables_present,
        "missing_variables": missing_variables,
        "forbidden_variables": forbidden_variables,
        "access_log_enabled": access_log_enabled,
        "ready": required_variables_present and not forbidden_variables and access_log_enabled,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check privacy-safe Nginx request/upstream timing telemetry configuration."
    )
    parser.add_argument("--config", required=True, help="Reviewed Nginx config or effective dump")
    args = parser.parse_args()

    path = Path(args.config)
    if not path.is_file():
        report = _empty_report()
        report["config_found"] = False
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 2

    report = analyze_nginx_config(path.read_text(encoding="utf-8", errors="replace"))
    report["config_found"] = True
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
