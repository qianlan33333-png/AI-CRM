from __future__ import annotations

from collections import Counter, defaultdict

from aicrm_next.platform.shared.typing import JsonDict

from .dto import BatchSendRequest, UserOpsFilters

CARD_DEFINITIONS = [
    ("lead_pool_total_count", "引流品总数"),
    ("wecom_added_count", "已加微"),
    ("wecom_not_added_count", "未加微"),
    ("mobile_bound_count", "已绑手机号"),
    ("mobile_unbound_count", "未绑手机号"),
    ("huangxiaocan_activated_count", "黄小璨已激活"),
    ("huangxiaocan_not_activated_count", "黄小璨未激活"),
    ("pending_input_count", "激活待录入"),
]

SKIPPED_REASON_LABELS = {
    "missing_unionid": "缺少 unionid",
    "missing_external_userid": "缺少 external_userid，无法企微私信",
    "missing_owner_userid": "缺少负责人",
    "do_not_disturb": "免打扰",
    "no_allowed_sender": "无可用发送人",
}


def _norm(value: object) -> str:
    return str(value or "").strip()


def normalize_filters(filters: UserOpsFilters | None) -> UserOpsFilters:
    filters = filters or UserOpsFilters()
    wecom_status = _norm(filters.wecom_status).lower()
    mobile_binding_status = _norm(filters.mobile_binding_status).lower()
    activation_bucket = _norm(filters.activation_bucket).lower()
    return UserOpsFilters(
        wecom_status=wecom_status if wecom_status in {"added", "not_added", "all"} else "",
        mobile_binding_status=mobile_binding_status if mobile_binding_status in {"bound", "unbound", "all"} else "",
        activation_bucket=activation_bucket if activation_bucket in {"activated", "not_activated", "pending_input", "all"} else "",
        class_term_no=_norm(filters.class_term_no),
        tag=_norm(filters.tag),
        keyword=_norm(filters.keyword),
        mobile=_norm(filters.mobile),
        owner_userid=_norm(filters.owner_userid),
    )


def apply_filters(rows: list[JsonDict], filters: UserOpsFilters) -> list[JsonDict]:
    normalized = normalize_filters(filters)
    filtered = list(rows)
    if normalized.wecom_status == "added":
        filtered = [item for item in filtered if item["is_added_wecom"]]
    elif normalized.wecom_status == "not_added":
        filtered = [item for item in filtered if not item["is_added_wecom"]]
    if normalized.mobile_binding_status == "bound":
        filtered = [item for item in filtered if item["is_mobile_bound"]]
    elif normalized.mobile_binding_status == "unbound":
        filtered = [item for item in filtered if not item["is_mobile_bound"]]
    if normalized.activation_bucket and normalized.activation_bucket != "all":
        filtered = [item for item in filtered if item["activation_bucket"] == normalized.activation_bucket]
    if normalized.class_term_no:
        filtered = [item for item in filtered if item["class_term_no"] == normalized.class_term_no]
    if normalized.tag:
        filtered = [item for item in filtered if normalized.tag in {str(tag) for tag in item.get("tags") or []}]
    if normalized.mobile:
        filtered = [item for item in filtered if normalized.mobile in str(item.get("mobile") or "")]
    if normalized.owner_userid:
        filtered = [item for item in filtered if item.get("owner_userid") == normalized.owner_userid]
    if normalized.keyword:
        keyword = normalized.keyword
        filtered = [
            item
            for item in filtered
            if any(
                keyword in str(item.get(field) or "")
                for field in ["unionid", "mobile", "external_userid", "customer_name", "owner_userid", "owner_display_name"]
            )
        ]
    return filtered


def build_overview_cards(rows: list[JsonDict]) -> list[JsonDict]:
    counts = {
        "lead_pool_total_count": len(rows),
        "wecom_added_count": sum(1 for item in rows if item["is_added_wecom"]),
        "wecom_not_added_count": sum(1 for item in rows if not item["is_added_wecom"]),
        "mobile_bound_count": sum(1 for item in rows if item["is_mobile_bound"]),
        "mobile_unbound_count": sum(1 for item in rows if not item["is_mobile_bound"]),
        "huangxiaocan_activated_count": sum(1 for item in rows if item["activation_bucket"] == "activated"),
        "huangxiaocan_not_activated_count": sum(1 for item in rows if item["activation_bucket"] == "not_activated"),
        "pending_input_count": sum(1 for item in rows if item["activation_bucket"] == "pending_input"),
    }
    return [{"key": key, "label": label, "value": counts[key]} for key, label in CARD_DEFINITIONS]


def _has_body(request: BatchSendRequest) -> bool:
    return bool(_norm(request.content) or request.images or request.attachments)


def _content_preview(content: str, limit: int = 80) -> str:
    normalized = _norm(content)
    return normalized if len(normalized) <= limit else normalized[:limit] + "..."


def _skipped_summary(skipped_reasons: Counter[str]) -> list[JsonDict]:
    return [
        {"reason": reason, "reason_label": SKIPPED_REASON_LABELS.get(reason, reason), "count": count}
        for reason, count in sorted(skipped_reasons.items())
    ]


def _selected_rows(rows: list[JsonDict], request: BatchSendRequest) -> list[JsonDict]:
    excluded_ids = set(request.excluded_ids)
    if request.selection_mode == "all_filtered":
        selected = list(rows)
    else:
        selected_ids = set(request.selected_ids)
        selected = [item for item in rows if item["id"] in selected_ids]
    return [item for item in selected if item["id"] not in excluded_ids]


def _target_payload(item: JsonDict) -> JsonDict:
    return {
        "id": item["id"],
        "unionid": item["unionid"],
        "external_userid": item.get("external_userid") or "",
        "owner_userid": item["owner_userid"],
        "owner_display_name": item.get("owner_display_name") or "",
        "customer_name": item["customer_name"],
        "mobile": item.get("mobile") or "",
    }


def group_targets_by_owner(final_targets: list[JsonDict]) -> list[JsonDict]:
    buckets: dict[str, JsonDict] = {}
    for target in final_targets:
        owner_userid = target["owner_userid"]
        bucket = buckets.setdefault(
            owner_userid,
            {
                "owner_userid": owner_userid,
                "owner_display_name": target.get("owner_display_name") or owner_userid,
                "sender_userid": owner_userid,
                "target_count": 0,
                "target_unionids": [],
                "external_userids": [],
            },
        )
        bucket["target_count"] += 1
        bucket["target_unionids"].append(target["unionid"])
        if target.get("external_userid"):
            bucket["external_userids"].append(target["external_userid"])
    return [buckets[key] for key in sorted(buckets)]


def resolve_batch_targets(rows: list[JsonDict], request: BatchSendRequest) -> JsonDict:
    selected = _selected_rows(rows, request)
    skipped_reasons: Counter[str] = Counter()
    final_targets: list[JsonDict] = []
    for item in selected:
        if _norm(item.get("skip_reason")):
            skipped_reasons[_norm(item.get("skip_reason"))] += 1
            continue
        if not item.get("unionid"):
            skipped_reasons["missing_unionid"] += 1
            continue
        if not item.get("external_userid"):
            skipped_reasons["missing_external_userid"] += 1
            continue
        if not item.get("owner_userid"):
            skipped_reasons["missing_owner_userid"] += 1
            continue
        if item.get("do_not_disturb") and not request.include_do_not_disturb:
            skipped_reasons["do_not_disturb"] += 1
            continue
        final_targets.append(_target_payload(item))

    owner_buckets = group_targets_by_owner(final_targets)
    sender_counts: defaultdict[str, int] = defaultdict(int)
    for bucket in owner_buckets:
        sender_counts[bucket["sender_userid"]] += bucket["target_count"]
    skipped_by_reason = dict(skipped_reasons)
    return {
        "filters": normalize_filters(request.filters).model_dump(),
        "selection_mode": request.selection_mode,
        "selected_count": len(selected),
        "eligible_count": len(final_targets),
        "skipped_count": sum(skipped_reasons.values()),
        "skipped_by_reason": skipped_by_reason,
        "skipped_summary": _skipped_summary(skipped_reasons),
        "skip_summary": _skipped_summary(skipped_reasons),
        "include_do_not_disturb": request.include_do_not_disturb,
        "owner_buckets": owner_buckets,
        "target_unionids": [target["unionid"] for target in final_targets],
        "sender_buckets": [
            {"sender_userid": sender_userid, "target_count": count}
            for sender_userid, count in sorted(sender_counts.items())
        ],
        "sendable_samples": final_targets[:5],
        "final_targets": final_targets,
        "content_preview": _content_preview(request.content),
        "image_count": len(request.images),
        "has_body": _has_body(request),
    }
