from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import secrets
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from xml.etree import ElementTree as ET

from aicrm_next.integration_ports import (
    WeComApiError,
    ProductionWeComAdapter,
    missing_wecom_config,
)
from aicrm_next.platform.platform_foundation.internal_events.shadow import (
    emit_owner_migration_executed_shadow_event,
    safe_emit,
)
from aicrm_next.platform.shared.runtime import production_data_ready

from .repo import FixtureOwnerMigrationRepository, PostgresOwnerMigrationRepository


TEMPLATE_HEADERS = ["external_userid", "是否迁移", "当前负责人userid", "客户备注名", "备注"]
DEFAULT_TRANSFER_WELCOME_MSG = "您好，后续将由新的服务同事继续为您服务。"
PREVIEW_TTL_MINUTES = 30
MOVE_YES_VALUES = {"", "是", "y", "yes", "true", "1", "迁移"}
MOVE_NO_VALUES = {"否", "n", "no", "false", "0", "不迁移"}
MOVE_FLAG_ALIASES = {
    "yes": "是",
    "y": "是",
    "true": "是",
    "1": "是",
    "迁移": "是",
    "": "是",
    "no": "否",
    "n": "否",
    "false": "否",
    "0": "否",
    "不迁移": "否",
}


def clean_text(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class OwnerMigrationCommand:
    source_owner_userid: str
    target_owner_userid: str
    operator: str = ""
    transfer_success_msg: str = ""
    batch_size: int = 100
    perform_wecom_transfer: bool = True
    execute: bool = False
    confirm: bool = False
    scope_type: str | None = None
    session_id: str = ""
    preview_token: str = ""
    preview_hash: str = ""
    confirm_phrase: str = ""


class OwnerMigrationRepository(Protocol):
    source_status: str

    def preview_owner_migration(
        self,
        *,
        source_owner_userid: str,
        target_owner_userid: str,
        external_userids: list[str] | None = None,
    ) -> dict[str, Any]: ...

    def execute_owner_migration(
        self,
        *,
        source_owner_userid: str,
        target_owner_userid: str,
        operator: str,
        external_userids: list[str] | None = None,
        target_owner_display_name: str | None = None,
    ) -> dict[str, Any]: ...

    def resolve_operation_members(self, userids: list[str]) -> dict[str, dict[str, Any]]: ...
    def lookup_customer_owners(self, external_userids: list[str]) -> dict[str, dict[str, Any]]: ...
    def save_import_session(self, session: dict[str, Any]) -> None: ...
    def get_import_session(self, session_id: str) -> dict[str, Any] | None: ...
    def save_preview(self, preview: dict[str, Any]) -> None: ...
    def get_preview(self, preview_token: str) -> dict[str, Any] | None: ...
    def get_latest_preview_by_session(self, session_id: str) -> dict[str, Any] | None: ...
    def mark_preview_executed(self, preview_token: str, result_id: str) -> None: ...
    def save_result(self, result: dict[str, Any]) -> None: ...
    def get_result(self, result_id: str) -> dict[str, Any] | None: ...
    def audit_owner_migration_event(self, event_type: str, payload: dict[str, Any]) -> None: ...


class OwnerMigrationService:
    def __init__(self, repo: OwnerMigrationRepository) -> None:
        self._repo = repo

    def run(self, command: OwnerMigrationCommand) -> dict[str, Any]:
        if _uses_scoped_flow(command):
            if command.execute:
                return self.execute_scoped(command)
            return self.preview_scoped(command)
        return self._run_legacy(command)

    def import_file(
        self,
        *,
        filename: str,
        content: bytes,
        source_owner_userid: str,
        target_owner_userid: str,
        include_wecom_transfer: bool = True,
        transfer_welcome_msg: str = "",
        operator: str = "",
    ) -> dict[str, Any]:
        source = clean_text(source_owner_userid)
        target = clean_text(target_owner_userid)
        operator = clean_text(operator) or "crm_console"
        validation = self._validate_owners(source, target)
        if validation is not None:
            return validation
        if not content:
            return _error("empty_file", "Uploaded file is empty")
        parsed = parse_owner_migration_file(filename=filename, content=content)
        if not parsed.get("ok"):
            return parsed
        rows = _normalize_import_rows(parsed["rows"], source_owner_userid=source)
        row_stats = _import_row_stats(rows)
        session_id = _new_id("oms")
        file_hash = hashlib.sha256(content).hexdigest()
        session = {
            "session_id": session_id,
            "file_name": filename,
            "file_hash": file_hash,
            "source_owner_userid": source,
            "target_owner_userid": target,
            "include_wecom_transfer": bool(include_wecom_transfer),
            "transfer_welcome_msg": clean_text(transfer_welcome_msg) or DEFAULT_TRANSFER_WELCOME_MSG,
            "rows": rows,
            "row_stats": row_stats,
            "operator": operator,
            "created_at": _now_iso(),
        }
        self._repo.save_import_session(session)
        self._repo.audit_owner_migration_event("owner_migration_import", session)
        return {"ok": True, "session_id": session_id, "file_hash": file_hash, "row_stats": row_stats, "rows": rows}

    def preview_scoped(self, command: OwnerMigrationCommand) -> dict[str, Any]:
        source = clean_text(command.source_owner_userid)
        target = clean_text(command.target_owner_userid)
        scope_type = _scope_type(command.scope_type)
        operator = clean_text(command.operator) or "crm_console"
        validation = self._validate_owners(source, target)
        if validation is not None:
            return validation
        include_wecom_transfer = bool(command.perform_wecom_transfer)
        transfer_welcome_msg = clean_text(command.transfer_success_msg) or DEFAULT_TRANSFER_WELCOME_MSG
        computed = self._compute_preview(
            scope_type=scope_type,
            source_owner_userid=source,
            target_owner_userid=target,
            session_id=clean_text(command.session_id),
            include_wecom_transfer=include_wecom_transfer,
            transfer_welcome_msg=transfer_welcome_msg,
            operator=operator,
        )
        if not computed.get("ok"):
            return computed
        preview_token = _new_id("omp")
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=PREVIEW_TTL_MINUTES)
        preview = {**computed, "preview_token": preview_token, "created_at": _now_iso(), "expires_at": expires_at.isoformat(), "operator": operator, "executed_result_id": ""}
        self._repo.save_preview(preview)
        self._repo.audit_owner_migration_event("owner_migration_preview", preview)
        return preview

    def execute_scoped(self, command: OwnerMigrationCommand) -> dict[str, Any]:
        source = clean_text(command.source_owner_userid)
        target = clean_text(command.target_owner_userid)
        scope_type = _scope_type(command.scope_type)
        operator = clean_text(command.operator) or "crm_console"
        if not command.confirm:
            return _error("confirm_required", "confirm is required before executing owner migration")
        validation = self._validate_owners(source, target)
        if validation is not None:
            return validation
        preview_token = clean_text(command.preview_token)
        if not preview_token:
            return _error("preview_token_required", "preview_token is required before executing owner migration")
        requested_hash = clean_text(command.preview_hash)
        if not requested_hash:
            return _error("preview_hash_required", "preview_hash is required before executing owner migration")
        stored_preview = self._repo.get_preview(preview_token)
        if not stored_preview:
            return _error("preview_token_not_found", "preview_token is not found or has expired")
        if clean_text(stored_preview.get("executed_result_id")):
            return _error("preview_token_already_executed", "preview_token has already been executed", details={"result_id": clean_text(stored_preview.get("executed_result_id"))})
        if _is_expired(clean_text(stored_preview.get("expires_at"))):
            return _error("preview_token_expired", "preview_token has expired; please preview again")
        include_wecom_transfer = bool(command.perform_wecom_transfer)
        transfer_welcome_msg = clean_text(command.transfer_success_msg) or DEFAULT_TRANSFER_WELCOME_MSG
        mismatches = _preview_parameter_mismatches(
            stored_preview,
            {
                "scope_type": scope_type,
                "source_owner_userid": source,
                "target_owner_userid": target,
                "session_id": clean_text(command.session_id),
                "include_wecom_transfer": include_wecom_transfer,
                "transfer_welcome_msg": transfer_welcome_msg,
            },
        )
        if mismatches:
            return _error("preview_parameters_changed", "Parameters changed after preview; please preview again", details={"fields": mismatches})
        recomputed = self._compute_preview(
            scope_type=scope_type,
            source_owner_userid=source,
            target_owner_userid=target,
            session_id=clean_text(command.session_id),
            include_wecom_transfer=include_wecom_transfer,
            transfer_welcome_msg=transfer_welcome_msg,
            operator=operator,
        )
        if not recomputed.get("ok"):
            return recomputed
        if requested_hash != clean_text(stored_preview.get("preview_hash")) or requested_hash != clean_text(recomputed.get("preview_hash")):
            return _error("preview_hash_mismatch", "preview_hash does not match current migration scope; please preview again")
        confirm_phrase = clean_text(stored_preview.get("confirm_phrase"))
        if clean_text(command.confirm_phrase) != confirm_phrase:
            return _error("confirm_phrase_mismatch", "confirm_phrase does not match the server generated confirmation phrase")
        eligible_external_userids = [clean_text(item) for item in recomputed.get("eligible_external_userids", []) if clean_text(item)]
        if not eligible_external_userids:
            return _error("no_ready_rows", "No ready customers are available for execution")
        result_id = _new_id("omr")
        job_id = _new_id("omj")
        transfer = _transfer_customers(
            source_owner_userid=source,
            target_owner_userid=target,
            external_userids=eligible_external_userids,
            transfer_success_msg=transfer_welcome_msg,
            batch_size=max(1, min(int(command.batch_size or 100), 100)),
            enabled=include_wecom_transfer,
        )
        if not transfer.get("ok"):
            result = self._build_execute_result(result_id=result_id, job_id=job_id, recomputed=recomputed, stored_preview=stored_preview, operator=operator, transfer=transfer, crm_result=None)
            self._repo.save_result(result)
            self._repo.audit_owner_migration_event("owner_migration_execute_failed", result)
            return {**_error(transfer.get("error_code") or "wecom_transfer_failed", transfer.get("error") or "WeCom transfer failed", status_code=502), "result_id": result_id, "job_id": job_id, "wecom_transfer": transfer}
        success_external_userids = [clean_text(item) for item in transfer.get("success_external_userids", []) if clean_text(item)]
        crm_scope = success_external_userids if include_wecom_transfer else eligible_external_userids
        crm_result = self._repo.execute_owner_migration(
            source_owner_userid=source,
            target_owner_userid=target,
            operator=operator,
            external_userids=crm_scope,
            target_owner_display_name=clean_text(recomputed.get("target_owner_display_name")) or target,
        )
        result = self._build_execute_result(result_id=result_id, job_id=job_id, recomputed=recomputed, stored_preview=stored_preview, operator=operator, transfer=transfer, crm_result=crm_result)
        self._repo.save_result(result)
        self._repo.mark_preview_executed(preview_token, result_id)
        self._repo.audit_owner_migration_event("owner_migration_execute", result)
        internal_event = safe_emit(
            "owner_migration.executed",
            emit_owner_migration_executed_shadow_event,
            command=command,
            result=result,
        )
        result["internal_event_id"] = internal_event.get("event_id") or ""
        result["internal_event_status"] = internal_event.get("status") or ""
        result["internal_event_reason"] = internal_event.get("reason") or ""
        result["internal_event_error"] = internal_event.get("error") or ""
        result["internal_event_consumer_run_count"] = int(internal_event.get("consumer_run_count") or 0)
        return result

    def export_session_errors(self, session_id: str) -> dict[str, Any]:
        session = self._repo.get_import_session(clean_text(session_id))
        if not session:
            return _error("session_not_found", "session_id is not found")
        preview = self._repo.get_latest_preview_by_session(clean_text(session_id)) or {}
        rows = preview.get("rows") or []
        if not rows:
            rows = [
                {
                    "row_number": row.get("row_number"),
                    "external_userid": row.get("external_userid"),
                    "customer_name": row.get("customer_name_in_file"),
                    "move_flag": row.get("move_flag"),
                    "current_owner_userid": row.get("current_owner_userid_in_file"),
                    "status": row.get("parse_status"),
                    "reason": row.get("parse_reason"),
                    "note": row.get("note"),
                }
                for row in session.get("rows", [])
                if row.get("parse_status") != "parsed"
            ]
        error_rows = [row for row in rows if row.get("status") not in {"ready", "skipped_by_file"} or row.get("parse_status") not in {None, "parsed"}]
        body = build_xlsx(
            ["row_number", "external_userid", "customer_name", "move_flag", "current_owner_userid", "status", "reason", "note"],
            [
                [
                    row.get("row_number", ""),
                    row.get("external_userid", ""),
                    row.get("customer_name") or row.get("customer_name_in_file") or "",
                    row.get("move_flag", ""),
                    row.get("current_owner_userid") or row.get("current_owner_userid_in_file") or "",
                    row.get("status") or row.get("parse_status") or "",
                    row.get("reason") or row.get("parse_reason") or "",
                    row.get("note", ""),
                ]
                for row in error_rows
            ],
            sheet_name="owner_migration_errors",
        )
        return {"ok": True, "content": body, "filename": f"owner_migration_errors_{session_id}.xlsx"}

    def export_result(self, result_id: str) -> dict[str, Any]:
        result = self._repo.get_result(clean_text(result_id))
        if not result:
            return _error("result_not_found", "result_id is not found")
        body = build_xlsx(
            ["external_userid", "wecom_status", "crm_status", "reason", "source_owner_userid", "target_owner_userid", "executed_at", "operator"],
            [
                [
                    row.get("external_userid", ""),
                    row.get("wecom_status", ""),
                    row.get("crm_status", ""),
                    row.get("reason", ""),
                    result.get("source_owner_userid", ""),
                    result.get("target_owner_userid", ""),
                    result.get("executed_at", ""),
                    result.get("operator", ""),
                ]
                for row in result.get("rows", [])
            ],
            sheet_name="owner_migration_result",
        )
        return {"ok": True, "content": body, "filename": f"owner_migration_result_{result_id}.xlsx"}

    def _run_legacy(self, command: OwnerMigrationCommand) -> dict[str, Any]:
        source = clean_text(command.source_owner_userid)
        target = clean_text(command.target_owner_userid)
        operator = clean_text(command.operator) or "crm_console"
        if not source:
            return _error("source_owner_userid_required", "source_owner_userid is required")
        if not target:
            return _error("target_owner_userid_required", "target_owner_userid is required")
        if source == target:
            return _error("same_owner_userid", "source and target owner_userid must be different")
        if command.execute:
            if not command.confirm:
                return _error("confirm_required", "confirm is required before executing owner migration")
            preview = self._repo.preview_owner_migration(source_owner_userid=source, target_owner_userid=target)
            candidates = [clean_text(item) for item in preview.get("all_external_userids", []) if clean_text(item)]
            transfer = _transfer_customers(
                source_owner_userid=source,
                target_owner_userid=target,
                external_userids=candidates,
                transfer_success_msg=clean_text(command.transfer_success_msg),
                batch_size=max(1, min(int(command.batch_size or 100), 100)),
                enabled=bool(command.perform_wecom_transfer),
            )
            if not transfer.get("ok"):
                return {
                    "ok": False,
                    "mode": "execute",
                    "source_owner_userid": source,
                    "target_owner_userid": target,
                    "operator": operator,
                    **preview,
                    "wecom_transfer": transfer,
                    "error_code": transfer.get("error_code") or "wecom_transfer_failed",
                    "error": transfer.get("error") or "WeCom transfer failed",
                    "message": transfer.get("error") or "WeCom transfer failed",
                    "details": {"wecom_transfer": transfer},
                    "status_code": 502,
                }
            success_external_userids = list(transfer.get("success_external_userids") or [])
            result = self._repo.execute_owner_migration(
                source_owner_userid=source,
                target_owner_userid=target,
                operator=operator,
                external_userids=success_external_userids if command.perform_wecom_transfer else None,
                target_owner_display_name=target,
            )
            result["wecom_transfer"] = transfer
        else:
            result = self._repo.preview_owner_migration(source_owner_userid=source, target_owner_userid=target)
        payload = {"ok": True, "mode": "execute" if command.execute else "preview", "source_owner_userid": source, "target_owner_userid": target, "operator": operator, "wecom_diagnostics": _wecom_transfer_diagnostics(), **result}
        if command.execute:
            legacy_event_key = hashlib.sha256(f"{source}:{target}:{operator}".encode("utf-8")).hexdigest()[:16]
            legacy_result_id = f"legacy:{legacy_event_key}"
            event_result = {
                **payload,
                "result_id": clean_text(payload.get("result_id")) or legacy_result_id,
                "job_id": clean_text(payload.get("job_id")) or legacy_result_id,
            }
            internal_event = safe_emit(
                "owner_migration.executed",
                emit_owner_migration_executed_shadow_event,
                command=command,
                result=event_result,
            )
            payload["internal_event_id"] = internal_event.get("event_id") or ""
            payload["internal_event_status"] = internal_event.get("status") or ""
            payload["internal_event_reason"] = internal_event.get("reason") or ""
            payload["internal_event_error"] = internal_event.get("error") or ""
            payload["internal_event_consumer_run_count"] = int(internal_event.get("consumer_run_count") or 0)
        return payload

    def _validate_owners(self, source: str, target: str) -> dict[str, Any] | None:
        if not source:
            return _error("source_owner_userid_required", "source_owner_userid is required")
        if not target:
            return _error("target_owner_userid_required", "target_owner_userid is required")
        if source == target:
            return _error("same_owner_userid", "source and target owner_userid must be different")
        members = self._repo.resolve_operation_members([source, target])
        source_member = members.get(source)
        target_member = members.get(target)
        if not source_member:
            return _error("source_owner_not_found", "source_owner_userid is not a valid operation member", details={"source_owner_userid": source})
        if not target_member:
            return _error("target_owner_not_found", "target_owner_userid is not a valid operation member", details={"target_owner_userid": target})
        if clean_text(target_member.get("status")) != "active":
            return _error("target_owner_inactive", "target_owner_userid is inactive and cannot be used as target owner", details={"target_owner_userid": target})
        return None

    def _compute_preview(
        self,
        *,
        scope_type: str,
        source_owner_userid: str,
        target_owner_userid: str,
        session_id: str,
        include_wecom_transfer: bool,
        transfer_welcome_msg: str,
        operator: str,
    ) -> dict[str, Any]:
        members = self._repo.resolve_operation_members([source_owner_userid, target_owner_userid])
        source_display = clean_text((members.get(source_owner_userid) or {}).get("display_name")) or source_owner_userid
        target_display = clean_text((members.get(target_owner_userid) or {}).get("display_name")) or target_owner_userid
        session: dict[str, Any] | None = None
        requested_external_userids: list[str] | None = None
        if scope_type == "excel_include":
            if not session_id:
                return _error("session_id_required", "session_id is required when scope_type=excel_include")
            session = self._repo.get_import_session(session_id)
            if not session:
                return _error("session_not_found", "session_id is not found")
            mismatches = _preview_parameter_mismatches(
                session,
                {
                    "source_owner_userid": source_owner_userid,
                    "target_owner_userid": target_owner_userid,
                    "include_wecom_transfer": include_wecom_transfer,
                    "transfer_welcome_msg": transfer_welcome_msg,
                },
            )
            if mismatches:
                return _error("session_parameters_changed", "Parameters changed since import; please upload the file again", details={"fields": mismatches})
            requested_external_userids = _excel_requested_external_userids(session.get("rows", []))
        elif scope_type != "all":
            return _error("unsupported_scope_type", "scope_type must be all or excel_include", details={"scope_type": scope_type})
        base_preview = self._repo.preview_owner_migration(source_owner_userid=source_owner_userid, target_owner_userid=target_owner_userid, external_userids=requested_external_userids)
        source_candidates = {clean_text(item) for item in base_preview.get("all_external_userids", []) if clean_text(item)}
        if scope_type == "all":
            eligible_external_userids = sorted(source_candidates)
            rows = [
                {
                    "row_number": index + 1,
                    "external_userid": external_userid,
                    "customer_name": "",
                    "move_flag": "是",
                    "current_owner_userid": source_owner_userid,
                    "status": "ready",
                    "can_execute": True,
                    "reason": "-",
                }
                for index, external_userid in enumerate(eligible_external_userids)
            ]
            row_stats = _preview_row_stats(rows, total_rows=len(rows), unique_external_userids=len(rows))
            file_hash = ""
        else:
            assert session is not None
            owner_index = self._repo.lookup_customer_owners([row["external_userid"] for row in session.get("rows", []) if clean_text(row.get("external_userid"))])
            rows = _preview_rows_from_session(session=session, source_owner_userid=source_owner_userid, target_owner_userid=target_owner_userid, source_candidates=source_candidates, owner_index=owner_index)
            eligible_external_userids = [row["external_userid"] for row in rows if row.get("can_execute")]
            row_stats = _preview_row_stats(rows, total_rows=int((session.get("row_stats") or {}).get("total_rows") or 0), unique_external_userids=int((session.get("row_stats") or {}).get("unique_external_userids") or 0))
            file_hash = clean_text(session.get("file_hash"))
        preview_hash = _preview_hash(
            {
                "scope_type": scope_type,
                "source_owner_userid": source_owner_userid,
                "target_owner_userid": target_owner_userid,
                "session_id": session_id if scope_type == "excel_include" else "",
                "file_hash": file_hash,
                "include_wecom_transfer": include_wecom_transfer,
                "transfer_welcome_msg": transfer_welcome_msg,
                "eligible_external_userids": eligible_external_userids,
            }
        )
        ready_count = len(eligible_external_userids)
        return {
            "ok": True,
            "mode": "preview",
            "scope_type": scope_type,
            "source_status": self._repo.source_status,
            "source_owner_userid": source_owner_userid,
            "target_owner_userid": target_owner_userid,
            "source_owner_display_name": source_display,
            "target_owner_display_name": target_display,
            "session_id": session_id if scope_type == "excel_include" else "",
            "file_hash": file_hash,
            "include_wecom_transfer": include_wecom_transfer,
            "transfer_welcome_msg": transfer_welcome_msg,
            "preview_hash": preview_hash,
            "confirm_phrase": f"确认将 {ready_count} 个客户从 {source_owner_userid} 迁移到 {target_owner_userid}",
            "candidate_count": ready_count,
            "eligible_external_userids": eligible_external_userids,
            "all_external_userids": eligible_external_userids,
            "sample_external_userids": eligible_external_userids[:20],
            "row_stats": row_stats,
            "surface_counts": base_preview.get("surface_counts", {}),
            "pending_review": _normalize_pending_review(base_preview.get("pending_review", {})),
            "rows": rows,
            "operator": operator,
            "notes": base_preview.get("notes", []),
        }

    def _build_execute_result(
        self,
        *,
        result_id: str,
        job_id: str,
        recomputed: dict[str, Any],
        stored_preview: dict[str, Any],
        operator: str,
        transfer: dict[str, Any],
        crm_result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        eligible_external_userids = [clean_text(item) for item in recomputed.get("eligible_external_userids", []) if clean_text(item)]
        failed_by_external = {
            clean_text(row.get("external_userid")): clean_text(row.get("errmsg")) or str(row.get("errcode") or "wecom_failed")
            for row in transfer.get("failed_customers", [])
            if clean_text(row.get("external_userid"))
        }
        success_external_userids = set(transfer.get("success_external_userids") or [])
        touched_external_userids = set((crm_result or {}).get("touched_external_userids") or (crm_result or {}).get("sample_external_userids") or [])
        include_wecom_transfer = bool(recomputed.get("include_wecom_transfer"))
        rows: list[dict[str, Any]] = []
        for external_userid in eligible_external_userids:
            if include_wecom_transfer:
                if external_userid in success_external_userids:
                    wecom_status = "success"
                    reason = "-"
                else:
                    wecom_status = "failed"
                    reason = failed_by_external.get(external_userid) or "wecom_transfer_failed"
            else:
                wecom_status = "skipped"
                reason = "local_only"
            crm_status = "updated" if external_userid in touched_external_userids else "skipped"
            if include_wecom_transfer and wecom_status != "success":
                crm_status = "skipped"
            rows.append({"external_userid": external_userid, "wecom_status": wecom_status, "crm_status": crm_status, "reason": reason})
        wecom_success = len([row for row in rows if row["wecom_status"] == "success"])
        wecom_failed = len([row for row in rows if row["wecom_status"] == "failed"])
        crm_updated = len([row for row in rows if row["crm_status"] == "updated"])
        executed_at = _now_iso()
        return {
            "ok": True,
            "mode": "local_only" if not include_wecom_transfer else "wecom_then_crm",
            "scope_type": recomputed.get("scope_type"),
            "source_owner_userid": recomputed.get("source_owner_userid"),
            "target_owner_userid": recomputed.get("target_owner_userid"),
            "source_owner_display_name": recomputed.get("source_owner_display_name"),
            "target_owner_display_name": recomputed.get("target_owner_display_name"),
            "session_id": recomputed.get("session_id", ""),
            "file_hash": recomputed.get("file_hash", ""),
            "preview_token": clean_text(stored_preview.get("preview_token")),
            "preview_hash": recomputed.get("preview_hash"),
            "requested_external_userids": len(eligible_external_userids),
            "wecom_requested": len(eligible_external_userids) if include_wecom_transfer else 0,
            "wecom_success": wecom_success,
            "wecom_failed": wecom_failed,
            "crm_updated": crm_updated,
            "crm_skipped_due_to_wecom_failure": wecom_failed if include_wecom_transfer else 0,
            "result_id": result_id,
            "job_id": job_id,
            "operator": operator,
            "include_wecom_transfer": include_wecom_transfer,
            "transfer_welcome_msg": recomputed.get("transfer_welcome_msg", ""),
            "total_rows": int((recomputed.get("row_stats") or {}).get("total_rows") or len(eligible_external_userids)),
            "eligible_count": len(eligible_external_userids),
            "pending_review_snapshot": recomputed.get("pending_review", {}),
            "wecom_transfer": transfer,
            "crm_result": crm_result or {},
            "rows": rows,
            "created_at": executed_at,
            "executed_at": executed_at,
        }


def _error(code: str, message: str, *, status_code: int = 400, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": False, "error_code": code, "message": message, "error": message, "details": details or {}, "status_code": status_code}


def build_owner_migration_service() -> OwnerMigrationService:
    repo: OwnerMigrationRepository
    if production_data_ready():
        repo = PostgresOwnerMigrationRepository()
    else:
        repo = FixtureOwnerMigrationRepository()
    return OwnerMigrationService(repo)


def owner_migration_template_xlsx() -> bytes:
    return build_xlsx(TEMPLATE_HEADERS, [], sheet_name="owner_migration")


def parse_owner_migration_file(*, filename: str, content: bytes) -> dict[str, Any]:
    suffix = clean_text(filename).lower().rsplit(".", 1)[-1] if "." in clean_text(filename) else ""
    try:
        if suffix == "xlsx":
            rows = _parse_xlsx_rows(content)
        elif suffix == "csv":
            rows = _parse_csv_rows(content)
        elif suffix == "xls":
            if content.startswith(b"PK"):
                rows = _parse_xlsx_rows(content)
            else:
                rows = _parse_csv_rows(content)
        else:
            return _error("unsupported_file_type", "Only xlsx, xls, and csv uploads are accepted")
    except zipfile.BadZipFile:
        return _error("unsupported_file_type", "Uploaded xlsx file is not a valid Excel workbook")
    except UnicodeDecodeError:
        return _error("unsupported_file_type", "Uploaded csv file must be UTF-8 encoded")
    except ValueError as exc:
        return _error(clean_text(getattr(exc, "args", ["invalid_file"])[0]) or "invalid_file", clean_text(exc) or "Invalid upload file")
    if not rows:
        return _error("empty_file", "Uploaded file has no data rows")
    return {"ok": True, "rows": rows}


def build_xlsx(headers: list[str], rows: list[list[Any]], *, sheet_name: str) -> bytes:
    def esc(value: Any) -> str:
        return str(value if value is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    def col_name(index: int) -> str:
        name = ""
        index += 1
        while index:
            index, rem = divmod(index - 1, 26)
            name = chr(65 + rem) + name
        return name

    all_rows = [headers, *rows]
    sheet_rows: list[str] = []
    for row_index, row in enumerate(all_rows, start=1):
        cells = []
        for col_index, value in enumerate(row):
            cells.append(f'<c r="{col_name(col_index)}{row_index}" t="inlineStr"><is><t>{esc(value)}</t></is></c>')
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    sheet_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'''
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>''')
        zf.writestr("_rels/.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>''')
        zf.writestr("xl/workbook.xml", f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="{esc(sheet_name)}" sheetId="1" r:id="rId1"/></sheets></workbook>''')
        zf.writestr("xl/_rels/workbook.xml.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>''')
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return out.getvalue()


def _parse_csv_rows(content: bytes) -> list[dict[str, Any]]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("empty_file")
    headers = [clean_text(item) for item in reader.fieldnames]
    if "external_userid" not in headers:
        raise ValueError("missing_required_column")
    rows: list[dict[str, Any]] = []
    for row_index, row in enumerate(reader, start=2):
        rows.append({"row_number": row_index, **{clean_text(key): value for key, value in row.items() if key is not None}})
    return rows


def _parse_xlsx_rows(content: bytes) -> list[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in zf.namelist():
            raise ValueError("empty_file")
        shared_strings = _read_shared_strings(zf)
        root = ET.fromstring(zf.read(sheet_name))
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    parsed_rows: list[tuple[int, list[str]]] = []
    for row_el in root.findall(f".//{ns}row"):
        row_number = int(row_el.attrib.get("r") or len(parsed_rows) + 1)
        values: dict[int, str] = {}
        for cell in row_el.findall(f"{ns}c"):
            ref = cell.attrib.get("r", "")
            values[_excel_col_index(ref)] = _cell_text(cell, shared_strings)
        max_col = max(values.keys(), default=-1)
        parsed_rows.append((row_number, [values.get(i, "") for i in range(max_col + 1)]))
    while parsed_rows and not any(clean_text(cell) for cell in parsed_rows[0][1]):
        parsed_rows.pop(0)
    if not parsed_rows:
        raise ValueError("empty_file")
    _header_number, headers = parsed_rows[0]
    clean_headers = [clean_text(header) for header in headers]
    if "external_userid" not in clean_headers:
        raise ValueError("missing_required_column")
    rows: list[dict[str, Any]] = []
    for row_number, values in parsed_rows[1:]:
        if not any(clean_text(value) for value in values):
            continue
        row = {"row_number": row_number}
        for index, header in enumerate(clean_headers):
            if header:
                row[header] = values[index] if index < len(values) else ""
        rows.append(row)
    if not rows:
        raise ValueError("empty_file")
    return rows


def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for si in root.findall(f"{ns}si"):
        values.append("".join(node.text or "" for node in si.findall(f".//{ns}t")))
    return values


def _excel_col_index(ref: str) -> int:
    letters = re.sub(r"[^A-Z]", "", ref.upper())
    value = 0
    for char in letters:
        value = value * 26 + (ord(char) - 64)
    return max(0, value - 1)


def _cell_text(cell: ET.Element, shared_strings: list[str]) -> str:
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return clean_text("".join(node.text or "" for node in cell.findall(f".//{ns}t")))
    value_node = cell.find(f"{ns}v")
    raw = "" if value_node is None or value_node.text is None else value_node.text
    if cell_type == "s":
        try:
            return clean_text(shared_strings[int(raw)])
        except (ValueError, IndexError):
            return ""
    return clean_text(raw)


def _normalize_import_rows(raw_rows: list[dict[str, Any]], *, source_owner_userid: str) -> list[dict[str, Any]]:
    seen_external_userids: set[str] = set()
    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        row_number = int(raw.get("row_number") or len(rows) + 2)
        external_userid = clean_text(raw.get("external_userid"))
        raw_flag = clean_text(raw.get("是否迁移")).lower()
        current_owner = clean_text(raw.get("当前负责人userid"))
        customer_name = clean_text(raw.get("客户备注名"))
        note = clean_text(raw.get("备注"))
        move_flag, flag_ok = _normalize_move_flag(raw_flag)
        parse_status = "parsed"
        parse_reason = "-"
        if not external_userid:
            parse_status = "missing_external_userid"
            parse_reason = "external_userid is required"
        elif not flag_ok:
            parse_status = "invalid_move_flag"
            parse_reason = "是否迁移字段非法"
        elif external_userid in seen_external_userids:
            parse_status = "duplicate"
            parse_reason = "duplicate external_userid; first row is kept"
        else:
            seen_external_userids.add(external_userid)
            if current_owner and current_owner != source_owner_userid:
                parse_reason = "当前负责人userid与选择的原负责人不一致，预览阶段将不可执行"
        rows.append(
            {
                "row_number": row_number,
                "external_userid": external_userid,
                "move_flag": move_flag,
                "current_owner_userid_in_file": current_owner,
                "customer_name_in_file": customer_name,
                "note": note,
                "parse_status": parse_status,
                "parse_reason": parse_reason,
            }
        )
    return rows


def _normalize_move_flag(raw_flag: str) -> tuple[str, bool]:
    normalized = clean_text(raw_flag).lower()
    if normalized in MOVE_YES_VALUES:
        return MOVE_FLAG_ALIASES.get(normalized, "是"), True
    if normalized in MOVE_NO_VALUES:
        return MOVE_FLAG_ALIASES.get(normalized, "否"), True
    return clean_text(raw_flag) or "", False


def _import_row_stats(rows: list[dict[str, Any]]) -> dict[str, int]:
    unique_external_userids = {row["external_userid"] for row in rows if row.get("external_userid") and row.get("parse_status") != "duplicate"}
    return {
        "total_rows": len(rows),
        "unique_external_userids": len(unique_external_userids),
        "marked_move": len([row for row in rows if row.get("move_flag") == "是" and row.get("parse_status") == "parsed"]),
        "marked_skip": len([row for row in rows if row.get("move_flag") == "否" and row.get("parse_status") == "parsed"]),
        "duplicate_rows": len([row for row in rows if row.get("parse_status") == "duplicate"]),
        "invalid_rows": len([row for row in rows if row.get("parse_status") in {"missing_external_userid", "invalid_move_flag"}]),
    }


def _excel_requested_external_userids(rows: list[dict[str, Any]]) -> list[str]:
    return [row["external_userid"] for row in rows if row.get("parse_status") == "parsed" and row.get("move_flag") == "是" and clean_text(row.get("external_userid"))]


def _preview_rows_from_session(
    *,
    session: dict[str, Any],
    source_owner_userid: str,
    target_owner_userid: str,
    source_candidates: set[str],
    owner_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for import_row in session.get("rows", []):
        external_userid = clean_text(import_row.get("external_userid"))
        parse_status = clean_text(import_row.get("parse_status"))
        move_flag = clean_text(import_row.get("move_flag")) or "是"
        current_owner_in_file = clean_text(import_row.get("current_owner_userid_in_file"))
        owner_info = owner_index.get(external_userid, {})
        owner_userids = set(owner_info.get("owner_userids") or [])
        status = "ready"
        can_execute = True
        reason = "-"
        current_owner = source_owner_userid if external_userid in source_candidates else (next(iter(owner_userids), "") if owner_userids else "")
        if parse_status == "duplicate":
            status, can_execute, reason = "duplicate", False, clean_text(import_row.get("parse_reason")) or "duplicate external_userid"
        elif parse_status == "missing_external_userid":
            status, can_execute, reason = "missing_external_userid", False, clean_text(import_row.get("parse_reason")) or "external_userid is required"
        elif parse_status == "invalid_move_flag":
            status, can_execute, reason = "invalid_move_flag", False, clean_text(import_row.get("parse_reason")) or "invalid move flag"
        elif move_flag == "否":
            status, can_execute, reason = "skipped_by_file", False, "Excel marked skip"
        elif current_owner_in_file and current_owner_in_file != source_owner_userid:
            status, can_execute, reason = "not_under_source_owner", False, "当前负责人userid与选择的原负责人不一致"
            current_owner = current_owner_in_file
        elif external_userid in source_candidates:
            status, can_execute, reason = "ready", True, "-"
            current_owner = source_owner_userid
        elif target_owner_userid in owner_userids:
            status, can_execute, reason = "already_target_owner", False, "Customer already belongs to target owner"
            current_owner = target_owner_userid
        elif owner_userids:
            status, can_execute, reason = "not_under_source_owner", False, "Customer is not under source owner"
        else:
            status, can_execute, reason = "not_found", False, "Customer is not found in current CRM candidate surfaces"
        rows.append(
            {
                "row_number": import_row.get("row_number"),
                "external_userid": external_userid,
                "customer_name": clean_text(owner_info.get("customer_name")) or clean_text(import_row.get("customer_name_in_file")),
                "move_flag": move_flag,
                "current_owner_userid": current_owner,
                "status": status,
                "can_execute": can_execute,
                "reason": reason,
                "note": clean_text(import_row.get("note")),
            }
        )
    return rows


def _preview_row_stats(rows: list[dict[str, Any]], *, total_rows: int, unique_external_userids: int) -> dict[str, int]:
    counts = {
        "total_rows": total_rows,
        "unique_external_userids": unique_external_userids,
        "ready": 0,
        "skipped_by_file": 0,
        "duplicate": 0,
        "missing_external_userid": 0,
        "invalid_move_flag": 0,
        "not_under_source_owner": 0,
        "not_found": 0,
        "already_target_owner": 0,
        "blocked": 0,
    }
    known = set(counts) - {"total_rows", "unique_external_userids", "blocked"}
    for row in rows:
        status = clean_text(row.get("status"))
        if status in counts:
            counts[status] += 1
        if status not in known:
            counts["blocked"] += 1
    return counts


def _preview_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _preview_parameter_mismatches(stored: dict[str, Any], current: dict[str, Any]) -> list[str]:
    mismatches: list[str] = []
    for key, value in current.items():
        stored_value = stored.get(key)
        if isinstance(value, bool):
            if bool(stored_value) != value:
                mismatches.append(key)
        elif clean_text(stored_value) != clean_text(value):
            mismatches.append(key)
    return mismatches


def _normalize_pending_review(value: dict[str, Any]) -> dict[str, int]:
    return {
        "user_ops_deferred_jobs": int(value.get("user_ops_deferred_jobs") or value.get("pending_user_ops_deferred_jobs") or 0),
        "broadcast_jobs": int(value.get("broadcast_jobs") or value.get("pending_broadcast_jobs") or 0),
        "outbound_tasks": int(value.get("outbound_tasks") or value.get("pending_outbound_tasks") or 0),
    }


def _scope_type(value: str | None) -> str:
    return clean_text(value) or "all"


def _uses_scoped_flow(command: OwnerMigrationCommand) -> bool:
    return any([clean_text(command.scope_type), clean_text(command.session_id), clean_text(command.preview_token), clean_text(command.preview_hash), clean_text(command.confirm_phrase)])


def _is_expired(value: str) -> bool:
    if not value:
        return True
    try:
        expires_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    return expires_at < datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _transfer_customers(
    *,
    source_owner_userid: str,
    target_owner_userid: str,
    external_userids: list[str],
    transfer_success_msg: str,
    batch_size: int,
    enabled: bool,
) -> dict[str, Any]:
    if not enabled:
        return {"ok": True, "enabled": False, "success_external_userids": list(external_userids), "failed_customers": [], "batches": []}
    if not external_userids:
        return {"ok": True, "enabled": True, "success_external_userids": [], "failed_customers": [], "batches": []}
    missing = missing_wecom_config()
    if missing:
        return {
            "ok": False,
            "enabled": True,
            "error_code": "missing_wecom_config",
            "error": "WeCom transfer config is missing",
            "missing_config": missing,
            "success_external_userids": [],
            "failed_customers": [],
            "batches": [],
        }
    adapter = ProductionWeComAdapter()
    success_external_userids: list[str] = []
    failed_customers: list[dict[str, Any]] = []
    batches: list[dict[str, Any]] = []
    for batch_index, start in enumerate(range(0, len(external_userids), batch_size), start=1):
        batch = external_userids[start : start + batch_size]
        payload: dict[str, Any] = {"handover_userid": source_owner_userid, "takeover_userid": target_owner_userid, "external_userid": batch}
        if transfer_success_msg:
            payload["transfer_success_msg"] = transfer_success_msg
        try:
            response = adapter.transfer_customer(payload)
        except WeComApiError as exc:
            return {
                "ok": False,
                "enabled": True,
                "error_code": "wecom_api_error",
                "error": exc.message,
                "payload": exc.payload,
                "success_external_userids": success_external_userids,
                "failed_customers": failed_customers,
                "batches": batches,
            }
        customer_results = list(response.get("customer") or [])
        reported_external_userids = {clean_text(item.get("external_userid")) for item in customer_results}
        batch_success = [clean_text(item.get("external_userid")) for item in customer_results if int(item.get("errcode") or 0) == 0 and clean_text(item.get("external_userid"))]
        batch_failed = []
        for item in customer_results:
            if int(item.get("errcode") or 0) == 0:
                continue
            failed_item = {"external_userid": clean_text(item.get("external_userid")), "errcode": int(item.get("errcode") or 0)}
            if clean_text(item.get("errmsg")):
                failed_item["errmsg"] = clean_text(item.get("errmsg"))
            batch_failed.append(failed_item)
        batch_failed.extend({"external_userid": external_userid, "errcode": -1, "errmsg": "missing_transfer_result"} for external_userid in batch if external_userid not in reported_external_userids)
        success_external_userids.extend(batch_success)
        failed_customers.extend(batch_failed)
        batches.append({"batch_index": batch_index, "requested_count": len(batch), "success_count": len(batch_success), "failed_count": len(batch_failed), "errcode": int(response.get("errcode") or 0), "errmsg": clean_text(response.get("errmsg"))})
    return {
        "ok": True,
        "enabled": True,
        "requested_count": len(external_userids),
        "success_count": len(success_external_userids),
        "failed_count": len(failed_customers),
        "success_external_userids": success_external_userids,
        "failed_customers": failed_customers,
        "batches": batches,
    }


def query_wecom_transfer_result(*, source_owner_userid: str, target_owner_userid: str, cursor: str = "") -> dict[str, Any]:
    source = clean_text(source_owner_userid)
    target = clean_text(target_owner_userid)
    if not source:
        return _error("source_owner_userid_required", "source_owner_userid is required")
    if not target:
        return _error("target_owner_userid_required", "target_owner_userid is required")
    payload: dict[str, Any] = {"handover_userid": source, "takeover_userid": target}
    if clean_text(cursor):
        payload["cursor"] = clean_text(cursor)
    missing = missing_wecom_config()
    if missing:
        return {"ok": False, "error_code": "missing_wecom_config", "message": "WeCom transfer config is missing", "error": "WeCom transfer config is missing", "details": {"missing_config": missing}, "missing_config": missing, "status_code": 502}
    try:
        result = ProductionWeComAdapter().transfer_result(payload)
    except WeComApiError as exc:
        return {"ok": False, "error_code": "wecom_api_error", "message": exc.message, "error": exc.message, "details": {"payload": exc.payload}, "payload": exc.payload, "status_code": 502}
    return {"ok": True, "source_owner_userid": source, "target_owner_userid": target, **result}


def _wecom_transfer_diagnostics() -> dict[str, Any]:
    missing = missing_wecom_config()
    return {"can_transfer_customer": not missing, "missing_config": missing, "real_wecom_adapter_reason": "missing_wecom_config" if missing else "enabled_by_owner_migration_confirmation"}
