from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from aicrm_next.extensions.ai.ai_audience_ops.automation_binding.precheck import (
    audience_package_key_from_webhook_url,
    inspect_automation_bindings,
)
from aicrm_next.platform.shared.db_session import get_session_factory
from aicrm_next.platform.shared.sensitive_data import redact_sensitive_data


class RemediationError(RuntimeError):
    def __init__(self, code: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.details = details or {}


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        items = [_canonical(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    return value


def issue_fingerprint(issues: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> str:
    normalized = [_canonical(dict(item)) for item in issues]
    normalized.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def issue_kind_counts(issues: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> dict[str, int]:
    return dict(sorted(Counter(str(item.get("kind") or "") for item in issues).items()))


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if int(manifest.get("schema_version") or 0) != 1:
        raise RemediationError("manifest_schema_version_invalid")
    if not re.fullmatch(r"[a-z0-9_-]{8,80}", str(manifest.get("operation_id") or "")):
        raise RemediationError("manifest_operation_id_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("expected_issue_fingerprint") or "")):
        raise RemediationError("manifest_fingerprint_invalid")
    if int(manifest.get("automation_id") or 0) <= 0:
        raise RemediationError("manifest_automation_id_invalid")
    if int(manifest.get("expected_issue_count") or 0) <= 0:
        raise RemediationError("manifest_issue_count_invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("expected_production_sha") or "")):
        raise RemediationError("manifest_production_sha_invalid")
    expected_counts = manifest.get("expected_issue_kind_counts")
    if not isinstance(expected_counts, dict) or not expected_counts:
        raise RemediationError("manifest_issue_kind_counts_invalid")
    if any(not str(key) or int(value) <= 0 for key, value in expected_counts.items()):
        raise RemediationError("manifest_issue_kind_counts_invalid")
    if not re.fullmatch(r"[A-Z0-9_]{16,120}", str(manifest.get("execute_confirmation") or "")):
        raise RemediationError("manifest_confirmation_invalid")
    return manifest


def _select_by_ids(session: Session, table: str, ids: list[int], *, lock: bool) -> list[dict[str, Any]]:
    if not ids:
        return []
    parameters = {f"row_id_{index}": int(row_id) for index, row_id in enumerate(ids)}
    placeholders = ", ".join(f":{key}" for key in parameters)
    suffix = " FOR UPDATE" if lock else ""
    rows = session.execute(
        text(f"SELECT * FROM {table} WHERE id IN ({placeholders}) ORDER BY id ASC{suffix}"),
        parameters,
    ).mappings()
    return [dict(row) for row in rows]


def _package_rows(session: Session, package_ids: list[int], package_keys: list[str]) -> list[dict[str, Any]]:
    clauses: list[str] = []
    parameters: dict[str, Any] = {}
    if package_ids:
        id_keys = []
        for index, package_id in enumerate(package_ids):
            key = f"package_id_{index}"
            parameters[key] = int(package_id)
            id_keys.append(f":{key}")
        clauses.append(f"id IN ({', '.join(id_keys)})")
    if package_keys:
        package_key_keys = []
        for index, package_key in enumerate(package_keys):
            key = f"package_key_{index}"
            parameters[key] = str(package_key)
            package_key_keys.append(f":{key}")
        clauses.append(f"package_key IN ({', '.join(package_key_keys)})")
    if not clauses:
        return []
    rows = session.execute(
        text(f"SELECT * FROM ai_audience_package WHERE {' OR '.join(clauses)} ORDER BY id ASC"),
        parameters,
    ).mappings()
    return [dict(row) for row in rows]


def _already_applied(session: Session, manifest: dict[str, Any]) -> bool:
    agent = (
        session.execute(
            text(
                """
            SELECT id, bound_package_key, send_webhook_url, status
            FROM automation_agent_runtime_config
            WHERE id = :automation_id
              AND status <> 'archived'
            LIMIT 1
            """
            ),
            {"automation_id": int(manifest["automation_id"])},
        )
        .mappings()
        .fetchone()
    )
    if not agent:
        return False
    target_key = audience_package_key_from_webhook_url(agent.get("send_webhook_url"))
    return bool(target_key and str(agent.get("bound_package_key") or "").strip() == target_key)


def _build_plan(session: Session, manifest: dict[str, Any], *, lock: bool) -> dict[str, Any]:
    report = inspect_automation_bindings(session.connection())
    if report.ok:
        if _already_applied(session, manifest):
            return {"status": "already_applied", "report": report}
        raise RemediationError("clean_state_does_not_match_authorized_result")

    actual_fingerprint = issue_fingerprint(report.issues)
    actual_counts = issue_kind_counts(report.issues)
    expected_counts = {str(key): int(value) for key, value in manifest["expected_issue_kind_counts"].items()}
    if (
        len(report.issues) != int(manifest["expected_issue_count"])
        or actual_counts != dict(sorted(expected_counts.items()))
        or actual_fingerprint != str(manifest["expected_issue_fingerprint"])
    ):
        raise RemediationError(
            "unexpected_issue_envelope",
            details={
                "actual_issue_count": len(report.issues),
                "actual_issue_fingerprint": actual_fingerprint,
                "actual_issue_kind_counts": actual_counts,
            },
        )

    automation_id = int(manifest["automation_id"])
    orphan_agent = [item for item in report.issues if item.get("kind") == "orphan_agent_binding" and int(item.get("automation_id") or 0) == automation_id]
    mismatches = [item for item in report.issues if item.get("kind") == "agent_package_mismatch" and int(item.get("automation_id") or 0) == automation_id]
    orphan_subscriptions = [item for item in report.issues if item.get("kind") == "orphan_subscription_package"]
    if len(orphan_agent) != 1 or len(mismatches) != 1 or not orphan_subscriptions:
        raise RemediationError("authorized_issue_shape_invalid")

    agent_rows = _select_by_ids(session, "automation_agent_runtime_config", [automation_id], lock=lock)
    if len(agent_rows) != 1:
        raise RemediationError("authorized_automation_missing")
    agent = agent_rows[0]
    current_bound_key = str(agent.get("bound_package_key") or "").strip()
    target_package_key = audience_package_key_from_webhook_url(agent.get("send_webhook_url"))
    mismatch = mismatches[0]
    if (
        not target_package_key
        or current_bound_key != str(orphan_agent[0].get("package_key") or "").strip()
        or current_bound_key != str(mismatch.get("bound_package_key") or "").strip()
        or target_package_key != str(mismatch.get("send_package_key") or "").strip()
    ):
        raise RemediationError("authorized_automation_state_changed")

    target_package = (
        session.execute(
            text(
                """
            SELECT id, package_key, status
            FROM ai_audience_package
            WHERE package_key = :package_key
              AND status <> 'archived'
            LIMIT 1
            """
            ),
            {"package_key": target_package_key},
        )
        .mappings()
        .fetchone()
    )
    if not target_package:
        raise RemediationError("authorized_target_package_unavailable")

    subscription_ids = sorted(int(item.get("subscription_id") or 0) for item in orphan_subscriptions)
    subscriptions = _select_by_ids(session, "ai_audience_outbound_subscription", subscription_ids, lock=lock)
    expected_pairs = sorted((int(item.get("subscription_id") or 0), int(item.get("package_id") or 0)) for item in orphan_subscriptions)
    actual_pairs = sorted((int(item.get("id") or 0), int(item.get("package_id") or 0)) for item in subscriptions)
    if actual_pairs != expected_pairs:
        raise RemediationError("authorized_subscription_state_changed")

    package_ids = sorted({package_id for _subscription_id, package_id in expected_pairs})
    package_rows = _package_rows(session, package_ids, [current_bound_key, target_package_key])
    package_status_by_id = {int(item["id"]): str(item.get("status") or "") for item in package_rows}
    if any(package_status_by_id.get(package_id) not in {None, "archived"} for package_id in package_ids):
        raise RemediationError("orphan_subscription_parent_reactivated")

    return {
        "status": "ready",
        "report": report,
        "fingerprint": actual_fingerprint,
        "agent": agent,
        "target_package": dict(target_package),
        "target_package_key": target_package_key,
        "subscriptions": subscriptions,
        "package_rows": package_rows,
        "subscription_ids": subscription_ids,
    }


def _secure_backup(directory: Path, manifest: dict[str, Any], plan: dict[str, Any]) -> dict[str, str]:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = directory.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
        raise RemediationError("backup_directory_unsafe")
    os.chmod(directory, 0o700)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{manifest['operation_id']}-{timestamp}-{plan['fingerprint'][:12]}.json"
    path = directory / filename
    payload = {
        "schema_version": 1,
        "operation_id": manifest["operation_id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "issue_fingerprint": plan["fingerprint"],
        "precheck_report": plan["report"].to_dict(),
        "automation_row": plan["agent"],
        "subscription_rows": plan["subscriptions"],
        "package_rows": plan["package_rows"],
    }
    encoded = json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)
    return {"path": str(path), "sha256": hashlib.sha256(encoded).hexdigest()}


def _summary(
    manifest: dict[str, Any],
    *,
    status: str,
    issue_count: int,
    issue_counts: dict[str, int],
    fingerprint: str,
    database_write_executed: bool,
    backup: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "operation_id": manifest["operation_id"],
        "status": status,
        "issue_count": issue_count,
        "issue_kind_counts": issue_counts,
        "issue_fingerprint": fingerprint,
        "database_write_executed": database_write_executed,
        "real_external_call_executed": False,
        "backup_created": bool(backup),
        "backup_path": str((backup or {}).get("path") or ""),
        "backup_sha256": str((backup or {}).get("sha256") or ""),
    }


def run_remediation(
    manifest: dict[str, Any],
    *,
    mode: str,
    confirmation: str,
    backup_dir: str | Path,
    current_release_sha: str,
    session_factory: Callable[[], Session] | None = None,
) -> dict[str, Any]:
    if mode not in {"preview", "apply"}:
        raise RemediationError("mode_invalid")
    if current_release_sha != str(manifest["expected_production_sha"]):
        raise RemediationError("production_release_sha_changed")
    factory = session_factory or get_session_factory()
    if mode == "preview":
        with factory() as session:
            session.execute(text("SET TRANSACTION READ ONLY"))
            plan = _build_plan(session, manifest, lock=False)
            session.rollback()
        if plan["status"] == "already_applied":
            return _summary(
                manifest,
                status="already_applied",
                issue_count=0,
                issue_counts={},
                fingerprint="",
                database_write_executed=False,
            )
        report = plan["report"]
        return _summary(
            manifest,
            status="ready",
            issue_count=len(report.issues),
            issue_counts=issue_kind_counts(report.issues),
            fingerprint=plan["fingerprint"],
            database_write_executed=False,
        )

    if confirmation != str(manifest["execute_confirmation"]):
        raise RemediationError("execute_confirmation_invalid")
    with factory() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"))
        plan = _build_plan(session, manifest, lock=True)
        if plan["status"] == "already_applied":
            session.rollback()
            return _summary(
                manifest,
                status="already_applied",
                issue_count=0,
                issue_counts={},
                fingerprint="",
                database_write_executed=False,
            )
        backup = _secure_backup(Path(backup_dir), manifest, plan)
        session.execute(
            text(
                """
                UPDATE automation_agent_runtime_config
                SET bound_package_key = :package_key,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :automation_id
                """
            ),
            {
                "automation_id": int(manifest["automation_id"]),
                "package_key": plan["target_package_key"],
            },
        )
        parameters = {f"subscription_id_{index}": int(subscription_id) for index, subscription_id in enumerate(plan["subscription_ids"])}
        placeholders = ", ".join(f":{key}" for key in parameters)
        deleted_count = int(
            session.execute(
                text(f"DELETE FROM ai_audience_outbound_subscription WHERE id IN ({placeholders})"),
                parameters,
            ).rowcount
            or 0
        )
        if deleted_count != len(plan["subscription_ids"]):
            raise RemediationError("authorized_subscription_delete_count_changed")
        post_report = inspect_automation_bindings(session.connection())
        if not post_report.ok:
            raise RemediationError(
                "post_remediation_precheck_failed",
                details={"post_issue_count": len(post_report.issues)},
            )
        if not any(
            int(item.get("automation_id") or 0) == int(manifest["automation_id"]) and str(item.get("package_key") or "") == plan["target_package_key"]
            for item in post_report.bindings
        ):
            raise RemediationError("post_remediation_binding_missing")
        session.commit()
    report = plan["report"]
    return _summary(
        manifest,
        status="applied",
        issue_count=len(report.issues),
        issue_counts=issue_kind_counts(report.issues),
        fingerprint=plan["fingerprint"],
        database_write_executed=True,
        backup=backup,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair one authorized AI Audience binding history envelope")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--mode", choices=("preview", "apply"), required=True)
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--backup-dir", default="/home/ubuntu/.aicrm-remediation-backups")
    parser.add_argument("--current-release-sha", required=True)
    args = parser.parse_args()
    try:
        manifest = load_manifest(args.manifest)
        payload = run_remediation(
            manifest,
            mode=args.mode,
            confirmation=args.confirmation,
            backup_dir=args.backup_dir,
            current_release_sha=args.current_release_sha,
        )
    except RemediationError as exc:
        payload = {
            "ok": False,
            "error_code": exc.code,
            "real_external_call_executed": False,
            **exc.details,
        }
        print(
            json.dumps(
                redact_sensitive_data(payload),
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        )
        return 1
    print(
        json.dumps(
            redact_sensitive_data(payload),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
