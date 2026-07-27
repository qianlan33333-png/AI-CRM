from __future__ import annotations

import base64
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import cmp_to_key
import hashlib
import hmac
import json
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from aicrm_next.shared.errors import ContractError
from aicrm_next.shared.runtime import require_signing_secret

from .domain import isoformat, parse_datetime, remaining_days, text, utcnow


GRID_SCHEMA_VERSION = 1
MAX_FILTER_CONDITIONS = 20
MAX_SORTS = 8
MAX_GROUPS = 2
MAX_CONFIG_BYTES = 32 * 1024
MAX_TEXT_FILTER_LENGTH = 200
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 200

TRI_STATE_OPTIONS = (
    {"value": "yes", "label": "是"},
    {"value": "no", "label": "否"},
    {"value": "unmatched", "label": "未匹配"},
)
PROGRESS_STATE_OPTIONS = (
    {"value": "unmatched", "label": "未匹配"},
    {"value": "no_plan", "label": "无计划"},
    {"value": "not_started", "label": "未开始"},
    {"value": "in_progress", "label": "进行中"},
    {"value": "complete", "label": "已完成"},
)


@dataclass(frozen=True)
class FieldDefinition:
    field_id: str
    label: str
    field_type: str
    icon: str
    operators: tuple[str, ...]
    sort_alias: str
    group_order_alias: str
    group_partition_alias: str
    sort_cast: str
    sort_nullable: bool = False
    group_cast: str | None = None
    group_nullable: bool = False
    editable: bool = False
    options: tuple[dict[str, str], ...] = ()


TEXT_OPERATORS = ("contains", "not_contains", "equals", "not_equals", "is_empty", "is_not_empty")
NUMBER_OPERATORS = ("equals", "not_equals", "gt", "gte", "lt", "lte", "between", "is_empty", "is_not_empty")
ENUM_OPERATORS = ("in", "not_in")
DATETIME_OPERATORS = ("before", "after", "between", "is_empty", "is_not_empty")
PROGRESS_OPERATORS = (
    "state_in",
    "ratio_equals",
    "ratio_gt",
    "ratio_gte",
    "ratio_lt",
    "ratio_lte",
    "ratio_between",
    "is_empty",
    "is_not_empty",
)

OPERATOR_SCHEMA: dict[str, dict[str, str]] = {
    "contains": {"label": "包含", "value_kind": "text"},
    "not_contains": {"label": "不包含", "value_kind": "text"},
    "equals": {"label": "等于", "value_kind": "scalar"},
    "not_equals": {"label": "不等于", "value_kind": "scalar"},
    "is_empty": {"label": "为空", "value_kind": "none"},
    "is_not_empty": {"label": "不为空", "value_kind": "none"},
    "gt": {"label": "大于", "value_kind": "number"},
    "gte": {"label": "大于等于", "value_kind": "number"},
    "lt": {"label": "小于", "value_kind": "number"},
    "lte": {"label": "小于等于", "value_kind": "number"},
    "between": {"label": "介于", "value_kind": "range"},
    "in": {"label": "属于", "value_kind": "multi_select"},
    "not_in": {"label": "不属于", "value_kind": "multi_select"},
    "before": {"label": "早于", "value_kind": "datetime"},
    "after": {"label": "晚于", "value_kind": "datetime"},
    "state_in": {"label": "状态属于", "value_kind": "multi_select"},
    "ratio_equals": {"label": "完成率等于", "value_kind": "number"},
    "ratio_gt": {"label": "完成率大于", "value_kind": "number"},
    "ratio_gte": {"label": "完成率大于等于", "value_kind": "number"},
    "ratio_lt": {"label": "完成率小于", "value_kind": "number"},
    "ratio_lte": {"label": "完成率小于等于", "value_kind": "number"},
    "ratio_between": {"label": "完成率介于", "value_kind": "range"},
}

FIELDS: tuple[FieldDefinition, ...] = (
    FieldDefinition(
        field_id="member",
        label="会员",
        field_type="text",
        icon="text",
        operators=TEXT_OPERATORS,
        sort_alias="member_sort",
        group_order_alias="member_group",
        group_partition_alias="member_group",
        sort_cast="text",
        group_cast="text",
    ),
    FieldDefinition(
        field_id="remaining_days",
        label="剩余有效期",
        field_type="number",
        icon="number",
        operators=NUMBER_OPERATORS,
        sort_alias="remaining_days",
        group_order_alias="remaining_days",
        group_partition_alias="remaining_days",
        sort_cast="integer",
        group_cast="integer",
    ),
    FieldDefinition(
        field_id="formally_logged_in",
        label="正式登录",
        field_type="tri_state",
        icon="person",
        operators=ENUM_OPERATORS,
        sort_alias="formally_logged_in_rank",
        group_order_alias="formally_logged_in_rank",
        group_partition_alias="formally_logged_in",
        sort_cast="integer",
        group_cast="integer",
        options=TRI_STATE_OPTIONS,
    ),
    FieldDefinition(
        field_id="token_usage",
        label="token 消耗",
        field_type="tri_state",
        icon="check",
        operators=ENUM_OPERATORS,
        sort_alias="token_usage_rank",
        group_order_alias="token_usage_rank",
        group_partition_alias="token_usage",
        sort_cast="integer",
        group_cast="integer",
        options=TRI_STATE_OPTIONS,
    ),
    FieldDefinition(
        field_id="learning_plan_progress",
        label="学习计划进度",
        field_type="progress",
        icon="progress",
        operators=PROGRESS_OPERATORS,
        sort_alias="progress_ratio",
        group_order_alias="progress_state_rank",
        group_partition_alias="progress_state",
        sort_cast="numeric",
        sort_nullable=True,
        group_cast="integer",
        options=PROGRESS_STATE_OPTIONS,
    ),
    FieldDefinition(
        field_id="open_count_7d",
        label="近 7 天打开次数",
        field_type="number",
        icon="number",
        operators=NUMBER_OPERATORS,
        sort_alias="open_count_7d",
        group_order_alias="open_count_7d",
        group_partition_alias="open_count_7d",
        sort_cast="integer",
        sort_nullable=True,
        group_cast="integer",
        group_nullable=True,
    ),
    FieldDefinition(
        field_id="last_open_at",
        label="最后打开时间",
        field_type="datetime",
        icon="datetime",
        operators=DATETIME_OPERATORS,
        sort_alias="last_open_at",
        group_order_alias="last_open_date",
        group_partition_alias="last_open_date",
        sort_cast="timestamptz",
        sort_nullable=True,
        group_cast="date",
        group_nullable=True,
    ),
    FieldDefinition(
        field_id="renewal_count",
        label="续费次数",
        field_type="number",
        icon="number",
        operators=NUMBER_OPERATORS,
        sort_alias="renewal_count",
        group_order_alias="renewal_count",
        group_partition_alias="renewal_count",
        sort_cast="integer",
        group_cast="integer",
    ),
    FieldDefinition(
        field_id="remark",
        label="备注",
        field_type="text",
        icon="text",
        operators=TEXT_OPERATORS,
        sort_alias="remark_sort",
        group_order_alias="remark_group",
        group_partition_alias="remark_group",
        sort_cast="text",
        sort_nullable=True,
        group_cast="text",
        group_nullable=True,
        editable=True,
    ),
    FieldDefinition(
        field_id="alliance",
        label="联盟",
        field_type="text",
        icon="text",
        operators=TEXT_OPERATORS,
        sort_alias="alliance_sort",
        group_order_alias="alliance_group",
        group_partition_alias="alliance_group",
        sort_cast="text",
        sort_nullable=True,
        group_cast="text",
        group_nullable=True,
        editable=True,
    ),
)
FIELD_MAP = {field.field_id: field for field in FIELDS}


class MemberViewConflictError(ContractError):
    pass


@dataclass(frozen=True)
class OrderKey:
    alias: str
    direction: str
    cast: str
    nullable: bool = False


@dataclass(frozen=True)
class OrderElement:
    expression: str
    direction: str
    cast: str
    source_alias: str
    null_flag: bool = False


def empty_view_config() -> dict[str, Any]:
    return {
        "schema_version": GRID_SCHEMA_VERSION,
        "filter": {"logic": "and", "conditions": []},
        "sorts": [],
        "groups": [],
    }


def member_grid_schema() -> dict[str, Any]:
    return {
        "schema_version": GRID_SCHEMA_VERSION,
        "fields": [
            {
                "id": field.field_id,
                "label": field.label,
                "type": field.field_type,
                "icon": field.icon,
                "editable": field.editable,
                "filter_operators": [
                    {"id": operator, **OPERATOR_SCHEMA[operator]}
                    for operator in field.operators
                ],
                "sortable": True,
                "groupable": True,
                "options": [dict(option) for option in field.options],
            }
            for field in FIELDS
        ],
        "limits": {
            "filter_conditions": MAX_FILTER_CONDITIONS,
            "sorts": MAX_SORTS,
            "groups": MAX_GROUPS,
            "page_size": DEFAULT_PAGE_SIZE,
        },
    }


def normalize_view_name(value: Any) -> str:
    name = " ".join(str(value or "").strip().split())
    if not name:
        raise ContractError("视图名称不能为空")
    if len(name) > 60:
        raise ContractError("视图名称不能超过 60 个字符")
    return name


def normalize_view_config(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    if len(encoded) > MAX_CONFIG_BYTES:
        raise ContractError("视图配置不能超过 32KB")
    schema_version = _as_int(raw.get("schema_version"), default=GRID_SCHEMA_VERSION)
    if schema_version != GRID_SCHEMA_VERSION:
        raise ContractError("不支持的视图配置版本")

    filter_raw = raw.get("filter") if isinstance(raw.get("filter"), dict) else {}
    logic = text(filter_raw.get("logic") or "and").lower()
    if logic not in {"and", "or"}:
        raise ContractError("筛选逻辑必须是 and 或 or")
    conditions_raw = filter_raw.get("conditions") if isinstance(filter_raw.get("conditions"), list) else []
    if len(conditions_raw) > MAX_FILTER_CONDITIONS:
        raise ContractError(f"筛选条件不能超过 {MAX_FILTER_CONDITIONS} 条")
    conditions = [_normalize_condition(item) for item in conditions_raw]

    sorts = _normalize_order_list(raw.get("sorts"), maximum=MAX_SORTS, label="排序")
    groups = _normalize_order_list(raw.get("groups"), maximum=MAX_GROUPS, label="分组")
    sort_fields = {item["field"] for item in sorts}
    group_fields = {item["field"] for item in groups}
    duplicated = sort_fields & group_fields
    if duplicated:
        raise ContractError(f"分组字段不能重复参与排序：{sorted(duplicated)[0]}")
    return {
        "schema_version": GRID_SCHEMA_VERSION,
        "filter": {"logic": logic, "conditions": conditions},
        "sorts": sorts,
        "groups": groups,
    }


def _normalize_order_list(value: Any, *, maximum: int, label: str) -> list[dict[str, str]]:
    raw_items = value if isinstance(value, list) else []
    if len(raw_items) > maximum:
        raise ContractError(f"{label}字段不能超过 {maximum} 个")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ContractError(f"{label}配置格式错误")
        field_id = text(raw.get("field"))
        if field_id not in FIELD_MAP:
            raise ContractError(f"不支持的字段：{field_id}")
        if field_id in seen:
            raise ContractError(f"{label}字段不能重复：{field_id}")
        direction = text(raw.get("direction") or "asc").lower()
        if direction not in {"asc", "desc"}:
            raise ContractError(f"{label}方向必须是 asc 或 desc")
        seen.add(field_id)
        result.append({"field": field_id, "direction": direction})
    return result


def _normalize_condition(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("筛选条件格式错误")
    field_id = text(value.get("field"))
    field = FIELD_MAP.get(field_id)
    if field is None:
        raise ContractError(f"不支持的字段：{field_id}")
    operator = text(value.get("operator"))
    if operator not in field.operators:
        raise ContractError(f"字段 {field_id} 不支持操作符 {operator}")
    normalized: dict[str, Any] = {"field": field_id, "operator": operator}
    if OPERATOR_SCHEMA[operator]["value_kind"] == "none":
        return normalized
    normalized["value"] = _normalize_condition_value(field, operator, value.get("value"))
    return normalized


def _normalize_condition_value(field: FieldDefinition, operator: str, value: Any) -> Any:
    if operator in {"contains", "not_contains", "equals", "not_equals"} and field.field_type == "text":
        normalized = str(value or "").strip()
        if len(normalized) > MAX_TEXT_FILTER_LENGTH:
            raise ContractError(f"文本筛选值不能超过 {MAX_TEXT_FILTER_LENGTH} 个字符")
        return normalized
    if operator in {"in", "not_in", "state_in"}:
        values = value if isinstance(value, list) else [value]
        allowed = {item["value"] for item in field.options}
        normalized_values = list(dict.fromkeys(text(item) for item in values if text(item)))
        if not normalized_values or any(item not in allowed for item in normalized_values):
            raise ContractError(f"字段 {field.field_id} 的枚举筛选值无效")
        return normalized_values
    if operator in {"between", "ratio_between"}:
        values = value if isinstance(value, list) else []
        if len(values) != 2:
            raise ContractError("区间筛选必须包含两个值")
        if field.field_type == "datetime":
            return [_normalize_datetime(item) for item in values]
        numbers = [_normalize_number(item) for item in values]
        if numbers[0] > numbers[1]:
            numbers.reverse()
        return numbers
    if field.field_type == "datetime":
        return _normalize_datetime(value)
    if field.field_type in {"number", "progress"}:
        number = _normalize_number(value)
        if field.field_type == "progress" and not 0 <= number <= 100:
            raise ContractError("学习进度完成率必须在 0 到 100 之间")
        return number
    return str(value or "").strip()


def _normalize_datetime(value: Any) -> str:
    parsed = parse_datetime(value)
    if parsed is None:
        raise ContractError("日期时间筛选值无效")
    return parsed.isoformat()


def _normalize_number(value: Any) -> int | float:
    if isinstance(value, bool):
        raise ContractError("数字筛选值无效")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ContractError("数字筛选值无效") from exc
    return int(number) if number.is_integer() else number


def _as_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def config_hash(config: dict[str, Any]) -> str:
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def encode_cursor(*, config: dict[str, Any], snapshot_at: datetime, keys: list[Any]) -> str:
    payload = {
        "v": 1,
        "config_hash": config_hash(config),
        "snapshot_at": snapshot_at.astimezone(timezone.utc).isoformat(),
        "keys": [_json_value(item) for item in keys],
    }
    body = _b64(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_cursor_secret(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64(signature)}"


def decode_cursor(value: Any, *, config: dict[str, Any]) -> dict[str, Any]:
    cursor = text(value)
    if not cursor:
        return {}
    if len(cursor) > 8192 or "." not in cursor:
        raise ContractError("分页游标无效")
    body, supplied_signature = cursor.rsplit(".", 1)
    try:
        supplied = _unb64(supplied_signature)
        body_bytes = _unb64(body)
        if _b64(supplied) != supplied_signature or _b64(body_bytes) != body:
            raise ValueError("non-canonical base64")
        expected = hmac.new(_cursor_secret(), body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("signature mismatch")
        payload = json.loads(body_bytes.decode("utf-8"))
    except Exception as exc:
        raise ContractError("分页游标无效") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise ContractError("分页游标版本无效")
    if payload.get("config_hash") != config_hash(config):
        raise ContractError("视图配置已变化，请重新加载数据")
    snapshot_at = parse_datetime(payload.get("snapshot_at"))
    keys = payload.get("keys")
    if snapshot_at is None or not isinstance(keys, list):
        raise ContractError("分页游标内容无效")
    return {**payload, "snapshot_at": snapshot_at, "keys": keys}


def _cursor_secret() -> bytes:
    return require_signing_secret("SECRET_KEY", local_fallback="aicrm-next-local-secret") + b":service-period-member-grid"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def order_keys(config: dict[str, Any]) -> list[OrderKey]:
    keys: list[OrderKey] = []
    for item in config.get("groups") or []:
        field = FIELD_MAP[item["field"]]
        keys.append(
            OrderKey(
                alias=field.group_order_alias,
                direction=item["direction"],
                cast=field.group_cast or field.sort_cast,
                nullable=field.group_nullable,
            )
        )
    for item in config.get("sorts") or []:
        field = FIELD_MAP[item["field"]]
        keys.append(
            OrderKey(
                alias=field.sort_alias,
                direction=item["direction"],
                cast=field.sort_cast,
                nullable=field.sort_nullable,
            )
        )
    if not config.get("sorts"):
        keys.append(OrderKey(alias="end_at", direction="desc", cast="timestamptz", nullable=False))
    keys.append(OrderKey(alias="record_id", direction="desc", cast="bigint", nullable=False))
    return keys


def order_elements(config: dict[str, Any]) -> list[OrderElement]:
    elements: list[OrderElement] = []
    for key in order_keys(config):
        if key.nullable:
            elements.append(
                OrderElement(
                    expression=f"({key.alias} IS NULL)::integer",
                    direction="asc",
                    cast="integer",
                    source_alias=key.alias,
                    null_flag=True,
                )
            )
        elements.append(
            OrderElement(
                expression=key.alias,
                direction=key.direction,
                cast=key.cast,
                source_alias=key.alias,
            )
        )
    return elements


def sql_order_clause(config: dict[str, Any]) -> str:
    return ", ".join(f"{element.expression} {element.direction.upper()}" for element in order_elements(config))


def order_values_for_row(row: dict[str, Any], config: dict[str, Any]) -> list[Any]:
    values: list[Any] = []
    for element in order_elements(config):
        source = row.get(element.source_alias)
        if element.null_flag:
            values.append(1 if source is None else 0)
        else:
            values.append(source)
    return values


def sql_keyset_clause(config: dict[str, Any], cursor_keys: list[Any]) -> tuple[str, list[Any]]:
    elements = order_elements(config)
    if len(cursor_keys) != len(elements):
        raise ContractError("分页游标排序键无效")
    branches: list[str] = []
    params: list[Any] = []
    for index, element in enumerate(elements):
        value = cursor_keys[index]
        if value is None:
            continue
        prefix: list[str] = []
        prefix_params: list[Any] = []
        for previous, previous_value in zip(elements[:index], cursor_keys[:index]):
            if previous_value is None:
                prefix.append(f"{previous.expression} IS NULL")
            else:
                prefix.append(f"{previous.expression} = {_placeholder(previous.cast)}")
                prefix_params.append(previous_value)
        comparator = ">" if element.direction == "asc" else "<"
        comparison = f"{element.expression} {comparator} {_placeholder(element.cast)}"
        branch = " AND ".join([*prefix, comparison])
        branches.append(f"({branch})")
        params.extend(prefix_params)
        params.append(value)
    return (" OR ".join(branches) if branches else "FALSE", params)


def _placeholder(cast: str) -> str:
    supported = {"text", "integer", "bigint", "numeric", "date", "timestamptz"}
    if cast not in supported:
        raise ContractError("分页游标字段类型无效")
    return f"%s::{cast}"


def sql_filter_clause(config: dict[str, Any]) -> tuple[str, list[Any]]:
    conditions = (config.get("filter") or {}).get("conditions") or []
    if not conditions:
        return "TRUE", []
    clauses: list[str] = []
    params: list[Any] = []
    for condition in conditions:
        clause, condition_params = _sql_condition(condition)
        clauses.append(f"({clause})")
        params.extend(condition_params)
    connector = " AND " if (config.get("filter") or {}).get("logic") == "and" else " OR "
    return connector.join(clauses), params


def _sql_condition(condition: dict[str, Any]) -> tuple[str, list[Any]]:
    field_id = condition["field"]
    operator = condition["operator"]
    value = condition.get("value")
    if field_id in {"member", "remark", "alliance"}:
        search_alias = {
            "member": "member_search",
            "remark": "remark_search",
            "alliance": "alliance_search",
        }[field_id]
        value_alias = {
            "member": "member_sort",
            "remark": "remark_sort",
            "alliance": "alliance_sort",
        }[field_id]
        if operator in {"is_empty", "is_not_empty"}:
            clause = f"NULLIF(BTRIM(COALESCE({value_alias}, '')), '') IS NULL"
            return ((f"NOT ({clause})" if operator == "is_not_empty" else clause), [])
        normalized = str(value or "").lower()
        if operator in {"contains", "not_contains"}:
            clause = f"COALESCE({search_alias}, '') LIKE %s ESCAPE E'\\\\'"
            params = [f"%{_escape_like(normalized)}%"]
        else:
            clause = f"COALESCE({value_alias}, '') = %s::text"
            params = [normalized]
        if operator in {"not_contains", "not_equals"}:
            clause = f"NOT ({clause})"
        return clause, params

    alias = {
        "remaining_days": "remaining_days",
        "formally_logged_in": "formally_logged_in",
        "token_usage": "token_usage",
        "open_count_7d": "open_count_7d",
        "last_open_at": "last_open_at",
        "renewal_count": "renewal_count",
    }.get(field_id)
    if field_id == "learning_plan_progress":
        if operator == "state_in":
            return "progress_state = ANY(%s::text[])", [value]
        if operator in {"is_empty", "is_not_empty"}:
            clause = "progress_ratio IS NULL"
            return ((f"NOT ({clause})" if operator == "is_not_empty" else clause), [])
        mapped = operator.removeprefix("ratio_")
        return _sql_scalar_condition("progress_ratio", mapped, value, cast="numeric")
    if alias is None:
        raise ContractError(f"不支持的筛选字段：{field_id}")
    if operator in {"in", "not_in"}:
        clause = f"{alias} = ANY(%s::text[])"
        return ((f"NOT ({clause})" if operator == "not_in" else clause), [value])
    cast = "timestamptz" if field_id == "last_open_at" else "numeric"
    return _sql_scalar_condition(alias, operator, value, cast=cast)


def _sql_scalar_condition(alias: str, operator: str, value: Any, *, cast: str) -> tuple[str, list[Any]]:
    if operator in {"is_empty", "is_not_empty"}:
        clause = f"{alias} IS NULL"
        return ((f"NOT ({clause})" if operator == "is_not_empty" else clause), [])
    if operator == "between":
        return f"{alias} BETWEEN {_placeholder(cast)} AND {_placeholder(cast)}", [value[0], value[1]]
    comparator = {
        "equals": "=",
        "not_equals": "<>",
        "gt": ">",
        "gte": ">=",
        "lt": "<",
        "lte": "<=",
        "before": "<",
        "after": ">",
    }.get(operator)
    if comparator is None:
        raise ContractError(f"不支持的筛选操作符：{operator}")
    return f"{alias} {comparator} {_placeholder(cast)}", [value]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def normalize_member_row(member: dict[str, Any], *, snapshot_at: datetime | None = None) -> dict[str, Any]:
    snapshot = snapshot_at or utcnow()
    match_status = text(member.get("huangyoucan_match_status"))
    matched = match_status in {"matched_unionid", "matched_mobile"}
    display_name = text(member.get("display_name") or member.get("unionid"))
    external_userid = text(member.get("external_userid"))
    remark = str(member.get("remark") or "").strip()
    alliance = str(member.get("alliance") or "").strip()
    progress = member.get("huangyoucan_learning_plan_progress")
    progress = progress if isinstance(progress, dict) else None
    current = _optional_int((progress or {}).get("current"))
    total = _optional_int((progress or {}).get("total"))
    progress_state, progress_ratio = _progress_state(matched=matched, current=current, total=total)
    last_open = parse_datetime(member.get("huangyoucan_last_open_at")) if matched else None
    last_open_iso = last_open.isoformat() if last_open else None
    last_open_date = last_open.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat() if last_open else None
    end_at = parse_datetime(member.get("end_at"))
    remaining = remaining_days(end_at, now=snapshot) if end_at else _as_int(member.get("remaining_days"))
    formal = _tri_state(matched, member.get("huangyoucan_formally_logged_in"))
    token_usage = _tri_state(matched, member.get("huangyoucan_has_token_usage"))
    open_count = _optional_int(member.get("huangyoucan_open_count_7d")) if matched else None
    return {
        "record_id": member.get("record_id") or member.get("id") or text(member.get("unionid")),
        "unionid": text(member.get("unionid")),
        "display_name": display_name,
        "external_userid": external_userid,
        "member_sort": display_name.lower(),
        "member_group": display_name or None,
        "member_search": " ".join((display_name, external_userid, text(member.get("unionid")))).lower(),
        "remaining_days": remaining,
        "formally_logged_in": formal,
        "formally_logged_in_rank": _tri_state_rank(formal),
        "token_usage": token_usage,
        "token_usage_rank": _tri_state_rank(token_usage),
        "progress_state": progress_state,
        "progress_state_rank": _progress_state_rank(progress_state),
        "progress_ratio": progress_ratio,
        "progress_current": current,
        "progress_total": total,
        "open_count_7d": open_count,
        "last_open_at": last_open_iso,
        "last_open_date": last_open_date,
        "renewal_count": max(0, _as_int(member.get("renewal_count"))),
        "remark": remark,
        "remark_sort": remark.lower() or None,
        "remark_group": remark or None,
        "remark_search": remark.lower(),
        "alliance": alliance,
        "alliance_sort": alliance.lower() or None,
        "alliance_group": alliance or None,
        "alliance_search": alliance.lower(),
        "end_at": end_at.isoformat() if end_at else snapshot.isoformat(),
    }


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _tri_state(matched: bool, value: Any) -> str:
    if not matched:
        return "unmatched"
    return "yes" if bool(value) else "no"


def _tri_state_rank(value: str) -> int:
    return {"yes": 0, "no": 1, "unmatched": 2}.get(value, 3)


def _progress_state(*, matched: bool, current: int | None, total: int | None) -> tuple[str, float | None]:
    if not matched:
        return "unmatched", None
    if current is None or total is None or total <= 0:
        return "no_plan", None
    ratio = round(max(0.0, min(float(current) / float(total) * 100.0, 100.0)), 4)
    if current <= 0:
        return "not_started", ratio
    if current >= total:
        return "complete", ratio
    return "in_progress", ratio


def _progress_state_rank(value: str) -> int:
    return {"unmatched": 0, "no_plan": 1, "not_started": 2, "in_progress": 3, "complete": 4}.get(value, 5)


def query_in_memory_rows(
    members: Iterable[dict[str, Any]],
    *,
    config: dict[str, Any],
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str = "",
) -> dict[str, Any]:
    normalized_config = normalize_view_config(config)
    decoded = decode_cursor(cursor, config=normalized_config) if cursor else {}
    snapshot_at = decoded.get("snapshot_at") or utcnow()
    member_items = [dict(member) for member in members]
    rows = [normalize_member_row(member, snapshot_at=snapshot_at) for member in member_items]
    rows = [row for row in rows if _matches_filters(row, normalized_config)]
    rows.sort(key=cmp_to_key(lambda left, right: _compare_rows(left, right, normalized_config)))
    _attach_in_memory_group_counts(rows, normalized_config)
    cursor_keys = decoded.get("keys") or []
    if cursor_keys:
        rows = [row for row in rows if _row_after_cursor(row, cursor_keys, normalized_config)]
    page_size = max(1, min(int(limit or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
    page = rows[: page_size + 1]
    has_more = len(page) > page_size
    page = page[:page_size]
    next_cursor = ""
    if has_more and page:
        next_cursor = encode_cursor(
            config=normalized_config,
            snapshot_at=snapshot_at,
            keys=order_values_for_row(page[-1], normalized_config),
        )
    return {
        "ok": True,
        "rows": [public_grid_row(row, normalized_config) for row in page],
        "total": None,
        "has_more": has_more,
        "next_cursor": next_cursor,
        "snapshot_at": snapshot_at.isoformat(),
        "page_size": page_size,
    }


def _matches_filters(row: dict[str, Any], config: dict[str, Any]) -> bool:
    conditions = (config.get("filter") or {}).get("conditions") or []
    if not conditions:
        return True
    matches = [_matches_condition(row, condition) for condition in conditions]
    return all(matches) if (config.get("filter") or {}).get("logic") == "and" else any(matches)


def _matches_condition(row: dict[str, Any], condition: dict[str, Any]) -> bool:
    field_id = condition["field"]
    operator = condition["operator"]
    expected = condition.get("value")
    if field_id in {"member", "remark", "alliance"}:
        search = str(
            row.get(
                {
                    "member": "member_search",
                    "remark": "remark_search",
                    "alliance": "alliance_search",
                }[field_id]
            )
            or ""
        )
        value = str(
            row.get(
                {
                    "member": "member_sort",
                    "remark": "remark_sort",
                    "alliance": "alliance_sort",
                }[field_id]
            )
            or ""
        )
        target = str(expected or "").lower()
        return {
            "contains": target in search,
            "not_contains": target not in search,
            "equals": value == target,
            "not_equals": value != target,
            "is_empty": not value.strip(),
            "is_not_empty": bool(value.strip()),
        }[operator]
    if field_id == "learning_plan_progress":
        if operator == "state_in":
            return row.get("progress_state") in expected
        actual = row.get("progress_ratio")
        if operator == "is_empty":
            return actual is None
        if operator == "is_not_empty":
            return actual is not None
        return _compare_scalar(actual, operator.removeprefix("ratio_"), expected)
    alias = {
        "remaining_days": "remaining_days",
        "formally_logged_in": "formally_logged_in",
        "token_usage": "token_usage",
        "open_count_7d": "open_count_7d",
        "last_open_at": "last_open_at",
        "renewal_count": "renewal_count",
    }[field_id]
    actual = row.get(alias)
    if operator == "in":
        return actual in expected
    if operator == "not_in":
        return actual not in expected
    return _compare_scalar(actual, operator, expected)


def _compare_scalar(actual: Any, operator: str, expected: Any) -> bool:
    if operator == "is_empty":
        return actual is None or actual == ""
    if operator == "is_not_empty":
        return actual is not None and actual != ""
    if actual is None:
        return False
    left = actual
    right = expected
    if isinstance(actual, str) and parse_datetime(actual) and operator in {"before", "after", "between"}:
        left = parse_datetime(actual)
        if operator == "between":
            right = [parse_datetime(item) for item in expected]
        else:
            right = parse_datetime(expected)
    if operator == "between":
        return right[0] <= left <= right[1]
    return {
        "equals": left == right,
        "not_equals": left != right,
        "gt": left > right,
        "gte": left >= right,
        "lt": left < right,
        "lte": left <= right,
        "before": left < right,
        "after": left > right,
    }[operator]


def _compare_rows(left: dict[str, Any], right: dict[str, Any], config: dict[str, Any]) -> int:
    left_values = order_values_for_row(left, config)
    right_values = order_values_for_row(right, config)
    return _compare_order_values(left_values, right_values, config)


def _row_after_cursor(row: dict[str, Any], cursor_values: list[Any], config: dict[str, Any]) -> bool:
    values = order_values_for_row(row, config)
    if len(values) != len(cursor_values):
        raise ContractError("分页游标排序键无效")
    return _compare_order_values(values, cursor_values, config) > 0


def _compare_order_values(left: list[Any], right: list[Any], config: dict[str, Any]) -> int:
    for element, left_value, right_value in zip(order_elements(config), left, right):
        normalized_left = _comparable(left_value, element.cast)
        normalized_right = _comparable(right_value, element.cast)
        if normalized_left == normalized_right:
            continue
        comparison = -1 if normalized_left < normalized_right else 1
        return comparison if element.direction == "asc" else -comparison
    return 0


def _comparable(value: Any, cast: str) -> Any:
    if value is None:
        return ""
    if cast in {"integer", "bigint", "numeric"}:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    return str(value)


def _attach_in_memory_group_counts(rows: list[dict[str, Any]], config: dict[str, Any]) -> None:
    groups = config.get("groups") or []
    if not groups:
        return
    counters = [Counter() for _ in groups]
    for row in rows:
        path: list[Any] = []
        for index, item in enumerate(groups):
            field = FIELD_MAP[item["field"]]
            path.append(row.get(field.group_partition_alias))
            counters[index][tuple(path)] += 1
    for row in rows:
        path = []
        for index, item in enumerate(groups):
            field = FIELD_MAP[item["field"]]
            path.append(row.get(field.group_partition_alias))
            row[f"_group_count_{index + 1}"] = counters[index][tuple(path)]


def public_grid_row(row: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    progress_state = text(row.get("progress_state")) or "unmatched"
    result = {
        "record_id": text(row.get("record_id")),
        "unionid": text(row.get("unionid")),
        "values": {
            "member": {
                "primary": text(row.get("display_name") or row.get("unionid")),
                "secondary": text(row.get("external_userid") or row.get("unionid")),
            },
            "remaining_days": _as_int(row.get("remaining_days")),
            "formally_logged_in": text(row.get("formally_logged_in")) or "unmatched",
            "token_usage": text(row.get("token_usage")) or "unmatched",
            "learning_plan_progress": {
                "state": progress_state,
                "current": _optional_int(row.get("progress_current")),
                "total": _optional_int(row.get("progress_total")),
                "ratio": row.get("progress_ratio"),
            },
            "open_count_7d": _optional_int(row.get("open_count_7d")),
            "last_open_at": isoformat(row.get("last_open_at")) or None,
            "renewal_count": max(0, _as_int(row.get("renewal_count"))),
            "remark": str(row.get("remark") or ""),
            "alliance": str(row.get("alliance") or ""),
        },
        "group_path": [],
    }
    for index, item in enumerate(config.get("groups") or []):
        field = FIELD_MAP[item["field"]]
        value = row.get(field.group_partition_alias)
        result["group_path"].append(
            {
                "field": field.field_id,
                "value": _json_value(value),
                "label": group_label(field.field_id, value),
                "count": _as_int(row.get(f"_group_count_{index + 1}")),
            }
        )
    return result


def group_label(field_id: str, value: Any) -> str:
    if value in (None, ""):
        return "空值"
    if field_id == "remaining_days":
        return f"{_as_int(value)} 天"
    if field_id == "renewal_count":
        return f"{_as_int(value)} 次"
    if field_id in {"formally_logged_in", "token_usage"}:
        return {"yes": "是", "no": "否", "unmatched": "未匹配"}.get(text(value), text(value))
    if field_id == "learning_plan_progress":
        return {item["value"]: item["label"] for item in PROGRESS_STATE_OPTIONS}.get(text(value), text(value))
    return str(value)


def clone_config(config: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(normalize_view_config(config))
