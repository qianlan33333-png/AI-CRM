from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any


Json = dict[str, Any]


@dataclass(frozen=True)
class _IdempotencyRecord:
    request_hash: str
    response: Json


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _hash(payload: Json) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _redact(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return "<redacted>" if len(text) <= 8 else f"{text[:4]}...{text[-4:]}"


def _redact_identity(identity: Json | None) -> Json:
    source = identity if isinstance(identity, dict) else {}
    return {
        "openid_redacted": _redact(str(source.get("openid") or "")),
        "unionid_redacted": _redact(str(source.get("unionid") or "")),
        "external_userid_redacted": _redact(str(source.get("external_userid") or "")),
        "mobile_redacted": _redact(str(source.get("mobile") or "")),
    }


def _side_effect_safety() -> Json:
    return {
        "production_public_submit_write_executed": False,
        "production_identity_write_executed": False,
        "production_tag_write_executed": False,
        "production_oauth_callback_cutover_executed": False,
        "outbound_send_executed": False,
        "batch_tag_write_executed": False,
        "external_userid_raw_output": False,
        "production_success_claimed": False,
        "route_owner_changed": False,
        "production_compat_changed": False,
        "fallback_removed": False,
    }


def _base_response() -> Json:
    safety = _side_effect_safety()
    return {
        "adapter_mode": "questionnaire_external_submit_fake_stub",
        **safety,
        "side_effect_safety": safety,
        "timestamp": _timestamp(),
    }


class QuestionnaireExternalSubmitFakeStubAdapter:
    def __init__(self) -> None:
        self._idempotency: dict[str, _IdempotencyRecord] = {}

    def deterministic_fake_public_submission(self) -> Json:
        return {
            "slug": "phase5ak-safe-questionnaire",
            "answers": {"q1": "fake_option_a", "q2": "fake_option_b"},
            "identity": {
                "openid": "openid_phase5ak_fake_001",
                "unionid": "unionid_phase5ak_fake_001",
                "external_userid": "wm_phase5ak_fake_001",
            },
            "tag_ids": ["tag_phase5ak_fake_safe"],
        }

    def validate_external_submit(self, *, slug: str, answers: Json | None, identity: Json | None) -> Json:
        if not str(slug or "").strip():
            return {**_base_response(), "ok": False, "result_status": "slug_missing", "error_code": "slug_missing"}
        if not isinstance(answers, dict) or not answers:
            return {**_base_response(), "ok": False, "result_status": "answers_missing", "error_code": "answers_missing"}
        redacted_identity = _redact_identity(identity)
        return {
            **_base_response(),
            "ok": True,
            "result_status": "valid_fake_stub_submit",
            "slug": str(slug),
            "answer_keys": sorted(str(key) for key in answers),
            "identity_redacted": redacted_identity,
        }

    def dry_run_public_submit(self, *, slug: str, answers: Json | None, identity: Json | None, operator: str, idempotency_key: str) -> Json:
        return self._with_idempotency(
            idempotency_key=idempotency_key,
            operation="dry_run_public_submit",
            payload={"slug": slug, "answers": answers or {}, "identity_redacted": _redact_identity(identity), "operator": operator},
            factory=lambda request_hash: self._dry_run_submit_response(slug=slug, answers=answers, identity=identity, operator=operator, request_hash=request_hash, idempotency_key=idempotency_key),
        )

    def dry_run_identity_mapping(self, *, submission: Json, operator: str, idempotency_key: str) -> Json:
        identity = submission.get("identity") if isinstance(submission, dict) else {}
        return self._with_idempotency(
            idempotency_key=idempotency_key,
            operation="dry_run_identity_mapping",
            payload={"submission_hash": _hash(submission if isinstance(submission, dict) else {}), "identity_redacted": _redact_identity(identity), "operator": operator},
            factory=lambda request_hash: {
                **_base_response(),
                "ok": True,
                "result_status": "identity_mapping_dry_run_ready",
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
                "operator": operator,
                "identity_redacted": _redact_identity(identity),
            },
        )

    def dry_run_tag_writeback(self, *, submission: Json, tag_ids: list[str], operator: str, idempotency_key: str) -> Json:
        normalized_tags = sorted({str(tag).strip() for tag in tag_ids if str(tag).strip()})
        if not normalized_tags:
            return {**_base_response(), "ok": False, "result_status": "tag_ids_missing", "error_code": "tag_ids_missing"}
        return self._with_idempotency(
            idempotency_key=idempotency_key,
            operation="dry_run_tag_writeback",
            payload={"submission_hash": _hash(submission if isinstance(submission, dict) else {}), "tag_ids": normalized_tags, "operator": operator},
            factory=lambda request_hash: {
                **_base_response(),
                "ok": True,
                "result_status": "tag_writeback_dry_run_ready",
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
                "operator": operator,
                "tag_ids_normalized": normalized_tags,
            },
        )

    def _dry_run_submit_response(self, *, slug: str, answers: Json | None, identity: Json | None, operator: str, request_hash: str, idempotency_key: str) -> Json:
        validation = self.validate_external_submit(slug=slug, answers=answers, identity=identity)
        if not validation.get("ok"):
            return {**validation, "idempotency_key": idempotency_key, "request_hash": request_hash}
        return {
            **_base_response(),
            "ok": True,
            "result_status": "public_submit_dry_run_ready",
            "submission_id": f"fake_sub_{request_hash[:12]}",
            "slug": str(slug),
            "answer_keys": sorted(str(key) for key in (answers or {})),
            "identity_redacted": _redact_identity(identity),
            "operator": operator,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
        }

    def _with_idempotency(self, *, idempotency_key: str, operation: str, payload: Json, factory: Any) -> Json:
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key:
            return {**_base_response(), "ok": False, "result_status": "idempotency_key_required", "error_code": "idempotency_key_required"}
        request_hash = _hash({"operation": operation, **payload})
        existing = self._idempotency.get(normalized_key)
        if existing and existing.request_hash != request_hash:
            return {**_base_response(), "ok": False, "result_status": "conflict", "error_code": "duplicate_idempotency_key", "idempotency_key": normalized_key, "request_hash": request_hash}
        if existing:
            replay = deepcopy(existing.response)
            replay["result_status"] = "replay"
            replay["idempotency_replay"] = True
            replay["timestamp"] = _timestamp()
            return replay
        response = factory(request_hash)
        self._idempotency[normalized_key] = _IdempotencyRecord(request_hash=request_hash, response=deepcopy(response))
        return response


def build_questionnaire_external_submit_fake_stub_adapter() -> QuestionnaireExternalSubmitFakeStubAdapter:
    return QuestionnaireExternalSubmitFakeStubAdapter()
