from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aicrm_next.shared.sensitive_data import redact_sensitive_data

from .sql_linter import extract_params, lint_sql


REFRESH_MODES = {"manual", "incremental_3m", "daily_0200", "incremental_3m_plus_daily_0200"}
SYSTEM_SQL_PARAMS = {"last_watermark_at", "refresh_started_at", "lookback_seconds", "package_id"}


@dataclass(frozen=True)
class PackageSpec:
    path: Path
    frontmatter: dict[str, Any]
    incremental_sql: str = ""
    snapshot_sql: str = ""

    @property
    def package_key(self) -> str:
        return str(self.frontmatter.get("package_key") or "").strip()


def parse_markdown_spec(path: str | Path) -> PackageSpec:
    spec_path = Path(path)
    return parse_markdown_spec_text(spec_path.read_text(encoding="utf-8"), path=spec_path)


def parse_markdown_spec_text(markdown: str, *, path: str | Path = "<inline>") -> PackageSpec:
    frontmatter, body = _split_frontmatter(str(markdown or ""))
    metadata = _load_frontmatter(frontmatter)
    sql_blocks = _extract_sql_blocks(body)
    return PackageSpec(
        path=Path(path),
        frontmatter=metadata,
        incremental_sql=sql_blocks.get("incremental", ""),
        snapshot_sql=sql_blocks.get("snapshot", ""),
    )


def validate_spec(spec: PackageSpec) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    metadata = spec.frontmatter

    for key in ("package_key", "name", "refresh_mode", "natural_language_definition"):
        if not str(metadata.get(key) or "").strip():
            errors.append(f"frontmatter_required:{key}")

    refresh_mode = str(metadata.get("refresh_mode") or "").strip()
    if refresh_mode and refresh_mode not in REFRESH_MODES:
        errors.append("invalid_refresh_mode")

    if refresh_mode in {"incremental_3m", "incremental_3m_plus_daily_0200"} and not spec.incremental_sql:
        errors.append("incremental_sql_required")
    if refresh_mode in {"daily_0200", "incremental_3m_plus_daily_0200"} and not spec.snapshot_sql:
        errors.append("snapshot_sql_required")

    parameters = metadata.get("parameters") if isinstance(metadata.get("parameters"), dict) else {}
    for kind, sql_text in (("incremental", spec.incremental_sql), ("snapshot", spec.snapshot_sql)):
        if not sql_text:
            continue
        validation = lint_sql(sql_text)
        errors.extend(f"{kind}:{item}" for item in validation.errors)
        undeclared = [item for item in extract_params(sql_text) if item not in parameters and item not in SYSTEM_SQL_PARAMS]
        errors.extend(f"{kind}:parameter_not_declared:{item}" for item in undeclared)

    webhook = metadata.get("webhook") if isinstance(metadata.get("webhook"), dict) else {}
    if webhook.get("payload_template") or webhook.get("headers"):
        errors.append("webhook_payload_or_headers_not_allowed")

    senders = metadata.get("senders") if isinstance(metadata.get("senders"), list) else []
    priorities: list[int] = []
    for index, sender in enumerate(senders):
        if not isinstance(sender, dict):
            errors.append(f"sender_invalid:{index}")
            continue
        if not str(sender.get("sender_userid") or "").strip():
            errors.append(f"sender_userid_required:{index}")
        status = str(sender.get("status") or "active").strip()
        if status not in {"active", "paused"}:
            errors.append(f"sender_status_invalid:{index}")
        try:
            priorities.append(int(sender.get("priority") or 100))
        except Exception:
            errors.append(f"sender_priority_invalid:{index}")
    if priorities and priorities != sorted(priorities):
        warnings.append("senders_should_be_sorted_by_priority")

    return sorted(set(errors)), sorted(set(warnings))


def package_payload_from_spec(spec: PackageSpec, *, package_key: str) -> dict[str, Any]:
    metadata = spec.frontmatter
    return {
        "package_key": package_key,
        "name": str(metadata.get("name") or "").strip(),
        "status": str(metadata.get("status") or "paused").strip(),
        "query_mode": str(metadata.get("query_mode") or "incremental_event").strip(),
        "identity_policy": str(metadata.get("identity_policy") or "external_userid").strip(),
        "refresh_mode": str(metadata.get("refresh_mode") or "manual").strip(),
        "natural_language_definition": str(metadata.get("natural_language_definition") or "").strip(),
        "parameters": metadata.get("parameters") if isinstance(metadata.get("parameters"), dict) else {},
        "incremental_sql_text": spec.incremental_sql,
        "snapshot_sql_text": spec.snapshot_sql,
    }


def redact_report(value: Any) -> str:
    return json.dumps(redact_sensitive_data(value), ensure_ascii=False, indent=2, default=str)


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---", 4)
    if end < 0:
        return "", text
    body_start = text.find("\n", end + 4)
    return text[4:end].strip(), text[body_start + 1 :].lstrip() if body_start >= 0 else ""


def _load_frontmatter(text: str) -> dict[str, Any]:
    if not text:
        return {}
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text) or {}
        return dict(data) if isinstance(data, dict) else {}
    except Exception:
        return _load_simple_yaml(text)


def _load_simple_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    current_key = ""
    current_list_item: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if indent == 0 and ":" in line:
            key, value = line.split(":", 1)
            current_key = key.strip()
            current_list_item = None
            root[current_key] = _parse_scalar(value.strip()) if value.strip() else {}
            continue
        if indent == 2 and line.startswith("- ") and current_key:
            if not isinstance(root.get(current_key), list):
                root[current_key] = []
            current_list_item = {}
            root[current_key].append(current_list_item)
            item_line = line[2:].strip()
            if ":" in item_line:
                key, value = item_line.split(":", 1)
                current_list_item[key.strip()] = _parse_scalar(value.strip())
            continue
        if indent >= 2 and ":" in line and current_key:
            key, value = line.split(":", 1)
            target: dict[str, Any]
            if current_list_item is not None:
                target = current_list_item
            else:
                if not isinstance(root.get(current_key), dict):
                    root[current_key] = {}
                target = root[current_key]
            target[key.strip()] = _parse_scalar(value.strip())
    return root


def _parse_scalar(value: str) -> Any:
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None", "~"}:
        return None
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value.strip("'\"")


def _extract_sql_blocks(body: str) -> dict[str, str]:
    result: dict[str, str] = {}
    current_kind = ""
    heading_re = re.compile(r"^#{1,6}\s+(.*)$")
    fence_re = re.compile(r"^```(\w*)\s*$")
    lines = body.splitlines()
    index = 0
    while index < len(lines):
        heading = heading_re.match(lines[index].strip())
        if heading:
            title = heading.group(1).lower()
            if "incremental sql" in title:
                current_kind = "incremental"
            elif "snapshot sql" in title:
                current_kind = "snapshot"
        fence = fence_re.match(lines[index].strip())
        if fence and current_kind:
            block: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                block.append(lines[index])
                index += 1
            result[current_kind] = "\n".join(block).strip()
            current_kind = ""
        index += 1
    return result
