#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import text

from aicrm_next.engagement.media_library.wecom_lease import build_wecom_media_lease_manager
from aicrm_next.insights.data_health.checks import _wecom_media_lease_health
from aicrm_next.integration_ports import build_wecom_media_upload_client
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


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if int(manifest.get("schema_version") or 0) != 1:
        raise RemediationError("manifest_schema_version_invalid")
    if not re.fullmatch(r"[a-z0-9_-]{8,80}", str(manifest.get("operation_id") or "")):
        raise RemediationError("manifest_operation_id_invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("expected_production_sha") or "")):
        raise RemediationError("manifest_production_sha_invalid")
    if int(manifest.get("expected_candidate_count") or 0) != 1:
        raise RemediationError("manifest_candidate_count_invalid")
    expected_evidence = manifest.get("expected_health_evidence")
    required_evidence = {
        "total_count",
        "ready_count",
        "refresh_due_count",
        "refreshing_count",
        "failed_count",
        "invalid_source_count",
        "canary_failed_count",
        "canary_invalid_source_count",
        "expired_count",
        "source_gap_count",
    }
    if not isinstance(expected_evidence, dict) or set(expected_evidence) != required_evidence:
        raise RemediationError("manifest_health_evidence_invalid")
    manifest["expected_health_evidence"] = {
        key: int(expected_evidence[key]) for key in sorted(required_evidence)
    }
    if not re.fullmatch(r"[A-Z0-9_]{16,120}", str(manifest.get("execute_confirmation") or "")):
        raise RemediationError("manifest_confirmation_invalid")
    return manifest


def _health_payload() -> dict[str, Any]:
    result = _wecom_media_lease_health()
    model_dump = getattr(result, "model_dump", None)
    return dict(model_dump() if callable(model_dump) else result.dict())


def _failed_candidates() -> list[dict[str, Any]]:
    with get_session_factory()() as session:
        rows = session.execute(
            text(
                """
                SELECT
                    lease.*,
                    CASE lease.material_kind
                        WHEN 'image' THEN EXISTS (
                            SELECT 1 FROM image_library material
                            WHERE material.id = lease.material_id
                              AND material.enabled IS TRUE
                              AND COALESCE(material.data_base64, '') <> ''
                        )
                        WHEN 'attachment' THEN EXISTS (
                            SELECT 1 FROM attachment_library material
                            WHERE material.id = lease.material_id
                              AND material.enabled IS TRUE
                              AND COALESCE(material.data_base64, '') <> ''
                        )
                        WHEN 'miniprogram' THEN EXISTS (
                            SELECT 1 FROM miniprogram_library material
                            WHERE material.id = lease.material_id
                              AND material.enabled IS TRUE
                              AND material.thumb_image_id IS NULL
                              AND COALESCE(material.thumb_image_base64, '') <> ''
                        )
                        ELSE FALSE
                    END AS durable_source_available
                FROM wecom_media_leases lease
                WHERE lease.tenant_id = 'aicrm'
                  AND lease.status = 'failed'
                ORDER BY lease.tenant_id, lease.corp_id,
                         lease.material_kind, lease.material_id, lease.upload_kind
                """
            )
        ).mappings()
        return [dict(row) for row in rows]


class _TrackingUploader:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.call_count = 0

    def upload_image(self, file_name: str, payload: bytes, content_type: str) -> Any:
        self.call_count += 1
        return self._delegate.upload_image(file_name, payload, content_type)

    def upload_attachment(self, file_name: str, payload: bytes, content_type: str) -> Any:
        self.call_count += 1
        return self._delegate.upload_attachment(file_name, payload, content_type)


def _manager_bundle() -> tuple[Any, _TrackingUploader]:
    tracker = _TrackingUploader(build_wecom_media_upload_client())
    return build_wecom_media_lease_manager(uploader=tracker), tracker


def _secure_backup(backup_dir: Path, manifest: dict[str, Any], candidate: dict[str, Any]) -> dict[str, str]:
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backup_dir, 0o700)
    path = backup_dir / f"{manifest['operation_id']}-{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
    payload = json.dumps(
        {
            "operation_id": manifest["operation_id"],
            "expected_production_sha": manifest["expected_production_sha"],
            "lease_row": _jsonable(candidate),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.write("\n")
    os.chmod(path, 0o600)
    return {
        "path": str(path),
        "sha256": hashlib.sha256((payload + "\n").encode("utf-8")).hexdigest(),
    }


def _summary(
    manifest: dict[str, Any],
    *,
    status: str,
    candidate_count: int,
    durable_source_available_count: int,
    database_write_executed: bool,
    real_external_call_executed: bool,
    backup: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "operation_id": manifest["operation_id"],
        "status": status,
        "candidate_count": candidate_count,
        "durable_source_available_count": durable_source_available_count,
        "database_write_executed": database_write_executed,
        "real_external_call_executed": real_external_call_executed,
        "backup_created": bool(backup),
        "backup_path": str((backup or {}).get("path") or ""),
        "backup_sha256": str((backup or {}).get("sha256") or ""),
        "contains_raw_material_identifier": False,
    }


def run_remediation(
    manifest: dict[str, Any],
    *,
    mode: str,
    confirmation: str,
    backup_dir: str | Path,
    current_release_sha: str,
    health_loader: Callable[[], dict[str, Any]] = _health_payload,
    candidate_loader: Callable[[], list[dict[str, Any]]] = _failed_candidates,
    manager_bundle_factory: Callable[[], tuple[Any, Any]] = _manager_bundle,
) -> dict[str, Any]:
    if mode not in {"preview", "apply"}:
        raise RemediationError("mode_invalid")
    if current_release_sha != str(manifest["expected_production_sha"]):
        raise RemediationError("production_release_sha_changed")

    health = health_loader()
    evidence = {key: int(value or 0) for key, value in dict(health.get("evidence") or {}).items()}
    candidates = candidate_loader()
    if (
        health.get("status") == "ok"
        and not candidates
        and evidence.get("failed_count", 0) == 0
        and evidence.get("invalid_source_count", 0) == 0
        and evidence.get("source_gap_count", 0) == 0
    ):
        return _summary(
            manifest,
            status="already_applied",
            candidate_count=0,
            durable_source_available_count=0,
            database_write_executed=False,
            real_external_call_executed=False,
        )

    if health.get("status") != "warn" or evidence != manifest["expected_health_evidence"]:
        raise RemediationError("unexpected_health_envelope")
    if len(candidates) != int(manifest["expected_candidate_count"]):
        raise RemediationError("unexpected_candidate_count")
    available_count = sum(1 for candidate in candidates if candidate.get("durable_source_available") is True)
    if available_count != len(candidates):
        raise RemediationError("candidate_durable_source_unavailable")
    if mode == "preview":
        return _summary(
            manifest,
            status="ready",
            candidate_count=len(candidates),
            durable_source_available_count=available_count,
            database_write_executed=False,
            real_external_call_executed=False,
        )
    if confirmation != str(manifest["execute_confirmation"]):
        raise RemediationError("execute_confirmation_invalid")

    candidate = candidates[0]
    backup = _secure_backup(Path(backup_dir), manifest, candidate)
    manager, tracker = manager_bundle_factory()
    try:
        result = manager.ensure_ready(
            str(candidate["material_kind"]),
            int(candidate["material_id"]),
            upload_kind=str(candidate["upload_kind"]),
            force_refresh=True,
        )
    except Exception as exc:
        raise RemediationError(
            "media_lease_refresh_failed",
            details={"real_external_call_executed": bool(getattr(tracker, "call_count", 0))},
        ) from exc
    if int(getattr(tracker, "call_count", 0)) != 1:
        raise RemediationError(
            "unexpected_provider_call_count",
            details={"real_external_call_executed": bool(getattr(tracker, "call_count", 0))},
        )
    if str(result.get("status") or "") != "ready" or not str(result.get("media_id") or ""):
        raise RemediationError("media_lease_not_ready_after_refresh", details={"real_external_call_executed": True})

    post_health = health_loader()
    post_evidence = dict(post_health.get("evidence") or {})
    post_candidates = candidate_loader()
    if (
        post_health.get("status") != "ok"
        or post_candidates
        or int(post_evidence.get("failed_count") or 0) != 0
        or int(post_evidence.get("invalid_source_count") or 0) != 0
        or int(post_evidence.get("source_gap_count") or 0) != 0
    ):
        raise RemediationError("post_remediation_health_not_green", details={"real_external_call_executed": True})
    return _summary(
        manifest,
        status="applied",
        candidate_count=1,
        durable_source_available_count=1,
        database_write_executed=True,
        real_external_call_executed=True,
        backup=backup,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair one authorized failed WeCom temporary media lease")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--mode", choices=("preview", "apply"), required=True)
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--backup-dir", default="/home/ubuntu/.aicrm-remediation-backups")
    parser.add_argument("--current-release-sha", required=True)
    args = parser.parse_args()
    try:
        payload = run_remediation(
            load_manifest(args.manifest),
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
            "contains_raw_material_identifier": False,
        }
        print(json.dumps(redact_sensitive_data(payload), ensure_ascii=False, sort_keys=True, default=str))
        return 1
    print(json.dumps(redact_sensitive_data(payload), ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
