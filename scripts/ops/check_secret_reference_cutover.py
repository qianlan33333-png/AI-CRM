#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import stat
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.platform.shared.db_session import get_engine  # noqa: E402
from aicrm_next.platform.shared.secret_store import (  # noqa: E402
    SECRET_REFERENCE_CUTOVER_KEY,
    SECRET_STORE_DIR_KEY,
    SENSITIVE_SETTING_KEYS,
    FileSecretStore,
    SecretStoreError,
    is_secret_reference,
    parse_secret_reference,
)


_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
_PERMISSION_ERROR_MARKERS = ("mode", "owned", "symlink", "directory")
_ENV_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")


def _read_settings(engine: Engine) -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT key, value FROM app_settings")).mappings().all()
    return {str(row.get("key") or "").strip(): str(row.get("value") or "").strip() for row in rows}


def _audit_rows(engine: Engine) -> tuple[list[str], int]:
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT before_json, after_json FROM admin_operation_logs")).all()
    except SQLAlchemyError:
        return [], 1
    return ["\n".join(str(value or "") for value in row) for row in rows], 0


def _is_permission_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in _PERMISSION_ERROR_MARKERS)


def _environment_secret_values(path: Path | None) -> tuple[dict[str, str], int, int]:
    if path is None:
        return {}, 0, 0
    try:
        target = path.expanduser()
        metadata = target.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            return {}, 0, 1
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            return {}, 0, 1
        if hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
            return {}, 0, 1
        body = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {}, 1, 0
    values: dict[str, str] = {}
    parse_errors = 0
    for line in body.splitlines():
        match = _ENV_ASSIGNMENT.match(line)
        if not match:
            continue
        key = str(match.group("key") or "")
        if key not in SENSITIVE_SETTING_KEYS:
            continue
        try:
            parsed = shlex.split(str(match.group("value") or ""), comments=True, posix=True)
        except ValueError:
            parse_errors += 1
            continue
        if len(parsed) > 1:
            parse_errors += 1
            continue
        values[key] = parsed[0] if parsed else ""
    return values, parse_errors, 0


def reconcile_secret_reference_cutover(
    *,
    engine: Engine,
    store: FileSecretStore,
    environment_file: Path | None = None,
) -> dict[str, Any]:
    settings = _read_settings(engine)
    plaintext_sensitive_rows = 0
    unresolved_refs = 0
    permission_errors = 0
    store_scan_errors = 0
    secret_values: set[str] = set()
    items: list[dict[str, Any]] = []

    for key in sorted(SENSITIVE_SETTING_KEYS):
        value = str(settings.get(key) or "").strip()
        if not value:
            continue
        if not is_secret_reference(value):
            plaintext_sensitive_rows += 1
            items.append({"key": key, "present": True, "status": "plaintext", "version": ""})
            continue
        try:
            parsed = parse_secret_reference(value)
            if parsed.key != key:
                raise SecretStoreError("secret reference key mismatch")
            resolved = store.read(value)
            secret_values.add(resolved)
            items.append({"key": key, "present": True, "status": "resolved", "version": parsed.version})
        except SecretStoreError as exc:
            unresolved_refs += 1
            if _is_permission_error(exc):
                permission_errors += 1
            items.append({"key": key, "present": True, "status": "unresolved", "version": ""})

    try:
        for reference in store.list_references():
            secret_values.add(store.read(reference))
    except SecretStoreError as exc:
        store_scan_errors += 1
        if _is_permission_error(exc):
            permission_errors += 1

    audit_rows, audit_scan_errors = _audit_rows(engine)
    unsafe_audit_hits = sum(1 for row in audit_rows if any(secret and secret in row for secret in secret_values))
    environment_values, environment_scan_errors, environment_permission_errors = _environment_secret_values(environment_file)
    plaintext_environment_entries = 0
    unresolved_environment_refs = 0
    environment_reference_mismatches = 0
    for key, value in environment_values.items():
        if not value:
            continue
        if not is_secret_reference(value):
            plaintext_environment_entries += 1
            continue
        try:
            parsed = parse_secret_reference(value)
            if parsed.key != key:
                raise SecretStoreError("secret reference key mismatch")
            resolved = store.read(value)
            database_reference = str(settings.get(key) or "").strip()
            if database_reference and database_reference != value:
                environment_reference_mismatches += 1
        except SecretStoreError:
            unresolved_environment_refs += 1
    cutover_enabled = str(settings.get(SECRET_REFERENCE_CUTOVER_KEY) or "").strip().lower() in _TRUE_VALUES
    ok = bool(
        cutover_enabled
        and plaintext_sensitive_rows == 0
        and unresolved_refs == 0
        and unsafe_audit_hits == 0
        and permission_errors == 0
        and store_scan_errors == 0
        and audit_scan_errors == 0
        and plaintext_environment_entries == 0
        and unresolved_environment_refs == 0
        and environment_scan_errors == 0
        and environment_permission_errors == 0
        and environment_reference_mismatches == 0
    )
    return {
        "ok": ok,
        "cutover_enabled": cutover_enabled,
        "configured_sensitive_rows": len(items),
        "plaintext_sensitive_rows": plaintext_sensitive_rows,
        "unresolved_refs": unresolved_refs,
        "unsafe_audit_hits": unsafe_audit_hits,
        "permission_errors": permission_errors,
        "store_scan_errors": store_scan_errors,
        "audit_scan_errors": audit_scan_errors,
        "plaintext_environment_entries": plaintext_environment_entries,
        "unresolved_environment_refs": unresolved_environment_refs,
        "environment_scan_errors": environment_scan_errors,
        "environment_permission_errors": environment_permission_errors,
        "environment_reference_mismatches": environment_reference_mismatches,
        "items": items,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reconcile the app-setting secret reference cutover.")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--secret-store-dir", default=os.getenv(SECRET_STORE_DIR_KEY, ""))
    parser.add_argument("--environment-file", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        report = reconcile_secret_reference_cutover(
            engine=get_engine(args.database_url or None),
            store=FileSecretStore(args.secret_store_dir),
            environment_file=args.environment_file,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
