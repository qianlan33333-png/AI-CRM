from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from .automation_binding.repository import BindingRepositoryError
from .repository import AudienceRepository, build_audience_repository, _text
from .repository_packages import ActivePackageTemplateUpdateError, ArchivedPackageTemplateUpdateError
from .schemas import TemplatePackageRequest
from .service import refresh_mode_config
from .simple_sql import compile_simple_sql, validate_simple_sql
from .template_registry import AudienceTemplate, get_template, template_catalog_payload


TEMPLATE_ERROR_CODES = frozenset(
    {
        "invalid_request",
        "unsafe_package_key_prefix",
        "template_not_found",
        "template_version_not_found",
        "unknown_parameter",
        "parameter_required",
        "invalid_parameter_type",
        "invalid_parameter_value",
        "owner_scope_required",
        "owner_userids_required",
        "invalid_time_window",
        "reference_not_found",
        "reference_ambiguous",
        "question_type_not_supported",
        "group_not_found",
        "automation_not_found",
        "automation_not_active",
        "automation_already_bound",
        "invalid_sender_status",
        "preview_failed",
        "preview_timeout",
        "empty_audience_requires_confirmation",
        "active_package_update_requires_pause",
        "archived_package_cannot_update",
        "template_sql_validation_failed",
        "apply_failed",
    }
)


class TemplateConfigError(RuntimeError):
    def __init__(self, code: str, **details: Any) -> None:
        super().__init__(code)
        self.code = code
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": self.code, **self.details}


class AudienceTemplateService:
    def __init__(self, repository: AudienceRepository | None = None) -> None:
        self._repo = repository or build_audience_repository()

    @staticmethod
    def list_templates() -> dict[str, Any]:
        return template_catalog_payload()

    def preview(self, raw_payload: dict[str, Any], *, prefix_gate: Any | None = None) -> dict[str, Any]:
        try:
            prepared = self._prepare(raw_payload, prefix_gate=prefix_gate)
            rows = self._repo.execute_readonly_query(
                prepared["compiled_sql"],
                {
                    **prepared["execution_parameters"],
                    "package_key": prepared["request"].package_key,
                    "package_id": 0,
                    "refresh_started_at": datetime.now(timezone.utc).isoformat(),
                    "last_watermark_at": "1970-01-01T00:00:00+00:00",
                    "lookback_seconds": 600,
                },
                limit=10001,
                timeout_seconds=10,
            )
        except TemplateConfigError as exc:
            return exc.to_dict()
        except Exception as exc:
            message = _text(exc).lower()
            code = "preview_timeout" if "statement timeout" in message or "query canceled" in message else "preview_failed"
            return {"ok": False, "error": code, "risk_warnings": [code]}
        count_capped = len(rows) > 10000
        count = min(len(rows), 10000)
        result = self._preview_payload(prepared, rows=rows[:10], count=count, count_capped=count_capped)
        return {"ok": True, **result}

    def apply(self, raw_payload: dict[str, Any], *, prefix_gate: Any | None = None) -> dict[str, Any]:
        preview = self.preview(raw_payload, prefix_gate=prefix_gate)
        if not preview.get("ok"):
            return {**preview, "applied": False}
        if int(preview.get("matched_count") or 0) == 0 and not bool(raw_payload.get("allow_empty")):
            return {
                **preview,
                "ok": False,
                "error": "empty_audience_requires_confirmation",
                "applied": False,
            }
        try:
            prepared = self._prepare(raw_payload, prefix_gate=prefix_gate)
            result = self._repo.apply_template_package(
                {
                    "package_key": prepared["request"].package_key,
                    "name": prepared["request"].name,
                    "template_key": prepared["template"].key,
                    "template_version": prepared["template"].version,
                    "template_parameters": prepared["normalized_parameters"],
                    "template_fingerprint": prepared["fingerprint"],
                    "natural_language_definition": prepared["natural_language_rule"],
                    "compiled_sql": prepared["compiled_sql"],
                    "execution_parameters": prepared["execution_parameters"],
                    "dependencies": list(prepared["template"].dependencies),
                    "refresh_mode": prepared["refresh_mode"],
                    "refresh_config": prepared["refresh_config"],
                    "senders": [item.model_dump() for item in prepared["request"].senders],
                    "group_id": prepared["group_id"],
                    "automation_agent_code": _text(prepared["request"].automation_agent_code),
                    "operator": _text(prepared["request"].operator) or "external",
                }
            )
        except ActivePackageTemplateUpdateError:
            return {**preview, "ok": False, "error": "active_package_update_requires_pause", "applied": False}
        except ArchivedPackageTemplateUpdateError:
            return {**preview, "ok": False, "error": "archived_package_cannot_update", "applied": False}
        except BindingRepositoryError as exc:
            return {**preview, "ok": False, "error": exc.code, "applied": False}
        except TemplateConfigError as exc:
            return {**preview, **exc.to_dict(), "applied": False}
        except Exception:
            return {**preview, "ok": False, "error": "apply_failed", "applied": False}
        package = dict(result.get("package") or {})
        version = dict(result.get("version") or {})
        return {
            **preview,
            "ok": True,
            "applied": True,
            "package_id": int(package.get("id") or 0),
            "version_id": int(version.get("id") or 0),
            "status": "paused",
            "created": bool(result.get("created")),
            "updated": bool(result.get("updated")),
            "idempotent": bool(result.get("idempotent")),
            "published": True,
        }

    def _prepare(self, raw_payload: dict[str, Any], *, prefix_gate: Any | None) -> dict[str, Any]:
        try:
            request = TemplatePackageRequest(**dict(raw_payload or {}))
        except ValidationError as exc:
            raise TemplateConfigError("invalid_request", validation_errors=exc.errors(include_url=False)) from exc
        request.package_key = _text(request.package_key)
        request.name = _text(request.name)
        if not request.package_key or not request.name:
            raise TemplateConfigError("invalid_request", validation_errors=["package_key_and_name_required"])
        if prefix_gate:
            prefix_error = prefix_gate(request.package_key)
            if prefix_error:
                raise TemplateConfigError(prefix_error, package_key=request.package_key)
        template = get_template(request.template_key, request.template_version)
        if not template:
            if get_template(request.template_key):
                raise TemplateConfigError("template_version_not_found", template_key=request.template_key)
            raise TemplateConfigError("template_not_found", template_key=request.template_key)
        normalized = self._normalize_parameters(template, request.parameters)
        refresh_mode = _text(request.refresh_mode) or template.default_refresh_mode
        refresh_config = _template_refresh_config(refresh_mode)
        if not refresh_config:
            raise TemplateConfigError(
                "invalid_parameter_value",
                field="refresh_mode",
                allowed=["manual", "every_3m", "daily_0200", "every_3m_plus_daily_0200"],
            )
        group_id = None
        if _text(request.group_name):
            group = self._resolve_reference("group", request.group_name)
            group_id = int(group["id"])
        if _text(request.automation_agent_code):
            automation = self._resolve_reference("automation", request.automation_agent_code)
            if _text(automation.get("status")) != "active" and _text(automation.get("bound_package_key")) != request.package_key:
                raise TemplateConfigError("automation_not_active", automation_agent_code=request.automation_agent_code)
            bound_key = _text(automation.get("bound_package_key"))
            if bound_key and bound_key != request.package_key:
                raise TemplateConfigError("automation_already_bound", bound_package_key=bound_key)
        senders = [item.model_dump() for item in request.senders]
        if any(_text(item.get("status")) not in {"active", "paused"} for item in senders):
            raise TemplateConfigError("invalid_sender_status")
        sender_ids = [_text(item.get("sender_userid")) for item in senders]
        if any(not item for item in sender_ids) or len(sender_ids) != len(set(sender_ids)):
            raise TemplateConfigError("invalid_request", validation_errors=["sender_userid_required_and_unique"])
        sql, execution_parameters = template.compiler(normalized)
        validation = validate_simple_sql(sql, execution_parameters)
        if not validation.ok:
            raise TemplateConfigError("template_sql_validation_failed", validation_errors=validation.errors)
        compiled_sql = compile_simple_sql(sql)
        natural_language_rule = template.explainer(normalized)
        fingerprint_payload = {
            "name": request.name,
            "template_key": template.key,
            "template_version": template.version,
            "parameters": normalized,
            "refresh_mode": refresh_mode,
            "senders": senders,
            "group_id": group_id,
            "automation_agent_code": _text(request.automation_agent_code),
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        return {
            "request": request,
            "template": template,
            "normalized_parameters": normalized,
            "refresh_mode": refresh_mode,
            "refresh_config": refresh_config,
            "group_id": group_id,
            "natural_language_rule": natural_language_rule,
            "compiled_sql": compiled_sql,
            "execution_parameters": execution_parameters,
            "fingerprint": fingerprint,
        }

    def _normalize_parameters(self, template: AudienceTemplate, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise TemplateConfigError("invalid_parameter_type", field="parameters", expected="object")
        fields = {str(field["name"]): dict(field) for field in template.fields}
        unknown = sorted(set(raw) - set(fields))
        if unknown:
            raise TemplateConfigError("unknown_parameter", fields=unknown)
        normalized: dict[str, Any] = {}
        for name, field in fields.items():
            value = raw.get(name, field.get("default"))
            if field.get("required") and (value is None or value == "" or value == []):
                code = "owner_scope_required" if name == "owner_scope" else "parameter_required"
                raise TemplateConfigError(code, field=name)
            normalized[name] = self._normalize_field(name, field, value)
        if normalized.get("owner_scope") == "specified" and not normalized.get("owner_userids"):
            raise TemplateConfigError("owner_userids_required", field="owner_userids")
        if normalized.get("owner_scope") == "all":
            normalized["owner_userids"] = []
        for minimum_name, maximum_name in (("entered_days_min", "entered_days_max"), ("elapsed_min", "elapsed_max")):
            if minimum_name in normalized and normalized.get(maximum_name) is not None:
                if int(normalized[maximum_name]) <= int(normalized[minimum_name]):
                    raise TemplateConfigError("invalid_time_window", min_field=minimum_name, max_field=maximum_name)
        if normalized.get("paid_at_from") and normalized.get("paid_at_to"):
            if _parse_datetime(normalized["paid_at_to"]) <= _parse_datetime(normalized["paid_at_from"]):
                raise TemplateConfigError("invalid_time_window", min_field="paid_at_from", max_field="paid_at_to")
        return self._resolve_parameter_references(template, normalized)

    def _normalize_field(self, name: str, field: dict[str, Any], value: Any) -> Any:
        field_type = field.get("type")
        if value is None and not field.get("required"):
            return None
        if field_type in {"string_list", "reference_list", "enum_list"}:
            if not isinstance(value, list):
                raise TemplateConfigError("invalid_parameter_type", field=name, expected="array")
            items = [_text(item) for item in value if _text(item)]
            if field.get("min_items") and len(items) < int(field["min_items"]):
                raise TemplateConfigError("parameter_required", field=name)
            if field_type == "enum_list" and any(item not in field.get("enum", []) for item in items):
                raise TemplateConfigError("invalid_parameter_value", field=name, allowed=field.get("enum", []))
            return list(dict.fromkeys(items))
        if field_type == "condition_list":
            if not isinstance(value, list) or not value:
                raise TemplateConfigError("invalid_parameter_type", field=name, expected="non_empty_array")
            conditions: list[dict[str, Any]] = []
            for index, item in enumerate(value):
                if not isinstance(item, dict) or set(item) - {"question", "options"}:
                    raise TemplateConfigError("invalid_parameter_type", field=f"conditions[{index}]", expected="question_and_options")
                question = item.get("question")
                options = item.get("options")
                if question in {None, ""} or not isinstance(options, list) or not options:
                    raise TemplateConfigError("parameter_required", field=f"conditions[{index}]")
                conditions.append({"question": question, "options": options})
            return conditions
        if field_type == "boolean":
            if not isinstance(value, bool):
                raise TemplateConfigError("invalid_parameter_type", field=name, expected="boolean")
            return value
        if field_type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise TemplateConfigError("invalid_parameter_type", field=name, expected="integer")
            if field.get("minimum") is not None and value < int(field["minimum"]):
                raise TemplateConfigError("invalid_parameter_value", field=name, minimum=field["minimum"])
            return value
        if field_type == "enum":
            value = _text(value)
            if value not in field.get("enum", []):
                raise TemplateConfigError("invalid_parameter_value", field=name, allowed=field.get("enum", []))
            return value
        if field_type == "datetime":
            if not value:
                return None
            return _parse_datetime(value).isoformat()
        if field_type == "reference":
            if isinstance(value, (str, int)) and _text(value):
                return value
            raise TemplateConfigError("invalid_parameter_type", field=name, expected="reference")
        return value

    def _resolve_parameter_references(self, template: AudienceTemplate, parameters: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(parameters)
        if template.key == "questionnaire_choice_answers":
            questionnaire = self._resolve_reference("questionnaire", normalized.pop("questionnaire"))
            normalized["questionnaire_id"] = int(questionnaire["id"])
            normalized["questionnaire_title"] = _text(questionnaire.get("title"))
            resolved_conditions: list[dict[str, Any]] = []
            for condition in normalized["conditions"]:
                question = self._resolve_reference("question", condition["question"], parent_id=int(questionnaire["id"]))
                if _text(question.get("type")) not in {"single_choice", "multi_choice"}:
                    raise TemplateConfigError(
                        "question_type_not_supported",
                        question_id=int(question["id"]),
                        question_type=_text(question.get("type")),
                    )
                options = [
                    self._resolve_reference("option", option, parent_id=int(question["id"]))
                    for option in condition["options"]
                ]
                resolved_conditions.append(
                    {
                        "question_id": int(question["id"]),
                        "question_title": _text(question.get("title")),
                        "option_ids": [int(option["id"]) for option in options],
                        "option_texts": [_text(option.get("title")) for option in options],
                    }
                )
            normalized["conditions"] = resolved_conditions
        elif template.key == "paid_order":
            products = [self._resolve_reference("product", item) for item in normalized.pop("products")]
            normalized["product_codes"] = [_text(item.get("code")) for item in products]
            normalized["product_names"] = [_text(item.get("title")) for item in products]
        elif template.key == "channel_entry":
            channels = [self._resolve_reference("channel", item) for item in normalized.pop("channels")]
            normalized["channel_codes"] = [_text(item.get("code")) for item in channels]
            normalized["channel_names"] = [_text(item.get("title")) for item in channels]
        elif template.key == "radar_first_click_elapsed":
            radars = [self._resolve_reference("radar", item) for item in normalized.pop("radars")]
            normalized["radar_ids"] = [int(item["id"]) for item in radars]
            normalized["radar_titles"] = [_text(item.get("title")) for item in radars]
        return normalized

    def _resolve_reference(self, reference_type: str, value: Any, *, parent_id: int | None = None) -> dict[str, Any]:
        candidates = self._repo.resolve_template_reference(reference_type, value, parent_id=parent_id)
        public_candidates = [
            {key: item.get(key) for key in ("id", "code", "title", "type", "status") if item.get(key) not in {None, ""}}
            for item in candidates[:10]
        ]
        if not candidates:
            code = "group_not_found" if reference_type == "group" else "automation_not_found" if reference_type == "automation" else "reference_not_found"
            raise TemplateConfigError(code, reference_type=reference_type, reference=value, candidates=[])
        if len(candidates) > 1:
            raise TemplateConfigError(
                "reference_ambiguous",
                reference_type=reference_type,
                reference=value,
                candidates=public_candidates,
            )
        return candidates[0]

    @staticmethod
    def _preview_payload(
        prepared: dict[str, Any],
        *,
        rows: list[dict[str, Any]],
        count: int,
        count_capped: bool,
    ) -> dict[str, Any]:
        template: AudienceTemplate = prepared["template"]
        warnings: list[str] = []
        if count == 0:
            warnings.append("empty_audience")
        if count_capped:
            warnings.append("count_capped_at_10000")
        samples = []
        for row in rows[:10]:
            value = _text(row.get("external_userid") or row.get("identity_value"))
            samples.append({"masked_identity": f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"})
        return {
            "mode": "template_preview",
            "package_key": prepared["request"].package_key,
            "template_key": template.key,
            "template_version": template.version,
            "normalized_parameters": prepared["normalized_parameters"],
            "natural_language_rule": prepared["natural_language_rule"],
            "dependencies": list(template.dependencies),
            "refresh_mode": prepared["refresh_mode"],
            "matched_count": count,
            "matched_count_is_lower_bound": count_capped,
            "matched_count_display": "至少 10,000 人" if count_capped else f"{count} 人",
            "sample_rows": samples,
            "risk_warnings": warnings,
            "template_fingerprint": prepared["fingerprint"],
        }


def _parse_datetime(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise TemplateConfigError("invalid_parameter_value", value=value, expected="ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _template_refresh_config(refresh_mode: str) -> dict[str, Any] | None:
    admin_mode = {
        "every_3m": "incremental_3m",
        "every_3m_plus_daily_0200": "incremental_3m_plus_daily_0200",
    }.get(refresh_mode, refresh_mode)
    return refresh_mode_config(admin_mode)


__all__ = ["AudienceTemplateService", "TEMPLATE_ERROR_CODES", "TemplateConfigError"]
