from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from aicrm_next.platform_foundation.audit_ledger import InMemoryAuditLedger
from aicrm_next.platform_foundation.command_bus import Command, CommandBus, CommandContext, CommandResult
from aicrm_next.platform_foundation.side_effects import InMemorySideEffectPlanRepository, SideEffectPlan
from aicrm_next.navigation_target.service import normalize_completion_target_for_storage
from aicrm_next.shared.errors import ContractError, NotFoundError

from .domain import admin_detail_projection, summary_projection
from .repo import QuestionnaireRepository, build_questionnaire_repository


class QuestionnaireAdminWriteInputError(ValueError):
    pass


class QuestionnaireAdminWriteNotFoundError(LookupError):
    pass


class QuestionnaireAdminWriteProductionUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class QuestionnaireAdminWriteCommand:
    command_name: str
    questionnaire_id: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    command_id: str = field(default_factory=lambda: uuid4().hex)
    idempotency_key: str = ""
    actor_id: str = "questionnaire_admin"
    actor_type: str = "user"
    dry_run: bool = False
    source_route: str = ""
    trace_id: str = field(default_factory=lambda: uuid4().hex)

    def to_payload(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "idempotency_key": self.idempotency_key,
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "questionnaire_id": self.questionnaire_id,
            "payload": dict(self.payload),
            "dry_run": self.dry_run,
            "source_route": self.source_route,
            "trace_id": self.trace_id,
        }


_audit_ledger = InMemoryAuditLedger()
_side_effect_plans = InMemorySideEffectPlanRepository()
_command_bus = CommandBus()


QUESTIONNAIRE_EXTERNAL_PUSH_TYPES = {"", "subscription", "premium", "trial"}
_BEIJING_TZ = timezone(timedelta(hours=8))
_EXPORT_BASE_FIELDS = ["submission_id", "submitted_at", "external_userid", "unionid", "mobile", "score", "final_tags"]
QUESTIONNAIRE_QUESTION_TYPES = {"single_choice", "multi_choice", "textarea", "mobile"}
QUESTIONNAIRE_ANSWER_DISPLAY_MODES = {"all_in_one", "one_by_one"}


class QuestionnaireLifecyclePolicy:
    def validate_command(self, command: QuestionnaireAdminWriteCommand) -> None:
        _validate_command(command)

    def validate_upsert(self, payload: dict[str, Any], *, existing_has_questions: bool = False) -> dict[str, Any]:
        normalized = _normalize_upsert_payload(payload)
        if not str(normalized.get("title") or "").strip():
            raise QuestionnaireAdminWriteInputError("title is required")
        if existing_has_questions and not normalized["questions"]:
            raise QuestionnaireAdminWriteInputError("questions cannot be empty when updating an existing questionnaire with questions")
        return normalized


class QuestionnaireAdminRepository:
    def __init__(self, repository: QuestionnaireRepository | None = None) -> None:
        self._repository = repository or _repo()

    def list_submissions(self, questionnaire_id: int, *, limit: int = 20, offset: int = 0) -> tuple[list[dict[str, Any]], int] | None:
        return self._repository.list_submissions(questionnaire_id, limit=limit, offset=offset)

    def get_questionnaire(self, questionnaire_id: int) -> dict[str, Any] | None:
        return self._repository.get_questionnaire(questionnaire_id)

    def save_questionnaire(self, payload: dict[str, Any], questionnaire_id: int | None = None) -> dict[str, Any]:
        return self._repository.save_questionnaire(payload, questionnaire_id)

    def set_enabled(self, questionnaire_id: int, enabled: bool) -> dict[str, Any] | None:
        return self._repository.set_enabled(questionnaire_id, enabled)

    def delete_questionnaire(self, questionnaire_id: int) -> bool:
        return self._repository.delete_questionnaire(questionnaire_id)


class QuestionnaireAuditWriter:
    def record(self, command: Command, result: CommandResult) -> None:
        _audit_ledger.record_event(
            event_type=f"{command.command_name}.{result.status}",
            actor_id=result.actor_id,
            actor_type=result.actor_type,
            target_type="questionnaire",
            target_id=str(result.payload.get("questionnaire_id") or command.payload.get("questionnaire_id") or ""),
            source_route=result.source_route,
            command_id=result.command_id,
            trace_id=result.trace_id,
            payload={
                "status": result.status,
                "write_model_status": result.payload.get("write_model_status") or "",
                "fallback_used": False,
                "real_external_call_executed": False,
            },
        )


class QuestionnaireExternalPushConfigPlanner:
    def optional_config_plan(self, command: Command, item: dict[str, Any]) -> SideEffectPlan | None:
        config = dict(item.get("external_push_config") or {})
        if not bool(config.get("enabled")):
            return None
        return _create_side_effect_plan(
            command=command,
            effect_type="questionnaire.external_push.configure",
            adapter_name="external_push",
            target_id=str(item["id"]),
            payload_summary={"questionnaire_id": int(item["id"]), "external_push_configured": True},
            risk_level="medium",
        )

    def publish_plan(self, command: Command, questionnaire_id: int) -> SideEffectPlan:
        return _create_side_effect_plan(
            command=command,
            effect_type="questionnaire.public_projection.publish",
            adapter_name="questionnaire_projection",
            target_id=str(questionnaire_id),
            payload_summary={"questionnaire_id": questionnaire_id, "publish": True},
            risk_level="medium",
        )

    def export_preview_plan(self, command: Command, questionnaire_id: int, fields: list[str], total: int) -> SideEffectPlan:
        return _create_side_effect_plan(
            command=command,
            effect_type="questionnaire.export.preview",
            adapter_name="storage",
            target_id=str(questionnaire_id),
            payload_summary={"questionnaire_id": questionnaire_id, "fields": fields, "estimated_count": total},
            risk_level="medium",
        )

    def export_download_plan(self, command: Command, questionnaire_id: int, fields: list[str], total: int) -> SideEffectPlan:
        return _create_side_effect_plan(
            command=command,
            effect_type="questionnaire.export.download",
            adapter_name="response_stream",
            target_id=str(questionnaire_id),
            payload_summary={"questionnaire_id": questionnaire_id, "fields": fields, "estimated_count": total},
            risk_level="medium",
        )


class QuestionnaireAdminCommandService:
    def __init__(self) -> None:
        self.lifecycle_policy = QuestionnaireLifecyclePolicy()

    def execute(self, command: QuestionnaireAdminWriteCommand) -> dict[str, Any]:
        self.lifecycle_policy.validate_command(command)
        platform_command = Command(
            command_name=command.command_name,
            payload=command.to_payload(),
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
            context=CommandContext(
                actor_id=command.actor_id,
                actor_type=command.actor_type,
                trace_id=command.trace_id,
                source_route=command.source_route,
                dry_run=command.dry_run,
            ),
        )
        result = _command_bus.execute(platform_command)
        if result.status == "failed":
            if "questionnaire not found" in result.error:
                raise QuestionnaireAdminWriteNotFoundError("questionnaire not found")
            if (
                "is required" in result.error
                or "Field required" in result.error
                or "validation error" in result.error
                or "json object" in result.error
                or "cannot be empty" in result.error
                or "already exists" in result.error
                or "must be" in result.error
            ):
                raise QuestionnaireAdminWriteInputError(result.error)
            raise QuestionnaireAdminWriteProductionUnavailableError(result.error)

        payload = dict(result.payload)
        payload.setdefault("questionnaire_id", command.questionnaire_id or 0)
        payload.setdefault("write_model_status", "dry_run" if result.status == "dry_run" else "updated")
        return _response_from_result(result, payload)


_command_service = QuestionnaireAdminCommandService()


def reset_questionnaire_admin_write_fixture_state() -> None:
    global _audit_ledger, _side_effect_plans, _command_bus, _command_service
    _audit_ledger = InMemoryAuditLedger()
    _side_effect_plans = InMemorySideEffectPlanRepository()
    _command_bus = CommandBus(audit_hook=QuestionnaireAuditWriter().record)
    _command_service = QuestionnaireAdminCommandService()
    _register_handlers()


def get_questionnaire_admin_write_audit_events() -> list[dict[str, Any]]:
    return [event.to_dict() for event in _audit_ledger.list_events()]


def get_questionnaire_admin_write_side_effect_plans() -> list[dict[str, Any]]:
    return [_plan_response(plan) for plan in _side_effect_plans.list_plans()]


def execute_questionnaire_admin_write(command: QuestionnaireAdminWriteCommand) -> dict[str, Any]:
    return _command_service.execute(command)


def _register_handlers() -> None:
    _command_bus.register("questionnaire.admin.create", _handle_create)
    _command_bus.register("questionnaire.admin.update", _handle_update)
    _command_bus.register("questionnaire.admin.duplicate", _handle_duplicate)
    _command_bus.register("questionnaire.admin.publish", _handle_publish)
    _command_bus.register("questionnaire.admin.enable", _handle_enable)
    _command_bus.register("questionnaire.admin.disable", _handle_disable)
    _command_bus.register("questionnaire.admin.delete", _handle_delete)
    _command_bus.register("questionnaire.admin.export_preview", _handle_export_preview)
    _command_bus.register("questionnaire.admin.export_audit", _handle_export_preview)
    _command_bus.register("questionnaire.admin.export_download", _handle_export_download)


def _validate_command(command: QuestionnaireAdminWriteCommand) -> None:
    if not command.command_id.strip():
        raise QuestionnaireAdminWriteInputError("command_id is required")
    if not command.source_route.strip():
        raise QuestionnaireAdminWriteInputError("source_route is required")
    if not command.actor_id.strip():
        raise QuestionnaireAdminWriteInputError("actor_id is required")
    if command.command_name != "questionnaire.admin.create" and not command.questionnaire_id:
        raise QuestionnaireAdminWriteInputError("questionnaire_id is required")


def _repo() -> QuestionnaireRepository:
    try:
        return build_questionnaire_repository()
    except Exception as exc:
        raise QuestionnaireAdminWriteProductionUnavailableError(str(exc)) from exc


def _upsert_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return QuestionnaireLifecyclePolicy().validate_upsert(payload)


def _normalized_text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _normalized_text(item)
        if text and text not in result:
            result.append(text)
    return result


def normalize_external_push_config(payload: dict[str, Any]) -> dict[str, Any]:
    config = dict(payload.get("external_push_config") or {})
    root_to_config = {
        "external_push_enabled": "enabled",
        "external_push_url": "webhook_url",
        "external_push_type": "type",
        "external_push_expires_at_ts": "expires_at_ts",
        "external_push_day": "day",
        "external_push_frequency": "frequency",
        "external_push_remark": "remark",
        "external_push_custom_params": "custom_params",
    }
    for root_key, config_key in root_to_config.items():
        if root_key in payload:
            config[config_key] = payload.get(root_key)
    external_type = _normalized_text(config.get("type"))
    if external_type not in QUESTIONNAIRE_EXTERNAL_PUSH_TYPES:
        raise QuestionnaireAdminWriteInputError("external_push_type must be subscription, premium or trial")
    return {
        "enabled": _normalized_bool(config.get("enabled")),
        "webhook_url": _normalized_text(config.get("webhook_url") or config.get("url")),
        "type": external_type,
        "expires_at_ts": _optional_int(config.get("expires_at_ts")),
        "day": _optional_int(config.get("day")),
        "frequency": _optional_int(config.get("frequency")),
        "remark": _normalized_text(config.get("remark")),
        "custom_params": list(config.get("custom_params") or []) if isinstance(config.get("custom_params"), list) else [],
    }


def _normalize_option(option: dict[str, Any], index: int) -> dict[str, Any]:
    option_text = _normalized_text(option.get("option_text") or option.get("label") or option.get("value"))
    if not option_text:
        raise QuestionnaireAdminWriteInputError("option_text is required")
    other_max_length = _optional_int(option.get("other_max_length"))
    if other_max_length is None:
        other_max_length = 80
    if other_max_length < 1 or other_max_length > 200:
        raise QuestionnaireAdminWriteInputError("other_max_length must be between 1 and 200")
    return {
        "id": option.get("id") or option.get("value") or f"option_{index + 1}",
        "option_text": option_text,
        "label": option_text,
        "value": _normalized_text(option.get("value") or option.get("id") or option_text),
        "score": float(option.get("score") or 0),
        "assessment_type_key": _normalized_text(option.get("assessment_type_key")),
        "tag_codes": _string_list(option.get("tag_codes")),
        "is_other": _normalized_bool(option.get("is_other")),
        "other_placeholder": _normalized_text(option.get("other_placeholder")),
        "other_max_length": other_max_length,
        "sort_order": int(option.get("sort_order") or index + 1),
    }


def _normalize_question(question: dict[str, Any], index: int) -> dict[str, Any]:
    question_type = _normalized_text(question.get("type") or "single_choice")
    if question_type not in QUESTIONNAIRE_QUESTION_TYPES:
        raise QuestionnaireAdminWriteInputError("question type must be single_choice, multi_choice, textarea or mobile")
    title = _normalized_text(question.get("title"))
    if not title:
        raise QuestionnaireAdminWriteInputError("question title is required")
    raw_options = list(question.get("options") or []) if isinstance(question.get("options") or [], list) else []
    if question_type in {"textarea", "mobile"}:
        if raw_options:
            raise QuestionnaireAdminWriteInputError("options are not allowed for textarea or mobile questions")
        options = []
    else:
        options = [
            _normalize_option(dict(option or {}), option_index)
            for option_index, option in enumerate(raw_options)
        ]
        if sum(1 for option in options if bool(option.get("is_other"))) > 1:
            raise QuestionnaireAdminWriteInputError("other option count must be at most one per question")
    return {
        "id": question.get("id") or f"q_{index + 1}",
        "type": question_type,
        "title": title,
        "placeholder_text": _normalized_text(question.get("placeholder_text")),
        "assessment_dimension_key": _normalized_text(question.get("assessment_dimension_key")),
        "sidebar_profile_field": _normalized_text(question.get("sidebar_profile_field")),
        "required": _normalized_bool(question.get("required")),
        "sort_order": int(question.get("sort_order") or index + 1),
        "options": options,
    }


def _normalize_score_rule(rule: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "min_score": _optional_float(rule.get("min_score")),
        "max_score": _optional_float(rule.get("max_score")),
        "tag_codes": _string_list(rule.get("tag_codes")),
        "sort_order": int(rule.get("sort_order") or index + 1),
    }


def _normalize_upsert_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw = dict(payload or {})
    title = _normalized_text(raw.get("title") or raw.get("name"))
    slug = _normalized_text(raw.get("slug"))
    answer_display_mode = _normalized_text(raw.get("answer_display_mode") or "all_in_one")
    if answer_display_mode not in QUESTIONNAIRE_ANSWER_DISPLAY_MODES:
        raise QuestionnaireAdminWriteInputError("answer_display_mode must be all_in_one or one_by_one")
    external_config = normalize_external_push_config(raw)
    is_disabled = _normalized_bool(raw.get("is_disabled")) if "is_disabled" in raw else not _normalized_bool(raw.get("enabled", True))
    assessment_config = raw.get("assessment_config")
    if assessment_config is None:
        assessment_config = raw.get("result_config") or {}
    if not isinstance(assessment_config, dict):
        raise QuestionnaireAdminWriteInputError("assessment_config must be an object")
    completion_target = normalize_completion_target_for_storage(raw, legacy_url_key="redirect_url")
    return {
        "slug": slug,
        "name": _normalized_text(raw.get("name") or title),
        "title": title,
        "description": _normalized_text(raw.get("description")),
        "enabled": not is_disabled,
        "is_disabled": is_disabled,
        "redirect_url": _normalized_text(raw.get("redirect_url")),
        "completion_target_json": completion_target,
        "submit_button_text": _normalized_text(raw.get("submit_button_text") or "提交"),
        "answer_display_mode": answer_display_mode,
        "assessment_enabled": _normalized_bool(raw.get("assessment_enabled")),
        "assessment_config": assessment_config,
        "result_config": assessment_config,
        "questions": [
            _normalize_question(dict(question or {}), index)
            for index, question in enumerate(raw.get("questions") or [])
        ],
        "score_rules": [
            _normalize_score_rule(dict(rule or {}), index)
            for index, rule in enumerate(raw.get("score_rules") or raw.get("rules") or [])
        ],
        "external_push_config": external_config,
        "external_push_enabled": external_config["enabled"],
        "external_push_url": external_config["webhook_url"],
        "external_push_type": external_config["type"],
        "external_push_expires_at_ts": external_config["expires_at_ts"],
        "external_push_day": external_config["day"],
        "external_push_frequency": external_config["frequency"],
        "external_push_remark": external_config["remark"],
        "external_push_custom_params": external_config["custom_params"],
    }


def _handle_create(command: Command) -> dict[str, Any]:
    payload = _upsert_payload(dict(command.payload.get("payload") or {}))
    if not str(payload.get("title") or "").strip():
        raise ContractError("title is required")
    item = QuestionnaireAdminRepository().save_questionnaire(payload)
    response = admin_detail_projection(item)
    response.update(
        {
            "questionnaire_id": int(item["id"]),
            "write_model_status": "created",
        }
    )
    plan = QuestionnaireExternalPushConfigPlanner().optional_config_plan(command, item)
    if plan:
        response["side_effect_plan"] = _plan_response(plan)
    return response


def _handle_update(command: Command) -> dict[str, Any]:
    questionnaire_id = int(command.payload["questionnaire_id"])
    admin_repo = QuestionnaireAdminRepository()
    existing = admin_repo.get_questionnaire(questionnaire_id)
    if not existing:
        raise NotFoundError("questionnaire not found")
    requested_payload = dict(command.payload.get("payload") or {})
    completion_keys = {"completion_target", "completion_target_json", "redirect_url"}
    if not completion_keys.intersection(requested_payload):
        requested_payload["completion_target"] = dict(existing.get("completion_target_json") or existing.get("completion_target") or {})
        requested_payload["redirect_url"] = str(existing.get("redirect_url") or "")
    external_keys = {
        "external_push_config",
        "external_push_enabled",
        "external_push_url",
        "external_push_type",
        "external_push_expires_at_ts",
        "external_push_day",
        "external_push_frequency",
        "external_push_remark",
        "external_push_custom_params",
    }
    if not external_keys.intersection(requested_payload):
        requested_payload["external_push_config"] = dict(existing.get("external_push_config") or {})
    payload = QuestionnaireLifecyclePolicy().validate_upsert(
        requested_payload,
        existing_has_questions=bool(existing.get("questions")),
    )
    if not str(payload.get("title") or "").strip():
        raise ContractError("title is required")
    item = admin_repo.save_questionnaire(payload, questionnaire_id)
    if not item:
        raise NotFoundError("questionnaire not found")
    response = admin_detail_projection(item)
    response.update({"questionnaire_id": questionnaire_id, "write_model_status": "updated"})
    plan = QuestionnaireExternalPushConfigPlanner().optional_config_plan(command, item)
    if plan:
        response["side_effect_plan"] = _plan_response(plan)
    return response


def _handle_duplicate(command: Command) -> dict[str, Any]:
    questionnaire_id = int(command.payload["questionnaire_id"])
    source = _repo().get_questionnaire(questionnaire_id)
    if not source:
        raise NotFoundError("questionnaire not found")
    payload = dict(source)
    requested = dict(command.payload.get("payload") or {})
    payload["title"] = str(requested.get("title") or f"{source.get('title') or source.get('name')} Copy").strip()
    payload["slug"] = str(requested.get("slug") or f"{source.get('slug')}-copy-{command.command_id[:6]}").strip()
    payload["enabled"] = False
    payload["is_disabled"] = True
    payload["lead_channel_id"] = None
    payload["redirect_url"] = ""
    payload["completion_target"] = {
        "enabled": False,
        "target_type": "h5",
        "open_strategy": "h5_redirect",
        "h5_url": "",
        "fallback_url": "",
        "mini_program": {"appid": "", "username": "", "path": "", "query": "", "env_version": "release"},
        "url_link": {"enabled": False, "url": "", "source_url": "", "response_url_key": "url_link", "expire_type": 0, "expire_interval": 30},
    }
    payload["completion_target_json"] = dict(payload["completion_target"])
    payload["external_push_config"] = {
        "enabled": False,
        "webhook_url": "",
        "type": "",
        "expires_at_ts": None,
        "day": None,
        "frequency": None,
        "remark": "",
        "custom_params": [],
    }
    for key, value in {
        "external_push_enabled": False,
        "external_push_url": "",
        "external_push_type": "",
        "external_push_expires_at_ts": None,
        "external_push_day": None,
        "external_push_frequency": None,
        "external_push_remark": "",
        "external_push_custom_params": [],
    }.items():
        payload[key] = value
    item = _repo().save_questionnaire(payload)
    response = admin_detail_projection(item)
    response.update(
        {
            "questionnaire_id": int(item["id"]),
            "source_questionnaire_id": questionnaire_id,
            "write_model_status": "duplicated",
        }
    )
    return response


def _handle_publish(command: Command) -> dict[str, Any]:
    questionnaire_id = int(command.payload["questionnaire_id"])
    item = QuestionnaireAdminRepository().set_enabled(questionnaire_id, True)
    if not item:
        raise NotFoundError("questionnaire not found")
    plan = QuestionnaireExternalPushConfigPlanner().publish_plan(command, questionnaire_id)
    return {
        "questionnaire_id": questionnaire_id,
        "questionnaire": summary_projection(item),
        "write_model_status": "published",
        "side_effect_plan": _plan_response(plan),
    }


def _handle_enable(command: Command) -> dict[str, Any]:
    return _set_enabled(command, enabled=True, status="enabled")


def _handle_disable(command: Command) -> dict[str, Any]:
    return _set_enabled(command, enabled=False, status="disabled")


def _set_enabled(command: Command, *, enabled: bool, status: str) -> dict[str, Any]:
    questionnaire_id = int(command.payload["questionnaire_id"])
    item = QuestionnaireAdminRepository().set_enabled(questionnaire_id, enabled)
    if not item:
        raise NotFoundError("questionnaire not found")
    return {
        "questionnaire_id": questionnaire_id,
        "questionnaire": summary_projection(item),
        "write_model_status": status,
    }


def _handle_delete(command: Command) -> dict[str, Any]:
    questionnaire_id = int(command.payload["questionnaire_id"])
    admin_repo = QuestionnaireAdminRepository()
    existing = admin_repo.get_questionnaire(questionnaire_id)
    if not existing:
        raise NotFoundError("questionnaire not found")
    existing_disabled = bool(existing.get("is_disabled")) or not bool(existing.get("enabled", True))
    if not existing_disabled:
        raise ContractError("questionnaire must be disabled before deletion")
    deleted = admin_repo.delete_questionnaire(questionnaire_id)
    if not deleted:
        raise NotFoundError("questionnaire not found")
    return {
        "questionnaire_id": questionnaire_id,
        "questionnaire": summary_projection(existing),
        "deleted": True,
        "delete_mode": "hard_delete",
        "write_model_status": "deleted",
    }


def _handle_export_preview(command: Command) -> dict[str, Any]:
    questionnaire_id = int(command.payload["questionnaire_id"])
    requested = dict(command.payload.get("payload") or {})
    fields = [str(item) for item in requested.get("fields") or ["submission_id", "external_userid", "answers", "created_at"]]
    result = QuestionnaireAdminRepository().list_submissions(questionnaire_id, limit=3, offset=0)
    if result is None:
        raise NotFoundError("questionnaire not found")
    submissions, total = result
    masked_sample = [_mask_submission(row, fields) for row in submissions]
    plan = QuestionnaireExternalPushConfigPlanner().export_preview_plan(command, questionnaire_id, fields, total)
    return {
        "questionnaire_id": questionnaire_id,
        "write_model_status": "export_preview_planned",
        "export_preview": {
            "fields": fields,
            "estimated_count": total,
            "masked_sample": masked_sample,
            "file_created": False,
        },
        "side_effect_plan": _plan_response(plan),
    }


def _handle_export_download(command: Command) -> dict[str, Any]:
    questionnaire_id = int(command.payload["questionnaire_id"])
    item = QuestionnaireAdminRepository().get_questionnaire(questionnaire_id)
    if not item:
        raise NotFoundError("questionnaire not found")
    requested = dict(command.payload.get("payload") or {})
    fields = [
        str(item)
        for item in requested.get("fields")
        or ["submission_id", "submitted_at", "external_userid", "mobile", "matched_by", "score", "final_tags", "answers"]
    ]
    limit = int(requested.get("limit") or 10000)
    limit = max(1, min(limit, 50000))
    result = QuestionnaireAdminRepository().list_submissions(questionnaire_id, limit=limit, offset=0)
    if result is None:
        raise NotFoundError("questionnaire not found")
    submissions, total = result
    fields, rows = _export_rows(item, submissions, requested_fields=fields)
    plan = QuestionnaireExternalPushConfigPlanner().export_download_plan(command, questionnaire_id, fields, total)
    slug = str(item.get("slug") or f"questionnaire-{questionnaire_id}").strip()
    return {
        "questionnaire_id": questionnaire_id,
        "write_model_status": "export_download_ready",
        "export_download": {
            "fields": fields,
            "rows": rows,
            "total": total,
            "returned_count": len(rows),
            "filename": f"questionnaire-{slug}-submissions.csv",
            "file_created": False,
        },
        "side_effect_plan": _plan_response(plan),
    }


def _mask_submission(row: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field_name in fields:
        value = row.get(field_name)
        if field_name in {"mobile", "external_userid", "openid", "unionid", "respondent_key"} and value:
            payload[field_name] = "masked"
        else:
            payload[field_name] = value
    return payload


def _export_rows(
    questionnaire: dict[str, Any],
    submissions: list[dict[str, Any]],
    *,
    requested_fields: list[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    question_headers = _export_question_headers(questionnaire, submissions)
    requested = [field_name for field_name in requested_fields if field_name and field_name not in {"matched_by", "answers"}]
    base_fields = requested or list(_EXPORT_BASE_FIELDS)
    for field_name in _EXPORT_BASE_FIELDS:
        if field_name not in base_fields and field_name == "unionid":
            insert_at = base_fields.index("mobile") if "mobile" in base_fields else len(base_fields)
            base_fields.insert(insert_at, field_name)
    fields = [*base_fields, *[header for _, header in question_headers]]
    rows: list[dict[str, Any]] = []
    for submission in submissions:
        row = {field: _export_base_value(submission, field) for field in base_fields}
        answers_by_question = _export_answers_by_question(submission)
        for question_id, header in question_headers:
            row[header] = answers_by_question.get(question_id, "")
        rows.append(row)
    return fields, rows


def _export_base_value(submission: dict[str, Any], field: str) -> Any:
    if field == "submitted_at":
        return _format_beijing_time(submission.get("submitted_at") or submission.get("created_at"))
    if field == "final_tags":
        tags = submission.get("final_tags")
        if isinstance(tags, list):
            return "、".join(str(item) for item in tags if item not in (None, ""))
        return tags or ""
    value = submission.get(field)
    return "" if value is None else value


def _export_question_headers(questionnaire: dict[str, Any], submissions: list[dict[str, Any]]) -> list[tuple[str, str]]:
    title_by_id: dict[str, str] = {}
    for question in questionnaire.get("questions") or []:
        if not isinstance(question, dict):
            continue
        question_id = str(question.get("id") or "").strip()
        title = str(question.get("title") or "").strip()
        if question_id and title and question_id not in title_by_id:
            title_by_id[question_id] = title
    for submission in submissions:
        for answer in submission.get("answer_snapshots") or []:
            if not isinstance(answer, dict):
                continue
            question_id = str(answer.get("question_id") or "").strip()
            title = str(answer.get("question_title_snapshot") or "").strip()
            if question_id and title and question_id not in title_by_id:
                title_by_id[question_id] = title
    seen_headers: dict[str, int] = {}
    headers: list[tuple[str, str]] = []
    for question_id, title in title_by_id.items():
        count = seen_headers.get(title, 0) + 1
        seen_headers[title] = count
        headers.append((question_id, title if count == 1 else f"{title} ({count})"))
    return headers


def _export_answers_by_question(submission: dict[str, Any]) -> dict[str, str]:
    answers: dict[str, str] = {}
    for answer in submission.get("answer_snapshots") or []:
        if not isinstance(answer, dict):
            continue
        question_id = str(answer.get("question_id") or "").strip()
        if not question_id:
            continue
        answers[question_id] = _export_answer_value(answer)
    return answers


def _export_answer_value(answer: dict[str, Any]) -> str:
    question_type = str(answer.get("question_type") or "").strip()
    text_value = str(answer.get("text_value") or "").strip()
    if question_type in {"textarea", "mobile"}:
        return text_value
    option_texts = [str(item).strip() for item in answer.get("selected_option_texts_snapshot") or [] if str(item).strip()]
    if text_value and option_texts:
        option_texts[-1] = f"{option_texts[-1]}：{text_value}"
    elif text_value:
        option_texts.append(text_value)
    return "、".join(option_texts)


def _format_beijing_time(value: Any) -> str:
    if not value:
        return ""
    parsed: datetime | None = value if isinstance(value, datetime) else None
    if parsed is None:
        raw = str(value).strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return _strip_timezone_text(raw)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(_BEIJING_TZ)
    return parsed.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _strip_timezone_text(value: str) -> str:
    text = value.replace("T", " ").strip()
    if "." in text:
        text = text.split(".", 1)[0]
    for marker in ("+", "Z"):
        if marker in text:
            text = text.split(marker, 1)[0].strip()
    return text


def _create_side_effect_plan(
    *,
    command: Command,
    effect_type: str,
    adapter_name: str,
    target_id: str,
    payload_summary: dict[str, Any],
    risk_level: str,
) -> SideEffectPlan:
    return _side_effect_plans.create_plan(
        command_id=command.command_id,
        effect_type=effect_type,
        adapter_name=adapter_name,
        adapter_mode="real_blocked",
        target_type="questionnaire",
        target_id=target_id,
        payload={
            "payload_summary": payload_summary,
            "real_external_call_executed": False,
        },
        status="planned",
        risk_level=risk_level,
        requires_approval=True,
    )


def _plan_response(plan: SideEffectPlan) -> dict[str, Any]:
    payload = plan.to_dict()
    summary = dict(payload.pop("payload") or {})
    payload["payload_summary"] = summary.get("payload_summary") or {}
    payload["real_external_call_executed"] = False
    return payload


def _response_from_result(result: CommandResult, payload: dict[str, Any]) -> dict[str, Any]:
    response = {
        "ok": result.status in {"completed", "dry_run"},
        "command_id": result.command_id,
        "command_name": result.command_name,
        "questionnaire_id": int(payload.get("questionnaire_id") or 0),
        "idempotency_key": result.idempotency_key,
        "source_status": "next_command",
        "write_model_status": payload.get("write_model_status") or "updated",
        "route_owner": "ai_crm_next",
        "fallback_used": False,
        "real_external_call_executed": False,
        "audit_recorded": True,
        "command_result_status": result.status,
    }
    response.update(payload)
    return response


reset_questionnaire_admin_write_fixture_state()
