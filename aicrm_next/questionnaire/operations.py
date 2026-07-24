from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from aicrm_next.integration_gateway.channel_completion_client import (
    ChannelCompletionClient,
    ChannelCompletionReadPort,
)
from aicrm_next.navigation_target import (
    DEFAULT_COMPLETION_TARGET,
    completion_action_with_lead_qr,
    normalize_completion_target,
)
from aicrm_next.navigation_target.domain import h5_url_for_legacy_fields
from aicrm_next.platform_foundation.external_effects import (
    ExternalEffectService,
    WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
)
from aicrm_next.platform_foundation.push_center.capability_status import PushCapabilityStatusReadService
from aicrm_next.questionnaire.external_push import build_questionnaire_external_effect_payload
from aicrm_next.shared.errors import ContractError
from aicrm_next.shared.outbound_https import WebhookUrlValidationError, validate_webhook_url
from .admin_write import QuestionnaireAdminWriteInputError, normalize_external_push_config
from .domain import normalize_questionnaire
from .repo import QuestionnaireRepository, build_questionnaire_repository


class QuestionnaireOperationsInputError(ValueError):
    pass


class QuestionnaireOperationsNotFoundError(LookupError):
    pass


class QuestionnaireOperationsConflictError(RuntimeError):
    pass


class QuestionnaireOperationsUnavailableError(RuntimeError):
    pass


def build_push_capability_reader() -> PushCapabilityStatusReadService:
    return PushCapabilityStatusReadService()


def build_external_effect_service() -> ExternalEffectService:
    return ExternalEffectService()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _default_target() -> dict[str, Any]:
    return deepcopy(DEFAULT_COMPLETION_TARGET)


def _external_push_projection(item: dict[str, Any]) -> dict[str, Any]:
    questionnaire = normalize_questionnaire(item)
    return {
        "enabled": bool(questionnaire.get("external_push_enabled")),
        "webhook_url": _text(questionnaire.get("external_push_url")),
        "type": _text(questionnaire.get("external_push_type")),
        "expires_at_ts": questionnaire.get("external_push_expires_at_ts"),
        "day": questionnaire.get("external_push_day"),
        "frequency": questionnaire.get("external_push_frequency"),
        "remark": _text(questionnaire.get("external_push_remark")),
        "custom_params": list(questionnaire.get("external_push_custom_params") or []),
    }


def _capability_projection() -> dict[str, Any]:
    try:
        capability = build_push_capability_reader().get_capability_status("questionnaire_external_push")
    except Exception as exc:
        raise QuestionnaireOperationsUnavailableError(str(exc)) from exc
    readonly_reason = _text(capability.get("readonly_reason"))
    return {
        "enabled": bool(capability.get("enabled")),
        "configured_enabled": bool(capability.get("configured_enabled")),
        "readonly": bool(readonly_reason),
        "reason": _text(capability.get("reason")) or readonly_reason or _text(capability.get("gate_problem")),
    }


class QuestionnaireOperationsService:
    def __init__(
        self,
        repository: QuestionnaireRepository | None = None,
        channel_reader: ChannelCompletionReadPort | None = None,
    ) -> None:
        self._repo = repository or build_questionnaire_repository()
        self._channels = channel_reader or ChannelCompletionClient()

    def get_operations(self, questionnaire_id: int) -> dict[str, Any]:
        item = self._require_questionnaire(questionnaire_id)
        return self._operations_payload(item)

    def save_completion(self, questionnaire_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_questionnaire(questionnaire_id)
        enabled = _bool(payload.get("enabled"))
        lead_channel_id: int | None = None
        lead_channel: dict[str, Any] | None = None
        target = _default_target()
        redirect_url = ""
        if enabled:
            action_type = _text(payload.get("action_type") or payload.get("type") or payload.get("mode"))
            if action_type == "lead_qr":
                try:
                    lead_channel_id = int(payload.get("lead_channel_id") or 0)
                except (TypeError, ValueError) as exc:
                    raise QuestionnaireOperationsInputError("lead_channel_id must be a positive integer") from exc
                if lead_channel_id <= 0:
                    raise QuestionnaireOperationsInputError("lead_channel_id is required for lead_qr")
                try:
                    lead_channel = self._channels.require_usable_channel_qr(lead_channel_id)
                except LookupError as exc:
                    raise QuestionnaireOperationsNotFoundError("channel not found") from exc
                except ValueError as exc:
                    raise QuestionnaireOperationsInputError(str(exc)) from exc
                except Exception as exc:
                    raise QuestionnaireOperationsUnavailableError(str(exc)) from exc
            elif action_type == "redirect":
                raw_target = payload.get("completion_target")
                if not isinstance(raw_target, dict):
                    raise QuestionnaireOperationsInputError("completion_target is required for redirect")
                try:
                    target = normalize_completion_target(raw_target)
                except ContractError as exc:
                    raise QuestionnaireOperationsInputError(str(exc)) from exc
                if not target.get("enabled"):
                    raise QuestionnaireOperationsInputError("completion_target must be enabled for redirect")
                if target.get("target_type") not in {"h5", "url_link"}:
                    raise QuestionnaireOperationsInputError("completion_target.target_type must be h5 or url_link")
                redirect_url = h5_url_for_legacy_fields(target)
            else:
                raise QuestionnaireOperationsInputError("action_type must be lead_qr or redirect")
        try:
            saved = self._repo.save_completion_operations(
                int(questionnaire_id),
                lead_channel_id=lead_channel_id,
                completion_target_json=target,
                redirect_url=redirect_url,
            )
        except Exception as exc:
            raise QuestionnaireOperationsUnavailableError(str(exc)) from exc
        if saved is None:
            raise QuestionnaireOperationsNotFoundError("questionnaire not found")
        return {
            "ok": True,
            "questionnaire": self._questionnaire_summary(saved),
            "completion": self._completion_projection(saved, lead_channel=lead_channel),
            **self._read_meta(),
        }

    def save_external_push(self, questionnaire_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_questionnaire(questionnaire_id)
        try:
            config = normalize_external_push_config({"external_push_config": payload})
        except (QuestionnaireAdminWriteInputError, TypeError, ValueError) as exc:
            raise QuestionnaireOperationsInputError(str(exc)) from exc
        if config["webhook_url"]:
            try:
                config["webhook_url"] = validate_webhook_url(config["webhook_url"])
            except WebhookUrlValidationError as exc:
                raise QuestionnaireOperationsInputError(str(exc)) from exc
        if config["enabled"] and not config["webhook_url"]:
            raise QuestionnaireOperationsInputError("webhook_url is required when external push is enabled")
        try:
            saved = self._repo.save_external_push_operations(int(questionnaire_id), config)
        except Exception as exc:
            raise QuestionnaireOperationsUnavailableError(str(exc)) from exc
        if saved is None:
            raise QuestionnaireOperationsNotFoundError("questionnaire not found")
        return {
            "ok": True,
            "questionnaire": self._questionnaire_summary(saved),
            "external_push": _external_push_projection(saved),
            **self._read_meta(),
        }

    def queue_external_push_test(self, questionnaire_id: int) -> dict[str, Any]:
        item = self._require_questionnaire(questionnaire_id)
        config = _external_push_projection(item)
        if not config["enabled"] or not config["webhook_url"]:
            raise QuestionnaireOperationsConflictError("external push must be enabled before testing")
        capability = _capability_projection()
        if not capability["enabled"] or capability["readonly"]:
            raise QuestionnaireOperationsConflictError(
                capability["reason"] or "questionnaire external push capability is disabled or read-only"
            )
        test_run_id = f"questionnaire-test-{uuid4().hex}"
        submitted_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        synthetic_submission = {
            "submission_id": test_run_id,
            "respondent_key": "questionnaire_test",
            "submitted_at": submitted_at,
            "answer_snapshots": [],
        }
        synthetic_body = {
            "user_id": "questionnaire_test",
            "questionnaire_title": _text(item.get("title") or item.get("name")),
            "submitted_at": submitted_at,
            "phone_number": "NULL",
            "answers": [],
            "is_test": True,
            "test_run_id": test_run_id,
        }
        for key in ("day", "frequency", "expires_at_ts", "type", "remark"):
            if config.get(key) not in (None, ""):
                synthetic_body[key] = config[key]
        reserved_test_fields = {
            "user_id",
            "phone_number",
            "mobile",
            "openid",
            "unionid",
            "external_userid",
            "answers",
            "submitted_at",
            "questionnaire_title",
            "test_run_id",
        }
        for custom_param in config.get("custom_params") or []:
            name = _text(custom_param.get("name") if isinstance(custom_param, dict) else "")
            normalized_name = name.casefold()
            contains_identity_field = any(
                fragment in normalized_name
                for fragment in ("phone", "mobile", "openid", "unionid", "external_user", "respondent")
            )
            if name and normalized_name not in reserved_test_fields and not contains_identity_field:
                synthetic_body[name] = custom_param.get("value")
        effect_payload = build_questionnaire_external_effect_payload(
            questionnaire={**item, "external_push_config": config},
            submission=synthetic_submission,
            computed_result={"answer_snapshots": []},
            target_url=config["webhook_url"],
            body=synthetic_body,
        )
        try:
            job = build_external_effect_service().plan_effect(
                effect_type=WEBHOOK_QUESTIONNAIRE_SUBMISSION_PUSH,
                adapter_name="outbound_webhook",
                operation="post",
                target_type="questionnaire_submission_test",
                target_id=test_run_id,
                business_type="questionnaire_test",
                business_id=str(questionnaire_id),
                payload=effect_payload,
                payload_summary={
                    "questionnaire_id": int(questionnaire_id),
                    "test_run_id": test_run_id,
                    "synthetic_data": True,
                    "target_url_present": True,
                    "real_external_call_executed": False,
                },
                source_module="questionnaire.operations",
                source_command_id=test_run_id,
                risk_level="medium",
                requires_approval=False,
                execution_mode="execute",
                status="queued",
                idempotency_key=f"questionnaire.operations.test:{test_run_id}",
            )
        except Exception as exc:
            raise QuestionnaireOperationsUnavailableError(str(exc)) from exc
        return {
            "ok": True,
            "questionnaire_id": int(questionnaire_id),
            "test_run_id": test_run_id,
            "job": job,
            "source_status": "next_command",
            "route_owner": "ai_crm_next",
            "fallback_used": False,
            "real_external_call_executed": False,
        }

    def resolve_completion_action(self, item: dict[str, Any]) -> dict[str, Any]:
        return resolve_questionnaire_completion_action(item, channel_reader=self._channels)

    def _require_questionnaire(self, questionnaire_id: int) -> dict[str, Any]:
        try:
            item = self._repo.get_questionnaire(int(questionnaire_id))
        except Exception as exc:
            raise QuestionnaireOperationsUnavailableError(str(exc)) from exc
        if not item:
            raise QuestionnaireOperationsNotFoundError("questionnaire not found")
        return item

    def _operations_payload(self, item: dict[str, Any]) -> dict[str, Any]:
        questionnaire = normalize_questionnaire(item)
        lead_channel = None
        lead_channel_id = questionnaire.get("lead_channel_id")
        if lead_channel_id:
            try:
                lead_channel = self._channels.get_channel_qr(int(lead_channel_id))
            except Exception as exc:
                raise QuestionnaireOperationsUnavailableError(str(exc)) from exc
        return {
            "ok": True,
            "questionnaire": self._questionnaire_summary(item),
            "completion": self._completion_projection(item, lead_channel=lead_channel),
            "external_push": _external_push_projection(item),
            "push_capability": _capability_projection(),
            **self._read_meta(),
        }

    def _questionnaire_summary(self, item: dict[str, Any]) -> dict[str, Any]:
        questionnaire = normalize_questionnaire(item)
        return {
            "id": int(questionnaire["id"]),
            "title": questionnaire["title"],
            "slug": questionnaire["slug"],
            "is_disabled": questionnaire["is_disabled"],
            "submission_count": questionnaire["submission_count"],
            "public_path": questionnaire["public_path"],
            "assessment_enabled": questionnaire["assessment_enabled"],
            "assessment_config": questionnaire["assessment_config"],
            "is_assessment_template_asset": questionnaire["is_assessment_template_asset"],
        }

    def _completion_projection(
        self,
        item: dict[str, Any],
        *,
        lead_channel: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        questionnaire = normalize_questionnaire(item)
        target = questionnaire["completion_target"]
        lead_channel_id = questionnaire.get("lead_channel_id")
        return {
            "enabled": bool(target.get("enabled") or lead_channel_id),
            "mode": "redirect" if target.get("enabled") else "lead_qr",
            "lead_channel_id": lead_channel_id,
            "lead_channel": lead_channel,
            "completion_target": target,
            "legacy_target_readonly": bool(target.get("enabled") and target.get("target_type") == "mini_program"),
        }

    def _read_meta(self) -> dict[str, Any]:
        return {
            "source_status": _text(getattr(self._repo, "source_status", "next_read_model")),
            "route_owner": "ai_crm_next",
            "fallback_used": False,
            "real_external_call_executed": False,
        }


def resolve_questionnaire_completion_action(
    item: dict[str, Any],
    *,
    channel_reader: ChannelCompletionReadPort | None = None,
) -> dict[str, Any]:
    questionnaire = normalize_questionnaire(item)
    channel = None
    lead_channel_id = questionnaire.get("lead_channel_id")
    if lead_channel_id:
        try:
            candidate = (channel_reader or ChannelCompletionClient()).get_channel_qr(int(lead_channel_id))
        except Exception:
            candidate = None
        if candidate and candidate.get("selectable"):
            channel = candidate
    action = completion_action_with_lead_qr(
        questionnaire["completion_target"],
        lead_qr=channel,
        legacy_redirect_url=questionnaire.get("redirect_url"),
    )
    if action.get("type") in {"url_link", "mini_program"}:
        action = {
            **action,
            "type": "redirect",
            "target_type": str(questionnaire["completion_target"].get("target_type") or ""),
            "navigation_target": action.get("navigation_target") or questionnaire["completion_target"],
        }
    return {
        "completion_action": action,
        "lead_qr": action.get("lead_qr") if action.get("type") == "lead_qr" else None,
    }


__all__ = [
    "QuestionnaireOperationsConflictError",
    "QuestionnaireOperationsInputError",
    "QuestionnaireOperationsNotFoundError",
    "QuestionnaireOperationsService",
    "QuestionnaireOperationsUnavailableError",
    "resolve_questionnaire_completion_action",
]
