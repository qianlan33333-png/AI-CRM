#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine


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


_ENV_ASSIGNMENT = re.compile(r"^(?P<prefix>\s*(?:export\s+)?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)=")
_AUDIT_REDACTION = "[redacted]"


@dataclass(frozen=True)
class _Candidate:
    key: str
    source: str
    value: str


def _read_app_settings(connection: Connection) -> dict[str, str]:
    rows = connection.execute(text("SELECT key, value FROM app_settings")).mappings().all()
    return {str(row.get("key") or "").strip(): str(row.get("value") or "") for row in rows}


def _inventory(engine: Engine, environment: Mapping[str, str]) -> list[_Candidate]:
    with engine.connect() as connection:
        stored = _read_app_settings(connection)
    result: list[_Candidate] = []
    for key in sorted(SENSITIVE_SETTING_KEYS):
        if key in stored and str(stored[key]).strip():
            result.append(_Candidate(key=key, source="app_settings", value=str(stored[key]).strip()))
            continue
        environment_value = str(environment.get(key) or "").strip()
        if environment_value:
            result.append(_Candidate(key=key, source="environment", value=environment_value))
            continue
        result.append(_Candidate(key=key, source="missing", value=""))
    return result


def _validated_reference(store: FileSecretStore, key: str, value: str) -> str:
    reference = parse_secret_reference(value)
    if reference.key != key:
        raise SecretStoreError(f"secret reference key mismatch for key={key}")
    store.read(value)
    return value


def _resolved_candidate_values(candidates: list[_Candidate], store: FileSecretStore) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for candidate in candidates:
        if not candidate.value:
            continue
        if is_secret_reference(candidate.value):
            _validated_reference(store, candidate.key, candidate.value)
            resolved[candidate.key] = store.read(candidate.value)
        else:
            resolved[candidate.key] = candidate.value
    return resolved


def _all_secret_values(candidates: list[_Candidate], store: FileSecretStore) -> set[str]:
    values = set(_resolved_candidate_values(candidates, store).values())
    for reference in store.list_references():
        values.add(store.read(reference))
    return {value for value in values if value}


def _replace_secret_substrings(value: str, secret_values: tuple[str, ...]) -> str:
    redacted = value
    for secret_value in secret_values:
        redacted = redacted.replace(secret_value, _AUDIT_REDACTION)
    return redacted


def _redact_audit_value(value: Any, secret_values: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        return {key: _redact_audit_value(item, secret_values) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_audit_value(item, secret_values) for item in value]
    if isinstance(value, tuple):
        return [_redact_audit_value(item, secret_values) for item in value]
    if isinstance(value, str):
        return _replace_secret_substrings(value, secret_values)
    return value


def _parsed_audit_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _audit_redaction_plan(connection: Connection, secret_values: set[str]) -> list[dict[str, Any]]:
    ordered_secret_values = tuple(sorted(secret_values, key=lambda value: (-len(value), value)))
    if not ordered_secret_values:
        return []
    rows = connection.execute(text("SELECT id, before_json, after_json FROM admin_operation_logs ORDER BY id")).mappings().all()
    updates: list[dict[str, Any]] = []
    for row in rows:
        before = _parsed_audit_value(row.get("before_json"))
        after = _parsed_audit_value(row.get("after_json"))
        redacted_before = _redact_audit_value(before, ordered_secret_values)
        redacted_after = _redact_audit_value(after, ordered_secret_values)
        if redacted_before == before and redacted_after == after:
            continue
        updates.append(
            {
                "id": int(row["id"]),
                "before_json": json.dumps(redacted_before, ensure_ascii=False, sort_keys=True),
                "after_json": json.dumps(redacted_after, ensure_ascii=False, sort_keys=True),
            }
        )
    return updates


def _redact_audit_rows(connection: Connection, secret_values: set[str]) -> int:
    updates = _audit_redaction_plan(connection, secret_values)
    if not updates:
        return 0
    if connection.dialect.name == "postgresql":
        statement = text(
            """
            UPDATE admin_operation_logs
            SET before_json = CAST(:before_json AS jsonb), after_json = CAST(:after_json AS jsonb)
            WHERE id = :id
            """
        )
    else:
        statement = text(
            """
            UPDATE admin_operation_logs
            SET before_json = :before_json, after_json = :after_json
            WHERE id = :id
            """
        )
    connection.execute(statement, updates)
    return len(updates)


def _safe_item(*, key: str, source: str, present: bool, status: str, reference: str = "") -> dict[str, Any]:
    version = parse_secret_reference(reference).version if reference else ""
    return {
        "key": key,
        "source": source,
        "version": version,
        "present": present,
        "status": status,
    }


def _single_quoted(value: str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def _write_all(file_descriptor: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(file_descriptor, data[offset:])
        if written <= 0:
            raise RuntimeError("runtime environment write was incomplete")
        offset += written


def _persist_environment_values(
    path: Path,
    values: Mapping[str, str],
    *,
    remove_keys: set[str] | frozenset[str] = frozenset(),
) -> None:
    target = path.expanduser()
    if not target.is_absolute():
        raise ValueError("runtime environment file path must be absolute")
    if target.exists():
        metadata = target.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ValueError("runtime environment file must be a regular file")
        mode = stat.S_IMODE(metadata.st_mode)
        if mode & 0o077:
            raise ValueError("runtime environment file must not be group/world accessible")
        body = target.read_text(encoding="utf-8")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        body = ""
        mode = 0o600

    managed = {str(key): str(value) for key, value in values.items()}
    removed = {str(key) for key in remove_keys}
    if set(managed) & removed:
        raise ValueError("environment key cannot be managed and removed together")
    pending = dict(managed)
    persisted: set[str] = set()
    lines: list[str] = []
    for line in body.splitlines():
        match = _ENV_ASSIGNMENT.match(line)
        key = str(match.group("key")) if match else ""
        if key in removed:
            continue
        if key in managed:
            if key in persisted:
                continue
            prefix = str(match.group("prefix") or "")
            lines.append(f"{prefix}{key}={_single_quoted(managed[key])}")
            persisted.add(key)
            pending.pop(key, None)
        else:
            lines.append(line)
    for key in sorted(pending):
        lines.append(f"{key}={_single_quoted(pending[key])}")
    encoded = (("\n".join(lines) + "\n") if lines else "").encode("utf-8")

    target.parent.mkdir(parents=True, exist_ok=True)
    directory_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    temporary_name = f".{target.name}.tmp-{secrets.token_hex(8)}"
    temporary_created = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        file_descriptor = os.open(temporary_name, flags, mode, dir_fd=directory_fd)
        temporary_created = True
        try:
            os.fchmod(file_descriptor, mode)
            _write_all(file_descriptor, encoded)
            os.fsync(file_descriptor)
        finally:
            os.close(file_descriptor)
        os.replace(temporary_name, target.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temporary_created = False
        os.fsync(directory_fd)
    finally:
        if temporary_created:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except OSError:
                pass
        os.close(directory_fd)


def _insert_reference(connection: Connection, *, key: str, reference: str) -> None:
    connection.execute(
        text(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (:key, :value, CURRENT_TIMESTAMP)
            """
        ),
        {"key": key, "value": reference},
    )


def _replace_plaintext_reference(connection: Connection, *, key: str, plaintext: str, reference: str) -> None:
    result = connection.execute(
        text(
            """
            UPDATE app_settings
            SET value = :reference, updated_at = CURRENT_TIMESTAMP
            WHERE key = :key AND value = :plaintext
            """
        ),
        {"key": key, "plaintext": plaintext, "reference": reference},
    )
    if result.rowcount != 1:
        raise RuntimeError(f"app setting changed during secret migration for key={key}")


def _enable_cutover(connection: Connection) -> None:
    connection.execute(
        text(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (:key, 'true', CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
            """
        ),
        {"key": SECRET_REFERENCE_CUTOVER_KEY},
    )


def migrate_app_setting_secrets(
    *,
    engine: Engine,
    store: FileSecretStore,
    environment: Mapping[str, str],
    dry_run: bool,
    transaction_hook: Callable[[Connection], None] | None = None,
    environment_file: Path | None = None,
) -> dict[str, Any]:
    candidates = _inventory(engine, environment)
    prepared: dict[str, str] = {}
    items: list[dict[str, Any]] = []
    plaintext_pending = 0
    migrated = 0
    already_referenced = 0
    audit_secret_values = _all_secret_values(candidates, store)
    with engine.connect() as connection:
        audit_rows_redaction_pending = len(_audit_redaction_plan(connection, audit_secret_values))
    for candidate in candidates:
        if not candidate.value:
            items.append(_safe_item(key=candidate.key, source=candidate.source, present=False, status="missing"))
            continue
        if is_secret_reference(candidate.value):
            reference = _validated_reference(store, candidate.key, candidate.value)
            prepared[candidate.key] = reference
            already_referenced += 1
            items.append(
                _safe_item(
                    key=candidate.key,
                    source=candidate.source,
                    present=True,
                    status="already_referenced",
                    reference=reference,
                )
            )
            continue
        plaintext_pending += 1
        if dry_run:
            items.append(
                _safe_item(
                    key=candidate.key,
                    source=candidate.source,
                    present=True,
                    status="plaintext_pending",
                )
            )
            continue
        reference = store.write(candidate.key, candidate.value)
        prepared[candidate.key] = reference
        migrated += 1
        items.append(
            _safe_item(
                key=candidate.key,
                source=candidate.source,
                present=True,
                status="migrated",
                reference=reference,
            )
        )

    if dry_run:
        return {
            "ok": bool(plaintext_pending == 0 and audit_rows_redaction_pending == 0),
            "dry_run": True,
            "configured": sum(1 for item in items if item["present"]),
            "plaintext_pending": plaintext_pending,
            "migrated": 0,
            "already_referenced": already_referenced,
            "audit_rows_redacted": 0,
            "audit_rows_redaction_pending": audit_rows_redaction_pending,
            "cutover_enabled": False,
            "environment_file_updated": False,
            "items": items,
        }

    audit_secret_values = _all_secret_values(candidates, store)

    environment_file_updated = False
    if environment_file is not None:
        _persist_environment_values(environment_file, {SECRET_STORE_DIR_KEY: str(store.root)})

    original_by_key = {candidate.key: candidate for candidate in candidates if candidate.value}
    with engine.begin() as connection:
        current = _read_app_settings(connection)
        for key, reference in prepared.items():
            original = original_by_key[key]
            current_value = str(current.get(key) or "").strip()
            if original.source == "app_settings" and current_value != original.value:
                raise RuntimeError(f"app setting changed during secret migration for key={key}")
            if original.source == "environment" and current_value:
                raise RuntimeError(f"app setting appeared during secret migration for key={key}")
            if original.source == "environment":
                _insert_reference(connection, key=key, reference=reference)
            elif not is_secret_reference(original.value):
                _replace_plaintext_reference(
                    connection,
                    key=key,
                    plaintext=original.value,
                    reference=reference,
                )
        persisted = _read_app_settings(connection)
        for key, expected_reference in prepared.items():
            persisted_reference = str(persisted.get(key) or "").strip()
            if persisted_reference != expected_reference:
                raise RuntimeError(f"secret reference persistence mismatch for key={key}")
            _validated_reference(store, key, persisted_reference)
        audit_rows_redacted = _redact_audit_rows(connection, audit_secret_values)
        _enable_cutover(connection)
        if transaction_hook is not None:
            transaction_hook(connection)

    if environment_file is not None:
        environment_references = {key: reference for key, reference in prepared.items() if str(environment.get(key) or "").strip()}
        _persist_environment_values(
            environment_file,
            {
                **environment_references,
                SECRET_STORE_DIR_KEY: str(store.root),
                SECRET_REFERENCE_CUTOVER_KEY: "true",
            },
        )
        environment_file_updated = True

    return {
        "ok": True,
        "dry_run": False,
        "configured": sum(1 for item in items if item["present"]),
        "plaintext_pending": 0,
        "migrated": migrated,
        "already_referenced": already_referenced,
        "audit_rows_redacted": audit_rows_redacted,
        "audit_rows_redaction_pending": 0,
        "cutover_enabled": True,
        "environment_file_updated": environment_file_updated,
        "items": items,
    }


def rollback_secret_reference(
    *,
    engine: Engine,
    store: FileSecretStore,
    key: str,
    reference: str,
    environment_file: Path | None = None,
) -> dict[str, Any]:
    normalized_key = str(key or "").strip()
    if normalized_key not in SENSITIVE_SETTING_KEYS:
        raise ValueError("rollback key is not a sensitive setting")
    validated = _validated_reference(store, normalized_key, str(reference or "").strip())
    with engine.begin() as connection:
        current = _read_app_settings(connection)
        if normalized_key in current:
            connection.execute(
                text("UPDATE app_settings SET value = :value, updated_at = CURRENT_TIMESTAMP WHERE key = :key"),
                {"key": normalized_key, "value": validated},
            )
        else:
            _insert_reference(connection, key=normalized_key, reference=validated)
        _enable_cutover(connection)
    if environment_file is not None:
        _persist_environment_values(
            environment_file,
            {
                normalized_key: validated,
                SECRET_STORE_DIR_KEY: str(store.root),
                SECRET_REFERENCE_CUTOVER_KEY: "true",
            },
        )
    return _safe_item(
        key=normalized_key,
        source="app_settings",
        present=True,
        status="rolled_back",
        reference=validated,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Migrate sensitive app settings to immutable file references.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Write secret versions, DB references, and the cutover sentinel.")
    mode.add_argument("--dry-run", action="store_true", help="Inventory only; this is the default.")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--secret-store-dir", default=os.getenv(SECRET_STORE_DIR_KEY, ""))
    parser.add_argument("--environment-file", type=Path)
    parser.add_argument("--rollback-key", default="")
    parser.add_argument("--rollback-reference", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        store = FileSecretStore(args.secret_store_dir)
        engine = get_engine(args.database_url or None)
        if bool(args.rollback_key) != bool(args.rollback_reference):
            raise ValueError("rollback arguments must be provided together")
        if args.rollback_key:
            if not args.execute:
                raise ValueError("rollback requires execute mode")
            report = rollback_secret_reference(
                engine=engine,
                store=store,
                key=args.rollback_key,
                reference=args.rollback_reference,
                environment_file=args.environment_file,
            )
        else:
            report = migrate_app_setting_secrets(
                engine=engine,
                store=store,
                environment=os.environ,
                dry_run=not args.execute,
                environment_file=args.environment_file,
            )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
