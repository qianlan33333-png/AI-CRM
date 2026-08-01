from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable


TemplateCompiler = Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]
TemplateExplainer = Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class AudienceTemplate:
    key: str
    version: int
    label: str
    description: str
    default_refresh_mode: str
    fields: tuple[dict[str, Any], ...]
    dependencies: tuple[str, ...]
    compiler: TemplateCompiler
    explainer: TemplateExplainer

    def public_dict(self) -> dict[str, Any]:
        return {
            "template_key": self.key,
            "template_version": self.version,
            "label": self.label,
            "description": self.description,
            "default_refresh_mode": self.default_refresh_mode,
            "fields": [dict(field) for field in self.fields],
            "dependencies": list(self.dependencies),
        }


OWNER_FIELDS = (
    {
        "name": "owner_scope",
        "label": "负责人范围",
        "type": "enum",
        "required": True,
        "enum": ["specified", "all"],
        "enum_labels": {"specified": "指定负责人", "all": "全部负责人"},
        "help": "必须明确选择；不会默认放大到全部负责人。",
    },
    {
        "name": "owner_userids",
        "label": "负责人 UserID",
        "type": "string_list",
        "required": False,
        "default": [],
        "visible_when": {"owner_scope": "specified"},
    },
)


def _placeholders(values: list[Any], prefix: str, params: dict[str, Any]) -> str:
    names: list[str] = []
    for index, value in enumerate(values):
        name = f"{prefix}_{index}"
        names.append(f":{name}")
        params[name] = value
    return ", ".join(names)


def _owner_clause(parameters: dict[str, Any], *, column: str, params: dict[str, Any]) -> str:
    if parameters["owner_scope"] == "all":
        return "TRUE"
    return f"{column} IN ({_placeholders(parameters['owner_userids'], 'owner_userid', params)})"


def _wecom_contact_sql(parameters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {}
    status_clause = _placeholders(parameters["contact_statuses"], "contact_status", params)
    owner_clause = _owner_clause(parameters, column="wc.owner_userid", params=params)
    registration = parameters["registration_status"]
    registration_clause = "TRUE"
    if registration == "registered":
        registration_clause = "COALESCE(rs.is_registered, FALSE) = TRUE"
    elif registration == "unregistered":
        registration_clause = "COALESCE(rs.is_registered, FALSE) = FALSE"
    sql = f"""
SELECT DISTINCT wc.external_userid
FROM audience_read.wecom_contacts_v1 wc
LEFT JOIN audience_read.registration_status_v1 rs
  ON rs.external_userid = wc.external_userid
WHERE wc.external_userid <> ''
  AND wc.status IN ({status_clause})
  AND {owner_clause}
  AND {registration_clause}
""".strip()
    return sql, params


def _questionnaire_sql(parameters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {"questionnaire_id": parameters["questionnaire_id"]}
    owner_clause = _owner_clause(parameters, column="fs.owner_userid", params=params)
    conditions: list[str] = []
    for condition_index, condition in enumerate(parameters["conditions"]):
        question_name = f"question_id_{condition_index}"
        params[question_name] = condition["question_id"]
        option_parts: list[str] = []
        for option_index, option_id in enumerate(condition["option_ids"]):
            option_name = f"option_id_json_{condition_index}_{option_index}"
            params[option_name] = f"[{int(option_id)}]"
            option_parts.append(f"qa.selected_option_ids @> CAST(:{option_name} AS jsonb)")
        conditions.append(
            "EXISTS (SELECT 1 FROM audience_read.questionnaire_answers_v1 qa "
            "WHERE qa.submission_id = fs.submission_id "
            f"AND qa.question_id = :{question_name} AND ({' OR '.join(option_parts)}))"
        )
    sql = f"""
WITH submission_candidates AS (
  SELECT DISTINCT
    qa.external_userid,
    qa.submission_id,
    qa.owner_userid,
    qa.submitted_at
  FROM audience_read.questionnaire_answers_v1 qa
  WHERE qa.questionnaire_id = :questionnaire_id
    AND qa.submitted_at IS NOT NULL
    AND qa.external_userid <> ''
),
ranked_submissions AS (
  SELECT
    external_userid,
    submission_id,
    owner_userid,
    submitted_at,
    ROW_NUMBER() OVER (
      PARTITION BY external_userid
      ORDER BY submitted_at ASC, submission_id ASC
    ) AS submission_rank
  FROM submission_candidates
),
first_submissions AS (
  SELECT external_userid, submission_id, owner_userid, submitted_at
  FROM ranked_submissions
  WHERE submission_rank = 1
)
SELECT fs.external_userid
FROM first_submissions fs
WHERE {owner_clause}
  AND {' AND '.join(conditions)}
""".strip()
    return sql, params


def _paid_order_sql(parameters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {}
    product_clause = _placeholders(parameters["product_codes"], "product_code", params)
    owner_clause = _owner_clause(parameters, column="orders.owner_userid", params=params)
    time_clauses: list[str] = []
    if parameters.get("paid_at_from"):
        params["paid_at_from"] = parameters["paid_at_from"]
        time_clauses.append("orders.paid_at >= CAST(:paid_at_from AS timestamptz)")
    if parameters.get("paid_at_to"):
        params["paid_at_to"] = parameters["paid_at_to"]
        time_clauses.append("orders.paid_at < CAST(:paid_at_to AS timestamptz)")
    contact_join = ""
    contact_clause = ""
    if parameters["require_active_wecom_contact"]:
        contact_join = "JOIN audience_read.wecom_contacts_v1 wc ON wc.external_userid = orders.external_userid"
        contact_clause = "AND wc.status = 'active'"
    paid_time_clause = "" if not time_clauses else "AND " + " AND ".join(time_clauses)
    sql = f"""
SELECT DISTINCT orders.external_userid
FROM audience_read.orders_v1 orders
{contact_join}
WHERE orders.external_userid <> ''
  AND orders.status = 'paid'
  AND orders.product_code IN ({product_clause})
  AND {owner_clause}
  {paid_time_clause}
  {contact_clause}
""".strip()
    return sql, params


def _channel_entry_sql(parameters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {
        "entered_days_min": parameters["entered_days_min"],
    }
    channel_clause = _placeholders(parameters["channel_codes"], "channel_code", params)
    owner_clause = _owner_clause(parameters, column="entry.owner_userid", params=params)
    time_clauses = [
        "entry.last_entered_at <= CAST(:refresh_started_at AS timestamptz) - (:entered_days_min * INTERVAL '1 day')"
    ]
    if parameters.get("entered_days_max") is not None:
        params["entered_days_max"] = parameters["entered_days_max"]
        time_clauses.append(
            "entry.last_entered_at > CAST(:refresh_started_at AS timestamptz) - (:entered_days_max * INTERVAL '1 day')"
        )
    contact_join = ""
    contact_clause = ""
    if parameters["require_active_wecom_contact"]:
        contact_join = "JOIN audience_read.wecom_contacts_v1 wc ON wc.external_userid = entry.external_userid"
        contact_clause = "AND wc.status = 'active'"
    sql = f"""
SELECT DISTINCT entry.external_userid
FROM audience_read.channel_entries_v1 entry
{contact_join}
WHERE entry.external_userid <> ''
  AND entry.channel_code IN ({channel_clause})
  AND {owner_clause}
  AND {' AND '.join(time_clauses)}
  {contact_clause}
""".strip()
    return sql, params


def _radar_click_sql(parameters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {
        "elapsed_min": parameters["elapsed_min"],
        "elapsed_unit": parameters["elapsed_unit"],
    }
    radar_clause = _placeholders(parameters["radar_ids"], "radar_id", params)
    owner_clause = _owner_clause(parameters, column="first_click.owner_userid", params=params)
    interval_expression = "(CASE WHEN :elapsed_unit = 'hour' THEN INTERVAL '1 hour' ELSE INTERVAL '1 day' END)"
    time_clauses = [
        f"first_click.first_clicked_at <= CAST(:refresh_started_at AS timestamptz) - (:elapsed_min * {interval_expression})"
    ]
    if parameters.get("elapsed_max") is not None:
        params["elapsed_max"] = parameters["elapsed_max"]
        time_clauses.append(
            f"first_click.first_clicked_at > CAST(:refresh_started_at AS timestamptz) - (:elapsed_max * {interval_expression})"
        )
    sql = f"""
WITH first_click AS (
  SELECT
    clicks.external_userid,
    clicks.radar_id,
    (ARRAY_AGG(clicks.owner_userid ORDER BY clicks.clicked_at ASC, clicks.click_id ASC))[1] AS owner_userid,
    MIN(clicks.clicked_at) AS first_clicked_at
  FROM audience_read.radar_clicks_v1 clicks
  WHERE clicks.radar_id IN ({radar_clause})
    AND clicks.external_userid <> ''
  GROUP BY clicks.external_userid, clicks.radar_id
)
SELECT DISTINCT first_click.external_userid
FROM first_click
WHERE {owner_clause}
  AND {' AND '.join(time_clauses)}
""".strip()
    return sql, params


def _member_usage_sql(parameters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {}
    owner_clause = _owner_clause(parameters, column="member.owner_userid", params=params)
    clauses = ["member.external_userid <> ''", "member.membership_source <> ''", owner_clause]
    service_period = parameters["service_period"]
    if service_period == "active":
        clauses.append("member.is_member = TRUE")
        clauses.append(
            "(member.membership_expires_at IS NULL OR member.membership_expires_at > CAST(:refresh_started_at AS timestamptz))"
        )
    elif service_period == "expired":
        clauses.append(
            "(member.membership_status = 'expired' OR member.membership_expires_at <= CAST(:refresh_started_at AS timestamptz))"
        )
    registration_status = parameters["registration_status"]
    if registration_status != "any":
        clauses.append(f"member.is_registered = {'TRUE' if registration_status == 'registered' else 'FALSE'}")
    usage_status = parameters["usage_status"]
    if usage_status != "any":
        clauses.append(f"member.has_real_usage = {'TRUE' if usage_status == 'used' else 'FALSE'}")
    if parameters["membership_tiers"]:
        tiers = _placeholders(parameters["membership_tiers"], "membership_tier", params)
        clauses.append(f"member.membership_tier IN ({tiers})")
    if parameters["membership_statuses"]:
        statuses = _placeholders(parameters["membership_statuses"], "membership_status", params)
        clauses.append(f"member.membership_status IN ({statuses})")
    sql = f"""
SELECT DISTINCT member.external_userid
FROM audience_read.huangxiaocan_member_usage_status_v1 member
WHERE {' AND '.join(clauses)}
""".strip()
    return sql, params


def _owner_text(parameters: dict[str, Any]) -> str:
    if parameters["owner_scope"] == "all":
        return "全部负责人"
    return "负责人 " + "、".join(parameters["owner_userids"])


def _explain_wecom(parameters: dict[str, Any]) -> str:
    return f"{_owner_text(parameters)}范围内，联系人状态为{'、'.join(parameters['contact_statuses'])}，注册状态为{parameters['registration_status']}。"


def _explain_questionnaire(parameters: dict[str, Any]) -> str:
    conditions = "；".join(
        f"题目“{item['question_title']}”选择“{' 或 '.join(item['option_texts'])}”"
        for item in parameters["conditions"]
    )
    return f"{_owner_text(parameters)}范围内，问卷“{parameters['questionnaire_title']}”的首次完整提交同时满足：{conditions}。"


def _explain_paid(parameters: dict[str, Any]) -> str:
    return f"{_owner_text(parameters)}范围内，已支付商品为{'、'.join(parameters['product_names'])}，支付时间采用左闭右开窗口。"


def _explain_channel(parameters: dict[str, Any]) -> str:
    maximum = parameters.get("entered_days_max")
    window = f"[{parameters['entered_days_min']},{maximum}) 天" if maximum is not None else f"至少 {parameters['entered_days_min']} 天"
    return f"{_owner_text(parameters)}范围内，从渠道{'、'.join(parameters['channel_names'])}进入距今{window}。"


def _explain_radar(parameters: dict[str, Any]) -> str:
    maximum = parameters.get("elapsed_max")
    unit = "小时" if parameters["elapsed_unit"] == "hour" else "天"
    window = f"[{parameters['elapsed_min']},{maximum}) {unit}" if maximum is not None else f"至少 {parameters['elapsed_min']} {unit}"
    return f"{_owner_text(parameters)}范围内，首次可归因点击任一雷达（{'、'.join(parameters['radar_titles'])}）距今{window}；重复点击不重置时间。"


def _explain_member(parameters: dict[str, Any]) -> str:
    tiers = "全部层级" if not parameters["membership_tiers"] else "、".join(parameters["membership_tiers"])
    return f"{_owner_text(parameters)}范围内，服务期={parameters['service_period']}、注册={parameters['registration_status']}、使用={parameters['usage_status']}、会员层级={tiers}。"


TEMPLATES = (
    AudienceTemplate(
        key="wecom_contact_registration",
        version=1,
        label="企微联系人与注册状态",
        description="按负责人、企微联系人状态和注册状态圈选。",
        default_refresh_mode="every_3m",
        fields=OWNER_FIELDS + (
            {"name": "contact_statuses", "label": "联系人状态", "type": "enum_list", "required": True, "enum": ["active", "deleted"], "default": ["active"]},
            {"name": "registration_status", "label": "注册状态", "type": "enum", "required": True, "enum": ["any", "registered", "unregistered"], "default": "any"},
        ),
        dependencies=("audience_read.wecom_contacts_v1", "audience_read.registration_status_v1"),
        compiler=_wecom_contact_sql,
        explainer=_explain_wecom,
    ),
    AudienceTemplate(
        key="questionnaire_choice_answers",
        version=1,
        label="问卷选择题答案",
        description="按首次完整提交的选择题答案圈选；题间 AND、题内选项 OR。",
        default_refresh_mode="every_3m",
        fields=(
            {"name": "questionnaire", "label": "问卷", "type": "reference", "reference": "questionnaire", "required": True},
            {"name": "conditions", "label": "题目条件", "type": "condition_list", "required": True, "min_items": 1},
        ) + OWNER_FIELDS,
        dependencies=("audience_read.questionnaire_answers_v1",),
        compiler=_questionnaire_sql,
        explainer=_explain_questionnaire,
    ),
    AudienceTemplate(
        key="paid_order",
        version=1,
        label="已支付订单",
        description="按商品、支付时间、负责人和有效企微联系人圈选。",
        default_refresh_mode="every_3m",
        fields=(
            {"name": "products", "label": "商品", "type": "reference_list", "reference": "product", "required": True, "min_items": 1},
            {"name": "paid_at_from", "label": "支付时间起点", "type": "datetime", "required": False},
            {"name": "paid_at_to", "label": "支付时间终点（不含）", "type": "datetime", "required": False},
        ) + OWNER_FIELDS + (
            {"name": "require_active_wecom_contact", "label": "要求有效企微联系人", "type": "boolean", "required": True, "default": True},
        ),
        dependencies=("audience_read.orders_v1", "audience_read.wecom_contacts_v1"),
        compiler=_paid_order_sql,
        explainer=_explain_paid,
    ),
    AudienceTemplate(
        key="channel_entry",
        version=1,
        label="渠道进入",
        description="按渠道和距进入时间窗口圈选。",
        default_refresh_mode="every_3m",
        fields=(
            {"name": "channels", "label": "渠道", "type": "reference_list", "reference": "channel", "required": True, "min_items": 1},
            {"name": "entered_days_min", "label": "距进入最少天数", "type": "integer", "required": True, "default": 0, "minimum": 0},
            {"name": "entered_days_max", "label": "距进入最大天数（不含）", "type": "integer", "required": False, "minimum": 1},
        ) + OWNER_FIELDS + (
            {"name": "require_active_wecom_contact", "label": "要求有效企微联系人", "type": "boolean", "required": True, "default": True},
        ),
        dependencies=("audience_read.channel_entries_v1", "audience_read.wecom_contacts_v1"),
        compiler=_channel_entry_sql,
        explainer=_explain_channel,
    ),
    AudienceTemplate(
        key="radar_first_click_elapsed",
        version=1,
        label="雷达首次点击距今",
        description="多个雷达 OR，以首次可归因点击为时间锚点。",
        default_refresh_mode="every_3m",
        fields=(
            {"name": "radars", "label": "雷达", "type": "reference_list", "reference": "radar", "required": True, "min_items": 1},
            {"name": "elapsed_min", "label": "最小经过时间", "type": "integer", "required": True, "default": 0, "minimum": 0},
            {"name": "elapsed_max", "label": "最大经过时间（不含）", "type": "integer", "required": False, "minimum": 1},
            {"name": "elapsed_unit", "label": "时间单位", "type": "enum", "required": True, "enum": ["hour", "day"], "default": "day"},
        ) + OWNER_FIELDS,
        dependencies=("audience_read.radar_clicks_v1",),
        compiler=_radar_click_sql,
        explainer=_explain_radar,
    ),
    AudienceTemplate(
        key="member_usage_status",
        version=1,
        label="会员与真实使用状态",
        description="按负责人、服务期、注册、真实使用和会员层级圈选。",
        default_refresh_mode="daily_0200",
        fields=OWNER_FIELDS + (
            {"name": "service_period", "label": "服务期", "type": "enum", "required": True, "enum": ["any", "active", "expired"], "default": "active"},
            {"name": "registration_status", "label": "注册状态", "type": "enum", "required": True, "enum": ["any", "registered", "unregistered"], "default": "any"},
            {"name": "usage_status", "label": "真实使用状态", "type": "enum", "required": True, "enum": ["any", "used", "unused"], "default": "any"},
            {"name": "membership_tiers", "label": "会员层级", "type": "string_list", "required": False, "default": []},
            {"name": "membership_statuses", "label": "会员状态", "type": "string_list", "required": False, "default": []},
        ),
        dependencies=("audience_read.huangxiaocan_member_usage_status_v1",),
        compiler=_member_usage_sql,
        explainer=_explain_member,
    ),
)


TEMPLATE_REGISTRY = MappingProxyType({template.key: template for template in TEMPLATES})


def get_template(template_key: str, template_version: int | None = None) -> AudienceTemplate | None:
    template = TEMPLATE_REGISTRY.get(str(template_key or "").strip())
    if not template:
        return None
    if template_version is not None and int(template_version) != template.version:
        return None
    return template


def template_catalog_payload() -> dict[str, Any]:
    return {"ok": True, "templates": [template.public_dict() for template in TEMPLATES]}


__all__ = [
    "AudienceTemplate",
    "TEMPLATE_REGISTRY",
    "TEMPLATES",
    "get_template",
    "template_catalog_payload",
]
