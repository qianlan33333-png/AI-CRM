# ruff: noqa: F401
from __future__ import annotations

from aicrm_next.extensions.commerce.service_period_grid_ports import SERVICE_PERIOD_GRID_COLLABORATOR_ROLE
from aicrm_next.platform.shared.runtime_settings import managed_runtime_setting
from aicrm_next.platform.shared.wecom_runtime import (
    WECOM_ENABLED_EFFECT_TYPES_KEY,
    WECOM_EXECUTION_MODE_KEY,
    load_wecom_execution_config,
)

from .application_support import (
    ADMIN_ASSIGNABLE_ROLE_OPTIONS,
    ADMIN_LEVEL_LABELS,
    APP_SETTING_DEFINITIONS,
    AdminConfigRepository,
    Any,
    CHANNEL_ENTRY_REALTIME_EFFECT_TYPES,
    CONFIG_CATEGORIES,
    CONFIG_SCHEMA,
    ConfigCategory,
    ConfigCategoryField,
    DEFAULT_MARKETING_AUTOMATION_NAME,
    DEFAULT_MARKETING_CHANNEL_TYPE,
    DEFAULT_MARKETING_CORE_THRESHOLD,
    DEFAULT_MARKETING_DAY_START_HOUR,
    DEFAULT_MARKETING_QUIET_HOUR_START,
    DEFAULT_MARKETING_TARGET_EVENT,
    DEFAULT_MARKETING_TIMEZONE,
    DEFAULT_MARKETING_TOP_THRESHOLD,
    DEFAULT_SIGNUP_CONVERSION_KEY,
    DEFAULT_SILENT_THRESHOLD_DAYS_BY_POOL,
    EXTRA_SETTING_DEFINITIONS,
    HTTP_URL_SETTING_KEYS,
    JSON_SETTING_KEYS,
    MCP_TOOL_GROUP_LABELS,
    PUSH_CAPABILITY_ADVANCED_KEYS,
    PushCapability,
    REALTIME_ALLOWED_TYPES_KEY,
    REALTIME_ENABLED_KEY,
    REALTIME_MAX_CONCURRENCY_KEY,
    ROLE_LABELS,
    SCHEDULER_BATCH_SIZE_KEY,
    SCHEDULER_ENABLED_KEY,
    SCHEDULER_INTERVAL_SECONDS_KEY,
    SENSITIVE_KEYS,
    TARGET_ADMIN_USER,
    TARGET_APP_SETTING,
    TARGET_CONFIG_CATEGORY_ENABLED,
    TARGET_MARKETING_AUTOMATION_CONFIG,
    TARGET_MCP_TOOL_SETTING,
    TARGET_PUSH_CAPABILITY,
    WECOM_MESSAGE_PRIVATE_SEND,
    ZoneInfo,
    ZoneInfoNotFoundError,
    _audit_action_label,
    _bool,
    _bounded_int,
    _capability_enabled_from_value,
    _capability_requires_webhook_gate,
    _default_display_name,
    _default_mcp_tool_defs,
    _default_tool_description,
    _default_tool_group,
    _derived_gate_payload,
    _effect_type_union_for_enabled_capabilities,
    _filter_text_match,
    _input_type_for_schema_type,
    _is_boolean_setting,
    _is_integer_setting,
    _json_loads,
    _metadata_for_setting,
    _normalize_boolean_text,
    _normalize_int,
    _normalize_silent_thresholds,
    _normalize_timezone,
    _positive_int,
    _scheduler_state_for_read_service,
    _schema_setting_metadata,
    _setting_metadata_map,
    _text,
    _tool_group_label,
    _validate_category_setting,
    _validate_known_setting,
    build_config_checklist,
    current_setting_values,
    ensure_admin_action_token,
    get_config_category,
    get_push_capability,
    json,
    mask_value,
    public_changed_row,
    setting_details,
    stored_value_matches,
    validate_config,
    visible_push_capabilities,
)

class AdminConfigReadService:
    def __init__(self, repo: AdminConfigRepository | None = None) -> None:
        self.repo = repo or AdminConfigRepository()

    def config_tabs(self, active_key: str) -> list[dict[str, Any]]:
        items = [
            {"key": "overview", "label": "概览", "href": "/admin/config"},
            {"key": "app_settings", "label": "系统设置", "href": "/admin/config/app-settings"},
            {"key": "releases", "label": "配置发布", "href": "/admin/config/releases"},
            {"key": "login_access", "label": "后台访问", "href": "/admin/config/detail/admin_access"},
            {"key": "checklist", "label": "配置检查清单", "href": "/admin/config/checklist"},
        ]
        return [{**item, "active": item["key"] == active_key} for item in items]

    def _setting_value_source(self, key: str) -> tuple[str, str]:
        value, source, _version, _updated_at = setting_details(self.repo, key)
        return value, source

    def _current_setting_values(self) -> dict[str, str]:
        return current_setting_values(self, CONFIG_SCHEMA)

    def _audit_meta_map(self, target_ids: list[str]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for target_id, row in self.repo.latest_audit_map(target_type=TARGET_APP_SETTING, target_ids=target_ids).items():
            result[target_id] = {
                "last_modified_at": _text(row.get("created_at")),
                "last_modified_by": _text(row.get("operator")),
                "last_action_type": _text(row.get("action_type")),
            }
        return result

    def _recent_audit_entries(self, target_type: str, limit: int = 8) -> list[dict[str, str]]:
        return [
            {
                "id": _text(row.get("id")),
                "operator": _text(row.get("operator")),
                "action_type": _text(row.get("action_type")),
                "target_id": _text(row.get("target_id")),
                "created_at": _text(row.get("created_at")),
            }
            for row in self.repo.list_audit_logs(target_type=target_type, limit=limit)
        ]

    def ensure_mcp_tool_settings_seed(self) -> None:
        existing = {item["tool_name"]: item for item in self.repo.list_mcp_tool_settings()}
        for index, tool in enumerate(_default_mcp_tool_defs()):
            tool_name = _text(tool.get("name"))
            if not tool_name or tool_name in existing:
                continue
            self.repo.upsert_mcp_tool_setting(
                tool_name=tool_name,
                tool_group=_default_tool_group(tool_name),
                display_name=_default_display_name(tool_name),
                description_override="",
                enabled=True,
                visible_in_console=True,
                show_sample_args=False,
                show_sample_output=False,
                sort_order=index,
            )

    def build_home_payload(self) -> dict[str, Any]:
        categories = self.list_config_categories()["rows"]
        return {
            "cards": [
                {
                    "label": row["label"],
                    "value": row["status_label"],
                    "description": row["group_label"],
                    "href": row["detail_href"],
                    "key": row["key"],
                    "enabled": row["enabled"],
                }
                for row in categories
            ],
            "categories": categories,
        }

    def list_app_settings(self, *, query: str, scope: str) -> dict[str, Any]:
        definitions = [dict(item) for item in APP_SETTING_DEFINITIONS]
        metadata = {item["key"]: dict(item) for item in definitions}
        audit_map = self._audit_meta_map(list(metadata.keys()))
        rows: list[dict[str, Any]] = []
        for item in definitions:
            value, source, version, updated_at = setting_details(self.repo, item["key"])
            display_value = mask_value(item["key"], value) if item["mode"] == "masked" else value
            row = {
                **item,
                "value": value if item["mode"] == "editable" else "",
                "display_value": display_value,
                "configured": bool(value),
                "source": source,
                "version": version,
                "updated_at": updated_at,
            }
            row.update(audit_map.get(item["key"], {"last_modified_at": "", "last_modified_by": "", "last_action_type": ""}))
            if scope and row["mode"] != scope:
                continue
            if not _filter_text_match(row, ["key", "label", "description"], query):
                continue
            rows.append(row)
        editable_count = sum(1 for row in rows if row["mode"] == "editable")
        masked_count = sum(1 for row in rows if row["mode"] == "masked")
        configured_count = sum(1 for row in rows if row["configured"])
        return {
            "rows": rows,
            "metadata_map": metadata,
            "summary_cards": [
                {"label": "可直接编辑", "value": editable_count, "description": "可以直接修改的设置项"},
                {"label": "敏感信息", "value": masked_count, "description": "只显示掩码的设置项"},
                {"label": "已配置", "value": configured_count, "description": "当前已经配置完成的设置项"},
            ],
            "audit_entries": self._recent_audit_entries(TARGET_APP_SETTING, limit=10),
        }

    def _category_enabled(self, category: ConfigCategory) -> bool:
        value, _source = self._setting_value_source(category.enabled_key)
        if not value and category.enabled_key.startswith("CONFIG_CATEGORY_"):
            return True
        return _bool(value)

    def _serialize_category_summary(self, category: ConfigCategory) -> dict[str, Any]:
        enabled = self._category_enabled(category)
        return {
            "key": category.key,
            "label": category.label,
            "group_label": category.group_label,
            "enabled": enabled,
            "status_label": "已生效" if enabled else "未生效",
            "detail_href": category.detail_href,
            "check_supported": category.check_supported,
            "sort_order": category.sort_order,
        }

    def list_config_categories(self) -> dict[str, Any]:
        rows = [self._serialize_category_summary(category) for category in sorted(CONFIG_CATEGORIES, key=lambda item: item.sort_order)]
        return {"rows": rows}

    def _serialize_category_field(self, ref: ConfigCategoryField) -> dict[str, Any]:
        metadata = _metadata_for_setting(ref.key)
        value, source, version, updated_at = setting_details(self.repo, ref.key)
        sensitive = ref.key in SENSITIVE_KEYS or metadata.get("mode") == "masked" or metadata.get("type") == "secret"
        display_value = mask_value(ref.key, value) if sensitive else value
        return {
            **metadata,
            "key": ref.key,
            "value": "" if sensitive else value,
            "display_value": display_value,
            "configured": bool(value),
            "source": source,
            "version": version,
            "updated_at": updated_at,
            "sensitive": sensitive,
            "readonly": bool(ref.readonly),
            "block_title": ref.block_title,
        }

    def get_config_category_detail(self, category_key: str) -> dict[str, Any]:
        category = get_config_category(category_key)
        if not category:
            raise KeyError("config category not found")
        if category.key == "webhooks_push":
            return {
                "category": {
                    **self._serialize_category_summary(category),
                    "enabled_key": category.enabled_key,
                    "special_view": "push_capabilities",
                    "capabilities_api": "/api/admin/config/push-capabilities",
                },
                "blocks": [],
                "special_view": "push_capabilities",
            }
        blocks_by_title: dict[str, list[dict[str, Any]]] = {}
        for ref in category.fields:
            field_row = self._serialize_category_field(ref)
            blocks_by_title.setdefault(ref.block_title, []).append(field_row)
        return {
            "category": {
                **self._serialize_category_summary(category),
                "enabled_key": category.enabled_key,
            },
            "blocks": [{"title": title, "fields": fields} for title, fields in blocks_by_title.items()],
        }

    def _capability_enabled(self, capability: PushCapability, *, default: bool = False) -> bool:
        value, _source = self._setting_value_source(capability.setting_key)
        if _text(value):
            return _capability_enabled_from_value(value, default=default)
        gate_consistent, _problem = self._capability_gate_consistent(capability, configured_enabled=True)
        return gate_consistent

    def _capability_gate_consistent(self, capability: PushCapability, *, configured_enabled: bool) -> tuple[bool, str]:
        if not configured_enabled:
            return True, ""
        allowed_types = {
            item.strip() for item in _text(self._setting_value_source("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES")[0]).replace("\n", ",").split(",") if item.strip()
        }
        wecom_effect_types = {effect_type for effect_type in capability.effect_types if effect_type.startswith("wecom.")}
        missing_types = [effect_type for effect_type in capability.effect_types if effect_type not in wecom_effect_types and effect_type not in allowed_types]
        if missing_types:
            return False, "effect_type_allowlist_missing"
        if _capability_requires_webhook_gate(capability) and not self._capability_enabled_from_setting("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE"):
            return False, "webhook_execute_disabled"
        if wecom_effect_types:
            wecom_config = load_wecom_execution_config()
            if not wecom_config.real_calls_enabled:
                return False, "wecom_execute_disabled"
            if not wecom_effect_types.issubset(set(wecom_config.enabled_effect_types)):
                return False, "wecom_effect_type_allowlist_missing"
        if capability.adapter_family == "payment" and not self._capability_enabled_from_setting("AICRM_EXTERNAL_EFFECT_PAYMENT_EXECUTE"):
            return False, "payment_execute_disabled"
        if "feishu.webhook.notify" in set(capability.effect_types) and not self._capability_enabled_from_setting("AICRM_EXTERNAL_EFFECT_FEISHU_EXECUTE"):
            return False, "feishu_execute_disabled"
        if "openclaw.context.push" in set(capability.effect_types) and not self._capability_enabled_from_setting("AICRM_EXTERNAL_EFFECT_OPENCLAW_EXECUTE"):
            return False, "openclaw_execute_disabled"
        if {"media.storage.upload", "wecom.media.upload"} & set(capability.effect_types) and not self._capability_enabled_from_setting(
            "AICRM_EXTERNAL_EFFECT_MEDIA_UPLOAD_EXECUTE"
        ):
            return False, "media_upload_execute_disabled"
        if capability.key == "test_receiver" and not self._capability_enabled_from_setting("AICRM_EXTERNAL_EFFECT_TEST_RECEIVER_ENABLED"):
            return False, "test_receiver_disabled"
        return True, ""

    def _serialize_push_capability(self, capability: PushCapability) -> dict[str, Any]:
        configured_enabled = self._capability_enabled(capability, default=False)
        gate_consistent, gate_problem = self._capability_gate_consistent(capability, configured_enabled=configured_enabled)
        enabled = configured_enabled and gate_consistent
        return {
            **capability.to_dict(),
            "enabled": enabled if capability.toggleable else False,
            "configured_enabled": configured_enabled if capability.toggleable else False,
            "gate_consistent": gate_consistent,
            "gate_problem": gate_problem,
            "readonly_reason": capability.readonly_reason,
        }

    def _advanced_push_items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for public_key, setting_key, label in PUSH_CAPABILITY_ADVANCED_KEYS:
            metadata = _metadata_for_setting(setting_key)
            value, _source, version, updated_at = setting_details(self.repo, setting_key)
            sensitive = setting_key in SENSITIVE_KEYS or metadata.get("mode") == "masked" or metadata.get("type") == "secret"
            items.append(
                {
                    "key": public_key,
                    "label": label,
                    "configured": bool(value),
                    "sensitive": sensitive,
                    "display_value": mask_value(setting_key, value) if sensitive else value,
                    "version": version,
                    "updated_at": updated_at,
                }
            )
        return items

    def get_push_capabilities(self) -> dict[str, Any]:
        capabilities = [self._serialize_push_capability(item) for item in visible_push_capabilities(main_only=True)]
        enabled_count = sum(1 for item in capabilities if item["toggleable"] and item["enabled"])
        toggleable_count = sum(1 for item in capabilities if item["toggleable"])
        test_only = self._capability_enabled_from_setting("AICRM_EXTERNAL_EFFECT_TEST_EXECUTION_ONLY")
        scheduler = _scheduler_state_for_read_service(self)
        if test_only:
            global_status = "test_only"
        elif enabled_count == 0:
            global_status = "disabled"
        elif enabled_count == toggleable_count:
            global_status = "enabled"
        else:
            global_status = "partial"
        return {
            "ok": True,
            "summary": {
                "total": len(capabilities),
                "enabled_count": enabled_count,
                "toggleable_count": toggleable_count,
                "global_status": global_status,
            },
            "capabilities": capabilities,
            "scheduler": scheduler,
            "advanced": {"visible": False, "items": self._advanced_push_items()},
            "route_owner": "ai_crm_next",
            "real_external_call_executed": False,
        }

    def _capability_enabled_from_setting(self, key: str) -> bool:
        value, _source = self._setting_value_source(key)
        return _bool(value)

    def list_mcp_tool_settings(self, *, query: str, enabled_only: bool) -> dict[str, Any]:
        self.ensure_mcp_tool_settings_seed()
        defaults = {item["name"]: item for item in _default_mcp_tool_defs() if _text(item.get("name"))}
        audit_map = self._audit_meta_map_for_type(
            TARGET_MCP_TOOL_SETTING,
            [item["tool_name"] for item in self.repo.list_mcp_tool_settings()],
        )
        rows: list[dict[str, Any]] = []
        for item in self.repo.list_mcp_tool_settings():
            tool_name = _text(item.get("tool_name"))
            default = defaults.get(tool_name, {})
            tool_group = _text(item.get("tool_group")) or _default_tool_group(tool_name)
            raw_display_name = _text(item.get("display_name"))
            description_override = _text(item.get("description_override"))
            row = {
                "tool_name": tool_name,
                "tool_group": tool_group,
                "tool_group_label": _tool_group_label(tool_group),
                "display_name": raw_display_name or _default_display_name(tool_name),
                "description_override": description_override,
                "description": description_override or _default_tool_description(tool_name, _text(default.get("description"))),
                "enabled": _bool(item.get("enabled")),
                "visible_in_console": _bool(item.get("visible_in_console")),
                "show_sample_args": _bool(item.get("show_sample_args")),
                "show_sample_output": _bool(item.get("show_sample_output")),
                "sort_order": int(item.get("sort_order") or 0),
                "updated_at": _text(item.get("updated_at")),
            }
            row.update(audit_map.get(tool_name, {"last_modified_at": "", "last_modified_by": "", "last_action_type": ""}))
            if enabled_only and not row["enabled"]:
                continue
            if not _filter_text_match(row, ["tool_name", "tool_group", "display_name", "description"], query):
                continue
            rows.append(row)
        auth_value, auth_source = self._setting_value_source("AICRM_AUTH_MCP_CLIENT_ID")
        return {
            "rows": rows,
            "auth_configured": bool(auth_value),
            "auth_source": auth_source,
            "summary_cards": [
                {"label": "工具数量", "value": len(rows), "description": "当前可管理的 AI 工具数量"},
                {"label": "已启用", "value": sum(1 for row in rows if row["enabled"]), "description": "当前允许调用的工具数量"},
                {"label": "后台展示", "value": sum(1 for row in rows if row["visible_in_console"]), "description": "当前在后台显示的工具数量"},
                {"label": "OAuth 客户端", "value": "已配置" if auth_value else "未配置", "description": "AI 工具机器身份状态"},
            ],
            "audit_entries": [
                {**item, "action_label": _audit_action_label(item["action_type"])} for item in self._recent_audit_entries(TARGET_MCP_TOOL_SETTING, limit=8)
            ],
        }

    def _marketing_default_config(self, automation_key: str = DEFAULT_SIGNUP_CONVERSION_KEY) -> dict[str, Any]:
        return {
            "automation_key": _text(automation_key) or DEFAULT_SIGNUP_CONVERSION_KEY,
            "automation_name": DEFAULT_MARKETING_AUTOMATION_NAME,
            "target_event": DEFAULT_MARKETING_TARGET_EVENT,
            "channel_type": DEFAULT_MARKETING_CHANNEL_TYPE,
            "enabled": True,
            "questionnaire_id": None,
            "questionnaire_missing": False,
            "missing_questionnaire_id": None,
            "core_threshold": DEFAULT_MARKETING_CORE_THRESHOLD,
            "top_threshold": DEFAULT_MARKETING_TOP_THRESHOLD,
            "day_start_hour": DEFAULT_MARKETING_DAY_START_HOUR,
            "quiet_hour_start": DEFAULT_MARKETING_QUIET_HOUR_START,
            "timezone": DEFAULT_MARKETING_TIMEZONE,
            "silent_threshold_days_by_pool": dict(DEFAULT_SILENT_THRESHOLD_DAYS_BY_POOL),
            "question_rules": [],
            "configured": False,
            "created_at": "",
            "updated_at": "",
        }

    def _questionnaire_rule_context(self, questionnaire_id: int | None) -> tuple[dict[int, dict[str, Any]], dict[int, dict[int, dict[str, Any]]]]:
        if not questionnaire_id:
            return {}, {}
        questions = self.repo.list_questionnaire_questions(int(questionnaire_id))
        question_map = {int(item.get("id") or 0): dict(item) for item in questions}
        option_map: dict[int, dict[int, dict[str, Any]]] = {}
        for option in self.repo.list_questionnaire_options(list(question_map.keys())):
            question_id = int(option.get("question_id") or 0)
            option_id = int(option.get("id") or 0)
            if question_id and option_id:
                option_map.setdefault(question_id, {})[option_id] = dict(option)
        return question_map, option_map

    def _serialize_marketing_rule(
        self,
        row: dict[str, Any],
        *,
        question_map: dict[int, dict[str, Any]],
        option_map: dict[int, dict[int, dict[str, Any]]],
    ) -> dict[str, Any]:
        question_id = int(row.get("question_id") or row.get("questionnaire_question_id") or 0)
        hit_option_ids = [int(item) for item in _json_loads(row.get("answer_match_value_json") or row.get("hit_option_ids_json"), default=[]) if _text(item)]
        question = question_map.get(question_id, {})
        available_options = option_map.get(question_id, {})
        return {
            "id": int(row.get("id") or 0),
            "questionnaire_id": int(row.get("questionnaire_id") or 0) or None,
            "questionnaire_question_id": question_id,
            "question_title": _text(question.get("title")) or _text(row.get("rule_name")),
            "question_type": _text(question.get("type")),
            "hit_option_ids_json": hit_option_ids,
            "hit_options": [{"id": option_id, "option_text": _text(available_options.get(option_id, {}).get("option_text"))} for option_id in hit_option_ids],
            "sort_order": int(row.get("sort_order") or 0),
        }

    def get_signup_conversion_config(self, *, automation_key: str = DEFAULT_SIGNUP_CONVERSION_KEY) -> dict[str, Any]:
        key = _text(automation_key) or DEFAULT_SIGNUP_CONVERSION_KEY
        defaults = self._marketing_default_config(key)
        row = self.repo.get_marketing_automation_config(key)
        if not row:
            return defaults
        payload = dict(_json_loads(row.get("config_payload_json"), default={}) or {})
        questionnaire_id = _positive_int(payload.get("questionnaire_id"), field_name="questionnaire_id", allow_none=True)
        questionnaire_missing = bool(questionnaire_id and not self.repo.get_questionnaire(int(questionnaire_id)))
        question_map, option_map = self._questionnaire_rule_context(None if questionnaire_missing else questionnaire_id)
        return {
            **defaults,
            "automation_key": _text(row.get("automation_key")) or key,
            "automation_name": _text(row.get("automation_name")) or DEFAULT_MARKETING_AUTOMATION_NAME,
            "target_event": _text(row.get("target_event")) or DEFAULT_MARKETING_TARGET_EVENT,
            "channel_type": _text(row.get("channel_type")) or DEFAULT_MARKETING_CHANNEL_TYPE,
            "enabled": _text(row.get("status")).lower() == "active",
            "questionnaire_id": None if questionnaire_missing else questionnaire_id,
            "questionnaire_missing": questionnaire_missing,
            "missing_questionnaire_id": questionnaire_id if questionnaire_missing else None,
            "core_threshold": _bounded_int(payload.get("core_threshold"), field_name="core_threshold", default=DEFAULT_MARKETING_CORE_THRESHOLD, minimum=0),
            "top_threshold": _bounded_int(payload.get("top_threshold"), field_name="top_threshold", default=DEFAULT_MARKETING_TOP_THRESHOLD, minimum=0),
            "day_start_hour": _bounded_int(
                payload.get("day_start_hour"),
                field_name="day_start_hour",
                default=DEFAULT_MARKETING_DAY_START_HOUR,
                minimum=0,
                maximum=23,
            ),
            "quiet_hour_start": _bounded_int(
                row.get("do_not_start_after_hour"),
                field_name="quiet_hour_start",
                default=DEFAULT_MARKETING_QUIET_HOUR_START,
                minimum=0,
                maximum=23,
            ),
            "timezone": _normalize_timezone(payload.get("timezone")),
            "silent_threshold_days_by_pool": _normalize_silent_thresholds(payload.get("silent_threshold_days_by_pool")),
            "question_rules": [
                self._serialize_marketing_rule(item, question_map=question_map, option_map=option_map)
                for item in self.repo.list_marketing_automation_question_rules(int(row.get("id") or 0))
            ],
            "configured": True,
            "created_at": _text(row.get("created_at")),
            "updated_at": _text(row.get("updated_at")),
        }

    def _audit_meta_map_for_type(self, target_type: str, target_ids: list[str]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for target_id, row in self.repo.latest_audit_map(target_type=target_type, target_ids=target_ids).items():
            result[target_id] = {
                "last_modified_at": _text(row.get("created_at")),
                "last_modified_by": _text(row.get("operator")),
                "last_action_type": _text(row.get("action_type")),
            }
        return result

    def schema_groups(self) -> list[dict[str, Any]]:
        return [{"label": group["label"], "required": group.get("required", False), "fields": group["fields"]} for group in CONFIG_SCHEMA.values()]

    def masked_setting_values(self) -> dict[str, str]:
        return {key: mask_value(key, value) for key, value in self._current_setting_values().items()}

    def build_checklist(self) -> list[dict[str, Any]]:
        return build_config_checklist(self._current_setting_values())

    def build_login_access_payload(self) -> dict[str, Any]:
        rows = self._admin_user_rows()
        login_audit_rows = [
            {
                "created_at": _text(row.get("created_at")),
                "display_name": _text(row.get("display_name")),
                "wecom_userid": _text(row.get("wecom_userid")),
                "login_type": _text(row.get("login_type")),
                "login_result": _text(row.get("login_result")),
                "ip": _text(row.get("ip")),
                "user_agent": _text(row.get("user_agent")),
            }
            for row in self.repo.list_admin_login_audit(limit=20)
        ]
        corp_id = self._setting_value_source("WECOM_CORP_ID")[0]
        directory_members = self._directory_members_from_admin_users(rows, corp_id=corp_id)
        return {
            "rows": rows,
            "super_admin_rows": [row for row in rows if row.get("admin_level") == "super_admin"],
            "admin_rows": [row for row in rows if row.get("admin_level") != "super_admin"],
            "directory_members": directory_members,
            "directory_summary": {
                "count": len(directory_members),
                "authorized_count": sum(1 for row in directory_members if row.get("is_authorized")),
                "last_synced_at": "",
            },
            "role_options": [{"value": key, "label": value} for key, value in ROLE_LABELS.items()],
            "assignable_role_options": list(ADMIN_ASSIGNABLE_ROLE_OPTIONS),
            "role_labels": dict(ROLE_LABELS),
            "admin_level_labels": dict(ADMIN_LEVEL_LABELS),
            "login_audit_rows": login_audit_rows,
            "auth_mode": self._setting_value_source("ADMIN_AUTH_MODE")[0] or "wecom_sso",
            "corp_id": corp_id,
        }

    def _admin_user_rows(self) -> list[dict[str, Any]]:
        raw_rows = self.repo.list_admin_users()
        role_rows = self.repo.list_admin_user_roles([int(row.get("id") or 0) for row in raw_rows])
        role_map: dict[int, list[str]] = {}
        for row in role_rows:
            role_map.setdefault(int(row.get("admin_user_id") or 0), []).append(_text(row.get("role_code")))
        rows: list[dict[str, Any]] = []
        for row in raw_rows:
            user_id = int(row.get("id") or 0)
            roles = [role for role in role_map.get(user_id, []) if role]
            admin_level = _text(row.get("admin_level")) or ("super_admin" if "super_admin" in roles else "admin")
            rows.append(
                {
                    **row,
                    "id": user_id,
                    "roles": roles,
                    "role_labels": [ROLE_LABELS.get(role, role) for role in roles],
                    "roles_display": " / ".join(ROLE_LABELS.get(role, role) for role in roles) or "-",
                    "is_active": _bool(row.get("is_active")),
                    "login_enabled": _bool(row.get("login_enabled")),
                    "admin_level": admin_level,
                    "admin_level_label": ADMIN_LEVEL_LABELS.get(admin_level, admin_level),
                }
            )
        return rows

    def _directory_members_from_admin_users(self, rows: list[dict[str, Any]], *, corp_id: str) -> list[dict[str, Any]]:
        result = []
        for row in rows:
            result.append(
                {
                    "wecom_userid": _text(row.get("wecom_userid")),
                    "display_name": _text(row.get("display_name")) or _text(row.get("wecom_userid")),
                    "wecom_corpid": _text(row.get("wecom_corpid")) or corp_id,
                    "department_ids_display": "",
                    "position": "",
                    "status_label": "已授权",
                    "is_authorized": True,
                    "admin_user_id": row.get("id"),
                    "admin_login_enabled": row.get("login_enabled"),
                    "admin_level": row.get("admin_level"),
                    "admin_level_label": row.get("admin_level_label"),
                }
            )
        return result


class AdminConfigWriteCommand:
    def __init__(self, repo: AdminConfigRepository | None = None) -> None:
        self.repo = repo or AdminConfigRepository()

    def execute(self, settings: dict[str, Any], *, operator: str) -> list[dict[str, Any]]:
        metadata = {item["key"]: dict(item) for item in APP_SETTING_DEFINITIONS}
        changed: list[dict[str, Any]] = []
        for key, raw_value in settings.items():
            normalized_key = _text(key)
            if not normalized_key:
                continue
            metadata_row = metadata.get(normalized_key)
            if metadata_row:
                if metadata_row["mode"] == "masked" and _text(raw_value) == "":
                    continue
                validated = _validate_known_setting(normalized_key, _text(raw_value))
            else:
                validated = _text(raw_value)
            before = self.repo.get_app_setting(normalized_key)
            if stored_value_matches(normalized_key, (before or {}).get("value"), validated):
                continue
            after = self.repo.upsert_app_setting(key=normalized_key, value=validated)
            self.repo.insert_audit_log(
                operator=operator,
                action_type="update" if before else "create",
                target_type=TARGET_APP_SETTING,
                target_id=normalized_key,
                before=before or {},
                after=after,
            )
            changed.append(public_changed_row(normalized_key, after))
        return changed

    def _category_or_error(self, category_key: str) -> ConfigCategory:
        category = get_config_category(category_key)
        if not category:
            raise KeyError("config category not found")
        return category

    def set_category_enabled(self, category_key: str, enabled: bool, *, operator: str) -> dict[str, Any]:
        category = self._category_or_error(category_key)
        normalized_value = _normalize_boolean_text(enabled)
        before = self.repo.get_app_setting(category.enabled_key)
        if _text((before or {}).get("value")) == normalized_value:
            return {
                "key": category.key,
                "enabled_key": category.enabled_key,
                "enabled": _bool(normalized_value),
                "changed": False,
            }
        after = self.repo.upsert_app_setting(key=category.enabled_key, value=normalized_value)
        self.repo.insert_audit_log(
            operator=operator,
            action_type="update" if before else "create",
            target_type=TARGET_CONFIG_CATEGORY_ENABLED,
            target_id=category.key,
            before=before or {},
            after=after,
        )
        return {
            "key": category.key,
            "enabled_key": category.enabled_key,
            "enabled": _bool(after.get("value")),
            "changed": True,
        }

    def save_category_settings(self, category_key: str, settings: dict[str, Any], *, operator: str) -> list[dict[str, Any]]:
        category = self._category_or_error(category_key)
        if category.key == "webhooks_push":
            raise ValueError("webhooks_push settings are managed by push capabilities API")
        allowed_refs = {ref.key: ref for ref in category.fields}
        submitted_keys = {_text(key) for key in settings if _text(key)}
        unknown_keys = sorted(key for key in submitted_keys if key not in allowed_refs)
        if unknown_keys:
            raise ValueError(f"setting key is not in category: {', '.join(unknown_keys)}")
        changed: list[dict[str, Any]] = []
        for raw_key, raw_value in settings.items():
            key = _text(raw_key)
            if not key:
                continue
            ref = allowed_refs[key]
            if ref.readonly:
                raise ValueError(f"{key} is readonly")
            metadata = _metadata_for_setting(key)
            if (key in SENSITIVE_KEYS or metadata.get("mode") == "masked") and _text(raw_value) == "":
                continue
            validated = _validate_category_setting(key, raw_value, metadata)
            before = self.repo.get_app_setting(key)
            if stored_value_matches(key, (before or {}).get("value"), validated):
                continue
            after = self.repo.upsert_app_setting(key=key, value=validated)
            self.repo.insert_audit_log(
                operator=operator,
                action_type="update" if before else "create",
                target_type=TARGET_APP_SETTING,
                target_id=key,
                before=before or {},
                after=after,
            )
            changed.append(public_changed_row(key, after))
        return changed

    def _upsert_setting_with_audit(self, *, key: str, value: str, operator: str, target_type: str = TARGET_APP_SETTING) -> dict[str, Any]:
        before = self.repo.get_app_setting(key)
        after = self.repo.upsert_app_setting(key=key, value=value)
        if _text((before or {}).get("value")) != _text(after.get("value")):
            self.repo.insert_audit_log(
                operator=operator,
                action_type="update" if before else "create",
                target_type=target_type,
                target_id=key,
                before=before or {},
                after=after,
            )
        return after

    def _enabled_capabilities_for_derivation(self, read_service: AdminConfigReadService) -> list[PushCapability]:
        enabled: list[PushCapability] = []
        for capability in visible_push_capabilities(main_only=False):
            if not capability.toggleable or not capability.supports_real_execution:
                continue
            if read_service._capability_enabled(capability, default=False):
                enabled.append(capability)
        return enabled

    def _write_derived_push_gates(self, *, operator: str) -> dict[str, Any]:
        read_service = AdminConfigReadService(self.repo)
        enabled_capabilities = self._enabled_capabilities_for_derivation(read_service)
        effect_types = _effect_type_union_for_enabled_capabilities(read_service)
        explicitly_disabled_wecom_types: set[str] = set()
        for capability in visible_push_capabilities(main_only=False):
            value, _source = read_service._setting_value_source(capability.setting_key)
            if not _text(value) or _capability_enabled_from_value(value, default=False):
                continue
            explicitly_disabled_wecom_types.update(effect_type for effect_type in capability.effect_types if effect_type.startswith("wecom."))
        for effect_type in load_wecom_execution_config().enabled_effect_types:
            if effect_type not in explicitly_disabled_wecom_types and effect_type not in effect_types:
                effect_types.append(effect_type)
        gates = _derived_gate_payload(effect_types, enabled_capabilities)
        self._upsert_setting_with_audit(
            key="AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES",
            value=",".join(effect_types),
            operator=operator,
            target_type=TARGET_PUSH_CAPABILITY,
        )
        self._upsert_setting_with_audit(
            key="AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE",
            value=_normalize_boolean_text(gates["webhook_execute"]),
            operator=operator,
            target_type=TARGET_PUSH_CAPABILITY,
        )
        self._upsert_setting_with_audit(
            key=WECOM_EXECUTION_MODE_KEY,
            value="execute" if gates["wecom_execute"] else "disabled",
            operator=operator,
            target_type=TARGET_PUSH_CAPABILITY,
        )
        self._upsert_setting_with_audit(
            key=WECOM_ENABLED_EFFECT_TYPES_KEY,
            value=",".join(effect_type for effect_type in effect_types if effect_type.startswith("wecom.")),
            operator=operator,
            target_type=TARGET_PUSH_CAPABILITY,
        )
        self._upsert_setting_with_audit(
            key="AICRM_EXTERNAL_EFFECT_WECOM_EXECUTE",
            value=_normalize_boolean_text(gates["wecom_execute"]),
            operator=operator,
            target_type=TARGET_PUSH_CAPABILITY,
        )
        self._upsert_setting_with_audit(
            key=REALTIME_ENABLED_KEY,
            value=_normalize_boolean_text(gates["realtime_enabled"]),
            operator=operator,
            target_type=TARGET_PUSH_CAPABILITY,
        )
        self._upsert_setting_with_audit(
            key=REALTIME_ALLOWED_TYPES_KEY,
            value=",".join(gates["realtime_allowed_types"]),
            operator=operator,
            target_type=TARGET_PUSH_CAPABILITY,
        )
        self._upsert_setting_with_audit(
            key="AICRM_EXTERNAL_EFFECT_PAYMENT_EXECUTE",
            value=_normalize_boolean_text(gates["payment_execute"]),
            operator=operator,
            target_type=TARGET_PUSH_CAPABILITY,
        )
        self._upsert_setting_with_audit(
            key="AICRM_EXTERNAL_EFFECT_FEISHU_EXECUTE",
            value=_normalize_boolean_text(gates["feishu_execute"]),
            operator=operator,
            target_type=TARGET_PUSH_CAPABILITY,
        )
        self._upsert_setting_with_audit(
            key="AICRM_EXTERNAL_EFFECT_OPENCLAW_EXECUTE",
            value=_normalize_boolean_text(gates["openclaw_execute"]),
            operator=operator,
            target_type=TARGET_PUSH_CAPABILITY,
        )
        self._upsert_setting_with_audit(
            key="AICRM_EXTERNAL_EFFECT_MEDIA_UPLOAD_EXECUTE",
            value=_normalize_boolean_text(gates["media_upload_execute"]),
            operator=operator,
            target_type=TARGET_PUSH_CAPABILITY,
        )
        self._upsert_setting_with_audit(
            key="AICRM_EXTERNAL_EFFECT_TEST_RECEIVER_ENABLED",
            value=_normalize_boolean_text(gates["test_receiver_enabled"]),
            operator=operator,
            target_type=TARGET_PUSH_CAPABILITY,
        )
        return gates

    def set_push_capability_enabled(self, capability_key: str, enabled: bool, *, operator: str) -> dict[str, Any]:
        capability = get_push_capability(capability_key)
        if not capability:
            raise KeyError("push capability not found")
        if not capability.toggleable:
            raise PermissionError("push_capability_not_toggleable")
        self._upsert_setting_with_audit(
            key=capability.setting_key,
            value=_normalize_boolean_text(enabled),
            operator=operator,
            target_type=TARGET_PUSH_CAPABILITY,
        )
        derived = self._write_derived_push_gates(operator=operator)
        capability_payload = AdminConfigReadService(self.repo).get_push_capabilities()["capabilities"]
        current = next(item for item in capability_payload if item["key"] == capability.key)
        return {"capability": current, "derived_gates": derived}

    def set_external_effect_scheduler_enabled(self, enabled: bool, *, operator: str) -> dict[str, Any]:
        self._upsert_setting_with_audit(
            key=SCHEDULER_ENABLED_KEY,
            value=_normalize_boolean_text(enabled),
            operator=operator,
            target_type=TARGET_PUSH_CAPABILITY,
        )
        interval_value, _source = AdminConfigReadService(self.repo)._setting_value_source(SCHEDULER_INTERVAL_SECONDS_KEY)
        if not _text(interval_value):
            self._upsert_setting_with_audit(
                key=SCHEDULER_INTERVAL_SECONDS_KEY,
                value="60",
                operator=operator,
                target_type=TARGET_PUSH_CAPABILITY,
            )
        batch_value, _source = AdminConfigReadService(self.repo)._setting_value_source(SCHEDULER_BATCH_SIZE_KEY)
        if not _text(batch_value):
            self._upsert_setting_with_audit(
                key=SCHEDULER_BATCH_SIZE_KEY,
                value="20",
                operator=operator,
                target_type=TARGET_PUSH_CAPABILITY,
            )
        return {"scheduler": _scheduler_state_for_read_service(AdminConfigReadService(self.repo))}

    def check_category(self, category_key: str, *, operator: str) -> dict[str, Any]:
        del operator
        read_service = AdminConfigReadService(self.repo)
        detail = read_service.get_config_category_detail(category_key)
        category = self._category_or_error(category_key)
        checks: list[dict[str, Any]] = []
        for block in detail["blocks"]:
            for field in block["fields"]:
                key = _text(field.get("key"))
                label = _text(field.get("label")) or key
                value, source = read_service._setting_value_source(key)
                configured = bool(value)
                severity = "error" if field.get("required") else "warning"
                if field.get("required") and not configured:
                    checks.append(
                        {
                            "key": key,
                            "label": label,
                            "check": "required",
                            "status": "failed",
                            "severity": severity,
                            "message": "必填项未配置",
                        }
                    )
                if field.get("sensitive") and not configured:
                    checks.append(
                        {
                            "key": key,
                            "label": label,
                            "check": "sensitive_configured",
                            "status": "warning",
                            "severity": "warning",
                            "message": "敏感字段尚未配置",
                        }
                    )
                if configured:
                    try:
                        _validate_category_setting(key, value, field)
                    except ValueError as exc:
                        checks.append(
                            {
                                "key": key,
                                "label": label,
                                "check": "format",
                                "status": "failed",
                                "severity": "error",
                                "message": str(exc),
                            }
                        )
                    else:
                        checks.append(
                            {
                                "key": key,
                                "label": label,
                                "check": "format",
                                "status": "passed",
                                "severity": "info",
                                "message": f"配置值格式有效（source={source}）",
                            }
                        )
        adapter_preview: dict[str, Any] | None = None
        if category.key == "wechat_pay":
            adapter_preview = {
                "adapter": "wechat_pay",
                "mode": _text(managed_runtime_setting("AICRM_NEXT_WECHAT_PAY_MODE")) or "fake",
                "real_external_call_executed": False,
            }
        elif category.key == "alipay":
            adapter_preview = {
                "adapter": "alipay",
                "mode": _text(managed_runtime_setting("AICRM_NEXT_ALIPAY_MODE")) or "fake",
                "real_external_call_executed": False,
            }
        elif category.key == "wechat_shop":
            token_value, _source = read_service._setting_value_source("WECHAT_SHOP_CALLBACK_TOKEN")
            adapter_preview = {
                "adapter": "wechat_shop",
                "callback_token_configured": bool(token_value),
                "real_external_call_executed": False,
                "test_order_id_required_for_order_sync": True,
            }
        failed_count = sum(1 for item in checks if item["status"] == "failed")
        warning_count = sum(1 for item in checks if item["status"] == "warning")
        return {
            "ok": failed_count == 0,
            "category": detail["category"],
            "checks": checks,
            "summary": {
                "total": len(checks),
                "failed": failed_count,
                "warnings": warning_count,
            },
            "adapter_preview": adapter_preview,
            "real_external_call_executed": False,
        }


class SetupWizardStateService:
    def __init__(self, read_service: AdminConfigReadService | None = None) -> None:
        self.read_service = read_service or AdminConfigReadService()

    def build_state(self, *, validation_errors: list[dict[str, str]] | None = None, save_success: bool = False) -> dict[str, Any]:
        return {
            "schema_groups": self.read_service.schema_groups(),
            "current_values": self.read_service.masked_setting_values(),
            "validation_errors": validation_errors or [],
            "save_success": save_success,
            "admin_action_token": ensure_admin_action_token(),
        }


class SetupWizardSaveCommand:
    def __init__(
        self,
        read_service: AdminConfigReadService | None = None,
        write_command: AdminConfigWriteCommand | None = None,
    ) -> None:
        self.read_service = read_service or AdminConfigReadService()
        self.write_command = write_command or AdminConfigWriteCommand()

    def execute(self, form_payload: dict[str, Any], *, operator: str) -> dict[str, Any]:
        settings_to_save: dict[str, str] = {}
        for raw_key, raw_value in form_payload.items():
            key = _text(raw_key)
            if not key.startswith("setting__"):
                continue
            field_key = key[len("setting__") :]
            value = _text(raw_value)
            if field_key in SENSITIVE_KEYS and not value:
                continue
            settings_to_save[field_key] = value
        merged = self.read_service._current_setting_values()
        merged.update(settings_to_save)
        errors = validate_config(merged)
        if errors:
            return {"ok": False, "validation_errors": errors, "changed": []}
        changed = self.write_command.execute(settings_to_save, operator=operator) if settings_to_save else []
        return {
            "ok": True,
            "validation_errors": [],
            "changed": changed,
            "source_status": "next_command",
            "fallback_used": False,
            "real_external_call_executed": False,
        }


class LoginAccessSaveCommand:
    def __init__(self, repo: AdminConfigRepository | None = None) -> None:
        self.repo = repo or AdminConfigRepository()

    def execute(self, payload: dict[str, Any], *, operator: str) -> dict[str, Any]:
        wecom_userid = _text(payload.get("wecom_userid"))
        if not wecom_userid:
            raise ValueError("wecom_userid is required")
        admin_level = _text(payload.get("admin_level")) or "admin"
        if admin_level not in {"admin", "super_admin"}:
            raise ValueError("admin_level must be admin or super_admin")
        raw_roles = payload.get("role_codes") or []
        if isinstance(raw_roles, str):
            raw_roles = [raw_roles]
        roles = [
            _text(role)
            for role in raw_roles
            if _text(role) in ROLE_LABELS
            and _text(role) not in {"super_admin", SERVICE_PERIOD_GRID_COLLABORATOR_ROLE}
        ]
        before = self.repo.get_admin_user(int(payload.get("id") or 0)) if _text(payload.get("id")) else self.repo.get_admin_user_by_wecom_userid(wecom_userid)
        preserved_system_role = bool(
            before
            and SERVICE_PERIOD_GRID_COLLABORATOR_ROLE
            in self.repo.admin_user_role_codes(int(before.get("id") or 0))
        )
        if admin_level == "super_admin":
            roles = ["super_admin"]
        elif not roles and not preserved_system_role:
            roles = ["viewer"]
        if admin_level != "super_admin" and preserved_system_role:
            roles.append(SERVICE_PERIOD_GRID_COLLABORATOR_ROLE)
        user_payload = {
            "id": int(payload.get("id") or 0),
            "wecom_userid": wecom_userid,
            "wecom_corpid": _text(payload.get("wecom_corpid")),
            "display_name": _text(payload.get("display_name")) or wecom_userid,
            "is_active": _bool(payload.get("is_active", True)),
            "auth_source": _text(payload.get("auth_source")) or "wecom_sso",
            "updated_by": operator,
            "login_enabled": _bool(payload.get("login_enabled", True)),
            "admin_level": admin_level,
        }
        saved = self.repo.upsert_admin_user(user_payload)
        self.repo.replace_admin_user_roles(admin_user_id=int(saved.get("id") or 0), role_codes=roles)
        after = self.repo.get_admin_user(int(saved.get("id") or 0)) or saved
        self.repo.insert_audit_log(
            operator=operator,
            action_type="update" if before else "create",
            target_type=TARGET_ADMIN_USER,
            target_id=_text(after.get("id")),
            before=before or {},
            after={**after, "roles": roles},
        )
        return {**after, "roles": roles}


class McpToolSettingSaveCommand:
    def __init__(self, repo: AdminConfigRepository | None = None, read_service: AdminConfigReadService | None = None) -> None:
        self.repo = repo or AdminConfigRepository()
        self.read_service = read_service or AdminConfigReadService(self.repo)

    def execute(self, payload: dict[str, Any], *, operator: str) -> dict[str, Any]:
        self.read_service.ensure_mcp_tool_settings_seed()
        tool_name = _text(payload.get("tool_name") or payload.get("tool_key"))
        defaults = {item["name"]: item for item in _default_mcp_tool_defs() if _text(item.get("name"))}
        if tool_name not in defaults:
            raise ValueError("工具名称不合法")
        before = self.repo.get_mcp_tool_setting(tool_name)
        saved = self.repo.upsert_mcp_tool_setting(
            tool_name=tool_name,
            tool_group=_text(payload.get("tool_group")) or _default_tool_group(tool_name),
            display_name=_text(payload.get("display_name")) or _default_display_name(tool_name),
            description_override=_text(payload.get("description_override")),
            enabled=_bool(payload.get("enabled")),
            visible_in_console=_bool(payload.get("visible_in_console", True)),
            show_sample_args=_bool(payload.get("show_sample_args")),
            show_sample_output=_bool(payload.get("show_sample_output")),
            sort_order=_normalize_int(payload.get("sort_order") or 0, field_name="sort_order", minimum=0),
        )
        self.repo.insert_audit_log(
            operator=operator,
            action_type="update" if before else "create",
            target_type=TARGET_MCP_TOOL_SETTING,
            target_id=tool_name,
            before=before or {},
            after=saved,
        )
        return {
            **saved,
            "enabled": _bool(saved.get("enabled")),
            "visible_in_console": _bool(saved.get("visible_in_console")),
            "show_sample_args": _bool(saved.get("show_sample_args")),
            "show_sample_output": _bool(saved.get("show_sample_output")),
        }


class SignupConversionConfigSaveCommand:
    def __init__(self, repo: AdminConfigRepository | None = None) -> None:
        self.repo = repo or AdminConfigRepository()

    def _normalize_rules(
        self,
        rules: Any,
        *,
        questionnaire_id: int,
        question_map: dict[int, dict[str, Any]],
        option_map: dict[int, dict[int, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        if not isinstance(rules, list):
            raise ValueError("question_rules must be an array")
        if not rules:
            raise ValueError("question_rules must contain at least one item")
        normalized: list[dict[str, Any]] = []
        seen_question_ids: set[int] = set()
        for index, item in enumerate(rules, start=1):
            if not isinstance(item, dict):
                raise ValueError("question rule must be an object")
            question_id = _positive_int(item.get("questionnaire_question_id"), field_name="questionnaire_question_id")
            assert question_id is not None
            if question_id in seen_question_ids:
                raise ValueError("question_rules cannot contain duplicate questionnaire_question_id")
            seen_question_ids.add(question_id)
            question = question_map.get(question_id)
            if not question:
                raise ValueError(f"question {question_id} does not belong to questionnaire {questionnaire_id}")
            if _text(question.get("type")) not in {"single_choice", "multi_choice"}:
                raise ValueError(f"question {question_id} does not support option matching")
            available_options = option_map.get(question_id, {})
            hit_option_ids = [int(option_id) for option_id in item.get("hit_option_ids_json") or [] if _text(option_id)]
            invalid_option_ids = [option_id for option_id in hit_option_ids if option_id not in available_options]
            if invalid_option_ids:
                raise ValueError(f"option {invalid_option_ids[0]} does not belong to question {question_id}")
            normalized.append(
                {
                    "questionnaire_question_id": int(question_id),
                    "hit_option_ids_json": hit_option_ids,
                    "sort_order": _bounded_int(item.get("sort_order"), field_name="sort_order", default=index, minimum=1),
                    "rule_code": f"question-{question_id}",
                    "rule_name": _text(question.get("title")) or f"question-{question_id}",
                    "rule_payload": {"questionnaire_id": int(questionnaire_id)},
                }
            )
        normalized.sort(key=lambda item: (item["sort_order"], item["questionnaire_question_id"]))
        return normalized

    def execute(self, payload: dict[str, Any], *, operator: str = "crm_console", automation_key: str = DEFAULT_SIGNUP_CONVERSION_KEY) -> dict[str, Any]:
        raw_payload = dict(payload or {})
        key = _text(automation_key) or DEFAULT_SIGNUP_CONVERSION_KEY
        read_service = AdminConfigReadService(self.repo)
        before = read_service.get_signup_conversion_config(automation_key=key)
        questionnaire_id = _positive_int(raw_payload.get("questionnaire_id", before.get("questionnaire_id")), field_name="questionnaire_id")
        assert questionnaire_id is not None
        if not self.repo.get_questionnaire(int(questionnaire_id)):
            raise ValueError("questionnaire not found")
        question_map, option_map = read_service._questionnaire_rule_context(int(questionnaire_id))
        core_threshold = _bounded_int(
            raw_payload.get("core_threshold", before.get("core_threshold")),
            field_name="core_threshold",
            default=DEFAULT_MARKETING_CORE_THRESHOLD,
            minimum=0,
        )
        top_threshold = _bounded_int(
            raw_payload.get("top_threshold", before.get("top_threshold")),
            field_name="top_threshold",
            default=DEFAULT_MARKETING_TOP_THRESHOLD,
            minimum=0,
        )
        if top_threshold < core_threshold:
            raise ValueError("top_threshold must be >= core_threshold")
        day_start_hour = _bounded_int(
            raw_payload.get("day_start_hour", before.get("day_start_hour")),
            field_name="day_start_hour",
            default=DEFAULT_MARKETING_DAY_START_HOUR,
            minimum=0,
            maximum=23,
        )
        quiet_hour_start = _bounded_int(
            raw_payload.get("quiet_hour_start", before.get("quiet_hour_start")),
            field_name="quiet_hour_start",
            default=DEFAULT_MARKETING_QUIET_HOUR_START,
            minimum=0,
            maximum=23,
        )
        if day_start_hour >= quiet_hour_start:
            raise ValueError("day_start_hour must be < quiet_hour_start")
        timezone = _normalize_timezone(raw_payload.get("timezone", before.get("timezone")))
        silent_thresholds = _normalize_silent_thresholds(raw_payload.get("silent_threshold_days_by_pool", before.get("silent_threshold_days_by_pool")))
        rules = self._normalize_rules(
            raw_payload.get("question_rules", before.get("question_rules")),
            questionnaire_id=int(questionnaire_id),
            question_map=question_map,
            option_map=option_map,
        )
        enabled = _bool(raw_payload.get("enabled", before.get("enabled")))
        saved_row = self.repo.upsert_marketing_automation_config(
            automation_key=key,
            automation_name=DEFAULT_MARKETING_AUTOMATION_NAME,
            target_event=DEFAULT_MARKETING_TARGET_EVENT,
            channel_type=DEFAULT_MARKETING_CHANNEL_TYPE,
            status="active" if enabled else "disabled",
            do_not_start_after_hour=quiet_hour_start,
            config_payload={
                "questionnaire_id": int(questionnaire_id),
                "core_threshold": core_threshold,
                "top_threshold": top_threshold,
                "day_start_hour": day_start_hour,
                "timezone": timezone,
                "silent_threshold_days_by_pool": silent_thresholds,
            },
        )
        self.repo.replace_marketing_automation_question_rules(
            automation_config_id=int(saved_row.get("id") or 0),
            questionnaire_id=int(questionnaire_id),
            rules=rules,
        )
        after = read_service.get_signup_conversion_config(automation_key=key)
        if before != after:
            self.repo.insert_audit_log(
                operator=_text(operator) or "crm_console",
                action_type="update" if before.get("configured") else "create",
                target_type=TARGET_MARKETING_AUTOMATION_CONFIG,
                target_id=key,
                before=before,
                after=after,
            )
        return after
