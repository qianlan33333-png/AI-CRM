from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import PurePath
from typing import Any
from uuid import UUID


class OperationCycleConflictError(RuntimeError):
    """A deterministic report conflict that the HTTP layer maps to 409."""

    status_code = 409

    def __init__(self, code: str, message: str = "") -> None:
        self.code = str(code or "operation_cycle_report_conflict")
        self.status = self.code
        super().__init__(message or self.code)


class OperationCyclePrivacyError(ValueError):
    """The report contains data that is forbidden in this aggregate-only domain."""

    code = "operation_cycle_private_data_rejected"


_SENSITIVE_KEYS = {
    "authorization",
    "credential",
    "credentials",
    "customer_ids",
    "display_name",
    "email",
    "external_userid",
    "external_userids",
    "external_userid_list",
    "mobile",
    "mobile_number",
    "openid",
    "open_id",
    "password",
    "phone",
    "phone_number",
    "raw_body",
    "raw_msg",
    "raw_message",
    "raw_messages",
    "original_message",
    "original_messages",
    "message_content",
    "message_contents",
    "message_body",
    "message",
    "messages",
    "message_text",
    "content_text",
    "nickname",
    "people",
    "person_ids",
    "raw_content",
    "raw_text",
    "recipient_list",
    "recipient_ids",
    "recipients",
    "refresh_token",
    "secret",
    "secrets",
    "target_unionids",
    "target_unionids_json",
    "targets",
    "token",
    "unionid",
    "unionids",
    "user_list",
    "user_ids",
    "users",
    "member_ids",
    "members",
    "individuals",
    "access_token",
    "api_key",
    "api_secret",
    "client_secret",
    "private_key",
    "signing_key",
}
_SENSITIVE_KEY_PARTS = (
    "external_userid",
    "unionid",
    "openid",
    "phone_number",
    "mobile_number",
    "raw_message",
    "original_message",
    "message_content",
    "raw_content",
    "phone",
    "mobile",
    "credential",
    "secret",
    "password",
    "access_token",
    "refresh_token",
    "api_key",
    "api_secret",
    "client_secret",
    "private_key",
    "signing_key",
)
_MOBILE_PATTERN = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9](?:[-\s]?\d){9}(?!\d)")
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}")
_PEM_PATTERN = re.compile(r"-----BEGIN [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)-----")
_API_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(?<![a-z0-9])(?:sk|rk|pk)-(?:proj-)?[a-z0-9_-]{12,}(?![a-z0-9])"
    r"|(?<![A-Z0-9])AKIA[A-Z0-9]{16}(?![A-Z0-9])"
    r"|(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{20,}(?![A-Za-z0-9])"
)
_JWT_CREDENTIAL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
)
_EMAIL_PATTERN = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+(?![\w.-])")
_WECOM_EXTERNAL_ID_PATTERN = re.compile(r"(?<![A-Za-z0-9])wm[A-Za-z0-9_-]{6,}(?![A-Za-z0-9])")
_LOCAL_PATH_PATTERN = re.compile(r"^(?:file://|/Users/|/home/|[A-Za-z]:\\)")


def _canonical_private_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


_CANONICAL_SENSITIVE_KEYS = {_canonical_private_key(value) for value in _SENSITIVE_KEYS}
_CANONICAL_SENSITIVE_KEY_PARTS = tuple(
    _canonical_private_key(value)
    for value in (*_SENSITIVE_KEY_PARTS, "raw_msg")
)


def _json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, UUID)):
        return str(value.isoformat() if isinstance(value, datetime) else value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    return value


def canonical_snapshot_json(payload: Any) -> str:
    return json.dumps(
        _json_safe(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def compute_snapshot_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_snapshot_json(payload).encode("utf-8")).hexdigest()


def validate_private_payload(payload: Any, *, path: str = "$") -> None:
    """Recursively reject identifiers, raw messages, credentials and local paths.

    This domain intentionally stores aggregate snapshots only. Key inspection is
    strict, while value inspection is limited to unambiguous phone/credential and
    local-path patterns so ordinary conclusions remain usable.
    """

    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    if isinstance(payload, dict):
        for raw_key, value in payload.items():
            key = str(raw_key or "").strip().lower().replace("-", "_")
            canonical_key = _canonical_private_key(raw_key)
            child_path = f"{path}.{raw_key}"
            if (
                key in _SENSITIVE_KEYS
                or canonical_key in _CANONICAL_SENSITIVE_KEYS
                or any(part in canonical_key for part in _CANONICAL_SENSITIVE_KEY_PARTS)
            ):
                raise OperationCyclePrivacyError(f"forbidden private field at {child_path}")
            validate_private_payload(value, path=child_path)
        return
    if isinstance(payload, (list, tuple, set)):
        for index, value in enumerate(payload):
            validate_private_payload(value, path=f"{path}[{index}]")
        return
    if isinstance(payload, PurePath):
        raise OperationCyclePrivacyError(f"local artifact path is forbidden at {path}")
    if isinstance(payload, str):
        value = payload.strip()
        if _MOBILE_PATTERN.search(value):
            raise OperationCyclePrivacyError(f"phone number is forbidden at {path}")
        if _EMAIL_PATTERN.search(value) or _WECOM_EXTERNAL_ID_PATTERN.search(value):
            raise OperationCyclePrivacyError(f"personal identifier is forbidden at {path}")
        if (
            _BEARER_PATTERN.search(value)
            or _PEM_PATTERN.search(value)
            or _API_CREDENTIAL_PATTERN.search(value)
            or _JWT_CREDENTIAL_PATTERN.search(value)
        ):
            raise OperationCyclePrivacyError(f"credential material is forbidden at {path}")
        if _LOCAL_PATH_PATTERN.search(value):
            raise OperationCyclePrivacyError(f"local artifact path is forbidden at {path}")


def ensure_unique(values: list[str], *, field_name: str) -> None:
    normalized = [str(value or "").strip() for value in values]
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must be unique within a complete snapshot")


_EXECUTION_STAGE_ORDER = {
    "scheduled": 0,
    "preflight": 1,
    "decisioning": 2,
    "dry_run": 3,
    "review": 4,
    "delivery": 5,
    "observing": 6,
    "postmortem": 7,
    "closed": 8,
}
_DELIVERY_STATUS_ORDER = {
    "not_started": 0,
    "waiting_window": 1,
    "dispatching": 2,
    "partial": 3,
    "completed": 4,
    "failed": 4,
    "cancelled": 4,
}


def validate_attempt_revision(previous: Any, current: Any) -> None:
    """Preserve blocked attempts and require an explicit recovery attempt.

    A complete later snapshot may enrich a previous attempt, but it cannot
    rewrite a blocked execution into success. Advancing the run after an
    unresolved block requires a newly recorded direct child attempt.
    """

    previous_attempts = {item.attempt_key: item for item in previous.attempts}
    current_attempts = {item.attempt_key: item for item in current.attempts}
    if _EXECUTION_STAGE_ORDER[current.execution_stage] < _EXECUTION_STAGE_ORDER[previous.execution_stage]:
        raise OperationCycleConflictError("execution_stage_regression")
    for attempt_key, previous_attempt in previous_attempts.items():
        current_attempt = current_attempts.get(attempt_key)
        if current_attempt is None:
            raise OperationCycleConflictError("attempt_history_missing")
        if current_attempt.parent_attempt_key != previous_attempt.parent_attempt_key:
            raise OperationCycleConflictError("attempt_parent_conflict")
        if previous_attempt.status == "blocked" and current_attempt.status != "blocked":
            raise OperationCycleConflictError("blocked_attempt_mutation")
        if previous_attempt.status == "completed" and current_attempt.status != "completed":
            raise OperationCycleConflictError("completed_attempt_mutation")

    previous_stages = {item.stage_key: item for item in previous.stages}
    current_stages = {item.stage_key: item for item in current.stages}
    for stage_key, previous_stage in previous_stages.items():
        current_stage = current_stages.get(stage_key)
        if current_stage is None:
            raise OperationCycleConflictError("stage_history_missing")
        if current_stage.attempt_key != previous_stage.attempt_key or current_stage.stage != previous_stage.stage:
            raise OperationCycleConflictError("stage_identity_conflict")
        if previous_stage.status in {"blocked", "completed"} and current_stage.status != previous_stage.status:
            raise OperationCycleConflictError("terminal_stage_mutation")

    stage_progressed = _EXECUTION_STAGE_ORDER[current.execution_stage] > _EXECUTION_STAGE_ORDER[previous.execution_stage]
    delivery_progressed = _DELIVERY_STATUS_ORDER[current.delivery_status] > _DELIVERY_STATUS_ORDER[previous.delivery_status]
    if not (stage_progressed or delivery_progressed):
        return

    newly_added = set(current_attempts) - set(previous_attempts)
    for attempt_key, previous_attempt in previous_attempts.items():
        if previous_attempt.status != "blocked":
            continue
        already_had_direct_child = any(
            child.parent_attempt_key == attempt_key
            for child in previous_attempts.values()
        )
        if already_had_direct_child:
            continue
        has_new_direct_child = any(
            child_key in newly_added and child.parent_attempt_key == attempt_key
            for child_key, child in current_attempts.items()
        )
        if not has_new_direct_child:
            raise OperationCycleConflictError("recovery_attempt_required")
