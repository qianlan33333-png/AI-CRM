from __future__ import annotations

import hashlib
import re
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.responses import JSONResponse

from aicrm_next.send_targets.dto import SendTargetRequest
from aicrm_next.send_targets.resolver import SendTargetError, SendTargetResolver

from .external_campaigns_repo import ExternalCampaignRepository
from .external_campaigns_repo import build_external_campaign_repository

JsonDict = dict[str, Any]

_DEFAULT_TIMEZONE = "Asia/Shanghai"
_EXTERNAL_CAMPAIGN_REQUIRED_SEND_PATH = "ai_assist_pending_review"
_EXTERNAL_CAMPAIGN_FORBIDDEN_SEND_PATH = "direct_broadcast_job"


class ExternalCampaignError(Exception):
    def __init__(
        self,
        error: str,
        *,
        status_code: int = 400,
        message: str = "",
        phase: str = "",
        external_userid: str = "",
        owner_userid: str = "",
        group_code: str = "",
        campaign_code: str = "",
        trace_id: str = "",
        details: JsonDict | None = None,
    ) -> None:
        super().__init__(message or error)
        self.error = error
        self.status_code = status_code
        self.phase = phase
        self.external_userid = external_userid
        self.owner_userid = owner_userid
        self.group_code = group_code
        self.campaign_code = campaign_code
        self.trace_id = trace_id
        self.details = details or {}

    def add_context(
        self,
        *,
        group_code: str = "",
        campaign_code: str = "",
        trace_id: str = "",
        owner_userid: str = "",
        external_userid: str = "",
    ) -> "ExternalCampaignError":
        if group_code and not self.group_code:
            self.group_code = group_code
        if campaign_code and not self.campaign_code:
            self.campaign_code = campaign_code
        if trace_id and not self.trace_id:
            self.trace_id = trace_id
        if owner_userid and not self.owner_userid:
            self.owner_userid = owner_userid
        if external_userid and not self.external_userid:
            self.external_userid = external_userid
        return self

    def to_response(self) -> JsonDict:
        payload: JsonDict = {
            "ok": False,
            "error": self.error,
            "route_owner": "ai_crm_next",
        }
        if str(self):
            payload["message"] = str(self)
        for key in ("phase", "external_userid", "owner_userid", "group_code", "campaign_code", "trace_id"):
            value = _text(getattr(self, key))
            if value:
                payload[key] = value
        payload.update(self.details)
        return payload


def _text(value: object) -> str:
    return str(value or "").strip()


def _truthy(value: object) -> bool:
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _bool_value(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = _text(value).lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _has_content_package_materials(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    for key in ("image_library_ids", "miniprogram_library_ids", "attachment_library_ids", "group_invite_library_ids"):
        items = value.get(key)
        if isinstance(items, list) and any(_text(item) for item in items):
            return True
    attachments = value.get("attachments")
    return isinstance(attachments, list) and bool(attachments)


def _has_material_refs(value: object) -> bool:
    return isinstance(value, dict) and isinstance(value.get("material_asset_ids"), list) and any(_text(item) for item in value.get("material_asset_ids") or [])


def _slug(value: object, *, fallback: str = "external_campaign") -> str:
    text = _text(value).lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or fallback


def _hash_payload(*parts: object) -> str:
    joined = "\n".join(_text(part) for part in parts)
    return hashlib.sha256(joined.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _parse_local_datetime(value: object, *, default_timezone: str) -> datetime:
    raw = _text(value)
    if not raw:
        raise ExternalCampaignError("scheduled_for is required")
    normalized = raw.replace(" ", "T", 1)
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ExternalCampaignError("scheduled_for must be ISO datetime or YYYY-MM-DD HH:MM") from exc
    tz = ZoneInfo(default_timezone or _DEFAULT_TIMEZONE)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _first_schedule(payload: JsonDict, steps: list[JsonDict], *, timezone_name: str) -> datetime:
    for key in ("scheduled_for", "scheduled_at", "send_at"):
        if _text(payload.get(key)):
            return _parse_local_datetime(payload.get(key), default_timezone=timezone_name)
    first = steps[0] if steps else {}
    for key in ("scheduled_for", "scheduled_at", "send_at"):
        if _text(first.get(key)):
            return _parse_local_datetime(first.get(key), default_timezone=timezone_name)
    raise ExternalCampaignError("scheduled_for is required")


def _normalize_step_list(raw_steps: Any, payload: JsonDict, recipient: JsonDict, *, timezone_name: str) -> list[JsonDict]:
    source_steps = raw_steps if isinstance(raw_steps, list) and raw_steps else None
    if source_steps is None:
        content = _text(recipient.get("content_text")) or _text(recipient.get("message")) or _text(payload.get("content_text")) or _text(payload.get("message"))
        if not content:
            raise ExternalCampaignError("message/content_text is required")
        source_steps = [{"content_text": content}]

    first_dt = _first_schedule(payload, list(source_steps), timezone_name=timezone_name)
    anchor_date = first_dt.date()
    normalized: list[JsonDict] = []
    for index, item in enumerate(source_steps):
        if not isinstance(item, dict):
            raise ExternalCampaignError("steps items must be objects")
        content_payload = _content_package_from_sources(payload, recipient, item)
        content = _text(item.get("content_text")) or _text(item.get("message"))
        if not content:
            content = _text(recipient.get("content_text")) or _text(recipient.get("message"))
        if not content and not _has_content_package_materials(content_payload):
            if _has_material_refs(content_payload):
                raise ExternalCampaignError("material_invalid", status_code=400, phase="content_validation")
            raise ExternalCampaignError("content_required", status_code=400, phase="content_validation")

        scheduled_value = item.get("scheduled_for") or item.get("scheduled_at") or item.get("send_at")
        if _text(scheduled_value):
            scheduled_dt = _parse_local_datetime(scheduled_value, default_timezone=timezone_name)
            day_offset = (scheduled_dt.date() - anchor_date).days
            send_time = scheduled_dt.strftime("%H:%M")
        else:
            day_offset = int(item.get("day_offset") if item.get("day_offset") is not None else index)
            send_time = _text(item.get("send_time")) or (first_dt.strftime("%H:%M") if index == 0 else "10:30")
        if day_offset < 0:
            raise ExternalCampaignError("step scheduled_for cannot be earlier than the first scheduled_for")
        if not re.match(r"^\d{2}:\d{2}$", send_time):
            raise ExternalCampaignError(f"steps[{index}].send_time must be HH:MM")
        hour, minute = [int(part) for part in send_time.split(":", 1)]
        scheduled_dt = datetime.combine(
            anchor_date + timedelta(days=day_offset),
            time(hour=hour, minute=minute),
            tzinfo=ZoneInfo(_text(item.get("timezone")) or timezone_name),
        )
        normalized.append(
            {
                "step_index": index,
                "day_offset": day_offset,
                "send_time": send_time,
                "timezone": _text(item.get("timezone")) or timezone_name,
                "scheduled_for": scheduled_dt.isoformat(),
                "content_text": content,
                "content_payload": content_payload,
                "stop_on_reply": _bool_value(
                    item.get("stop_on_reply", payload.get("stop_on_reply")),
                    default=True,
                ),
                "skip_if_recently_touched_days": int(
                    item.get("skip_if_recently_touched_days")
                    if item.get("skip_if_recently_touched_days") is not None
                    else payload.get("skip_if_recently_touched_days") or 0
                ),
            }
        )
    return normalized


def _normalize_recipients(payload: JsonDict) -> list[JsonDict]:
    raw_recipients = payload.get("recipients")
    recipients: list[JsonDict] = []
    if isinstance(raw_recipients, list) and raw_recipients:
        for item in raw_recipients:
            if isinstance(item, str):
                recipients.append({"external_userid": _text(item)})
            elif isinstance(item, dict):
                recipients.append(dict(item))
            else:
                raise ExternalCampaignError("recipients items must be strings or objects")
    else:
        external_userids = payload.get("external_userids")
        if isinstance(external_userids, list):
            recipients = [{"external_userid": _text(item)} for item in external_userids]
        elif _text(payload.get("external_userid")):
            recipients = [{"external_userid": _text(payload.get("external_userid"))}]
        elif _text(payload.get("unionid")):
            recipients = [{"unionid": _text(payload.get("unionid"))}]
        elif _text(payload.get("target_id")):
            recipients = [{"target_id": _text(payload.get("target_id")), "target_id_type": _text(payload.get("target_id_type")) or "auto"}]
    cleaned = []
    seen = set()
    for item in recipients:
        external_userid = _text(item.get("external_userid") or item.get("external_contact_id"))
        unionid = _text(item.get("unionid"))
        target_id = _text(item.get("target_id")) or unionid or external_userid
        if not target_id:
            continue
        target_id_type = _text(item.get("target_id_type")) or ("unionid" if unionid and not external_userid else "external_userid")
        key = f"{target_id_type}:{target_id}"
        if key in seen:
            continue
        seen.add(key)
        if external_userid:
            item["external_userid"] = external_userid
        if unionid:
            item["unionid"] = unionid
        item["target_id"] = target_id
        item["target_id_type"] = target_id_type
        cleaned.append(item)
    if not cleaned:
        raise ExternalCampaignError("target_id/external_userid/external_userids/recipients is required")
    return cleaned


def _build_repo(repo: ExternalCampaignRepository | None) -> ExternalCampaignRepository:
    return repo or build_external_campaign_repository()


def _external_campaign_review_details(
    *,
    owner_userid: str,
    group_code: str,
    group_label: str,
    recipient_count: int,
    previews: list[JsonDict] | None = None,
) -> JsonDict:
    details: JsonDict = {
        "send_path": _EXTERNAL_CAMPAIGN_REQUIRED_SEND_PATH,
        "required_send_path": _EXTERNAL_CAMPAIGN_REQUIRED_SEND_PATH,
        "forbidden_send_path": _EXTERNAL_CAMPAIGN_FORBIDDEN_SEND_PATH,
        "review_status": "pending_review",
        "run_status": "draft",
        "scheduled_jobs": 0,
        "review_required": True,
        "owner_userid": owner_userid,
        "group_code": group_code,
        "group_label": group_label,
        "recipient_count": int(recipient_count or 0),
    }
    if previews is not None:
        details["previews"] = previews
    return details


def _target_id_for_recipient(recipient: JsonDict) -> tuple[str, str]:
    external_userid = _text(recipient.get("external_userid") or recipient.get("external_contact_id"))
    unionid = _text(recipient.get("unionid"))
    target_id = _text(recipient.get("target_id")) or unionid or external_userid
    target_id_type = _text(recipient.get("target_id_type")) or ("unionid" if unionid and not external_userid else "external_userid")
    return target_id, target_id_type


def _lookup_target(
    *,
    target_id: str,
    target_id_type: str,
    owner_userid: str,
    strict_owner_match: bool,
    bypass_dnd: bool,
    repo: ExternalCampaignRepository,
) -> JsonDict:
    try:
        resolved = SendTargetResolver(repo).resolve(
            SendTargetRequest(
                target_id=target_id,
                target_id_type=target_id_type,
                sender_userid=owner_userid,
                strict_owner_match=strict_owner_match,
                bypass_dnd=bypass_dnd,
            )
        )
    except SendTargetError as exc:
        external_context = _text(exc.details.get("external_userid")) or (target_id if target_id_type == "external_userid" else "")
        raise ExternalCampaignError(
            exc.error_code,
            status_code=exc.status_code,
            message=str(exc),
            phase="target_lookup",
            external_userid=external_context,
            owner_userid=owner_userid,
            details=exc.details,
        ) from exc
    external_userid = _text(resolved.external_userid)
    contact = repo.fetch_contact_row(external_userid) if external_userid else {}
    return {
        "resolved": True,
        "source": resolved.target_source,
        "unionid": resolved.unionid,
        "external_userid": external_userid,
        "sender_userid": resolved.sender_userid,
        "customer_name": resolved.customer_name,
        "owner_userid": resolved.owner_userid,
        "warnings": list(resolved.warnings),
        "do_not_disturb_reasons": list(resolved.do_not_disturb_reasons),
        "contact": contact,
    }


def _material_package_from_ids(items: Any) -> JsonDict:
    package: JsonDict = {"material_asset_ids": []}
    if not isinstance(items, list):
        return {}
    for item in items:
        raw = _text(item)
        if not raw:
            continue
        package["material_asset_ids"].append(raw)
        prefix, _, value = raw.partition(":")
        key = {
            "image": "image_library_ids",
            "miniprogram": "miniprogram_library_ids",
            "attachment": "attachment_library_ids",
            "group_invite": "group_invite_library_ids",
        }.get(prefix)
        if key and _text(value):
            package.setdefault(key, []).append(value)
    return {key: value for key, value in package.items() if value}


def _merge_package(target: JsonDict, source: JsonDict) -> JsonDict:
    for key, value in source.items():
        if isinstance(value, list):
            existing = list(target.get(key) or [])
            for item in value:
                if item not in existing:
                    existing.append(item)
            target[key] = existing
        elif value not in (None, "", {}):
            target[key] = value
    return target


def _content_package_from_sources(*sources: JsonDict) -> JsonDict:
    package: JsonDict = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        nested = source.get("content_payload") if isinstance(source.get("content_payload"), dict) else {}
        _merge_package(package, nested)
        _merge_package(package, _material_package_from_ids(nested.get("material_asset_ids")))
        nested = source.get("content_package") if isinstance(source.get("content_package"), dict) else {}
        _merge_package(package, nested)
        _merge_package(package, _material_package_from_ids(nested.get("material_asset_ids")))
        for key in ("image_library_ids", "miniprogram_library_ids", "attachment_library_ids", "group_invite_library_ids", "attachments"):
            value = source.get(key)
            if isinstance(value, list):
                _merge_package(package, {key: value})
        _merge_package(package, _material_package_from_ids(source.get("material_asset_ids")))
    return package


def _content_summary(content_text: str, content_package: JsonDict) -> str:
    if content_text:
        return content_text[:200]
    counts = {key: len(value) for key, value in content_package.items() if isinstance(value, list)}
    return "attachments:" + ",".join(f"{key}={value}" for key, value in sorted(counts.items())) if counts else ""


def _direct_job_idempotency_key(
    *,
    payload: JsonDict,
    group_code: str,
    owner_userid: str,
    target_id: str,
    scheduled_for: str,
    step_index: int,
) -> str:
    provided = _text(payload.get("idempotency_key"))
    suffix = _hash_payload(group_code, owner_userid, target_id, scheduled_for, step_index)
    return f"{provided}:direct:{suffix}" if provided else f"direct_send:{suffix}"


def _direct_job_payload(*, step: JsonDict, target: JsonDict, owner_userid: str, content_package: JsonDict) -> JsonDict:
    content_text = _text(step.get("content_text"))
    return {
        "channel": "wecom_private",
        "sender_userid": owner_userid,
        "owner_userid": owner_userid,
        "target_unionids": [target["unionid"]],
        "content_text": content_text,
        "rendered_content": {"content_text": content_text},
        "content_payload_json": content_package,
        "content_package": content_package,
        "attachments": list(content_package.get("attachments") or []),
    }


def _preview_single_recipient_direct_send(
    *,
    payload: JsonDict,
    recipient: JsonDict,
    owner_userid: str,
    group_code: str,
    group_label: str,
    timezone_name: str,
    strict_owner_match: bool,
    bypass_dnd: bool,
    repo: ExternalCampaignRepository,
) -> JsonDict:
    target_id, target_id_type = _target_id_for_recipient(recipient)
    steps = _normalize_step_list(
        recipient.get("steps") if isinstance(recipient.get("steps"), list) else payload.get("steps"),
        payload,
        recipient,
        timezone_name=timezone_name,
    )
    target = _lookup_target(
        target_id=target_id,
        target_id_type=target_id_type,
        owner_userid=owner_userid,
        strict_owner_match=strict_owner_match,
        bypass_dnd=bypass_dnd,
        repo=repo,
    )
    return {
        "status": "preview",
        "target_id": target_id,
        "target_id_type": target_id_type,
        "unionid": target["unionid"],
        "external_userid": target["external_userid"],
        "sender_userid": owner_userid,
        "target_source": target["source"],
        "group_code": group_code,
        "group_label": group_label,
        "job_count": len(steps),
        "warnings": target.get("warnings") or [],
        "jobs": [
            {
                "status": "would_create",
                "scheduled_for": _text(step.get("scheduled_for")),
                "content_summary": _content_summary(_text(step.get("content_text")), _content_package_from_sources(payload, recipient, step)),
                "step_index": int(step["step_index"]),
            }
            for step in steps
        ],
    }


def _create_single_recipient_direct_send(
    *,
    payload: JsonDict,
    recipient: JsonDict,
    owner_userid: str,
    operator: str,
    group_code: str,
    group_label: str,
    timezone_name: str,
    strict_owner_match: bool,
    bypass_dnd: bool,
    repo: ExternalCampaignRepository,
    source_type: str,
) -> list[JsonDict]:
    target_id, target_id_type = _target_id_for_recipient(recipient)
    steps = _normalize_step_list(
        recipient.get("steps") if isinstance(recipient.get("steps"), list) else payload.get("steps"),
        payload,
        recipient,
        timezone_name=timezone_name,
    )
    target = _lookup_target(
        target_id=target_id,
        target_id_type=target_id_type,
        owner_userid=owner_userid,
        strict_owner_match=strict_owner_match,
        bypass_dnd=bypass_dnd,
        repo=repo,
    )
    created: list[JsonDict] = []
    trace_root = _text(payload.get("trace_id")) or f"{source_type}-{_hash_payload(group_code, owner_userid, target_id)}"
    for step in steps:
        content_package = _content_package_from_sources(payload, recipient, step)
        content_text = _text(step.get("content_text"))
        content_summary = _content_summary(content_text, content_package)
        if not content_text and not _has_content_package_materials(content_package):
            raise ExternalCampaignError(
                "content_required",
                status_code=400,
                message="content_text or material_asset_ids/attachments is required",
                phase="content_validation",
                external_userid=target["external_userid"],
                owner_userid=owner_userid,
            )
        scheduled_for = _text(step.get("scheduled_for"))
        idempotency_key = _direct_job_idempotency_key(
            payload=payload,
            group_code=group_code,
            owner_userid=owner_userid,
            target_id=target_id,
            scheduled_for=scheduled_for,
            step_index=int(step["step_index"]),
        )
        job = repo.create_broadcast_job(
            source_type=source_type,
            source_id=idempotency_key,
            source_table=source_type,
            scheduled_for=scheduled_for,
            priority=int(payload.get("priority") or 100),
            batch_key=f"{source_type}:{group_code}:{owner_userid}",
            idempotency_key=idempotency_key,
            target_unionids=[target["unionid"]],
            target_summary=f"{target.get('customer_name') or target['unionid']} / {target['external_userid']}",
            content_type="private_message",
            content_payload=_direct_job_payload(step=step, target=target, owner_userid=owner_userid, content_package=content_package),
            content_summary=content_summary,
            trace_id=f"{trace_root}:{int(step['step_index'])}",
            created_by=operator,
            business_domain="ai_assistant" if source_type == "external_campaign" else "manual",
            channel="wecom_private",
            target_kind="unionid",
            metadata={
                "source": "external_token_api" if source_type == "external_campaign" else "direct_send_api",
                "group_code": group_code,
                "group_label": group_label,
                "target_id": target_id,
                "target_id_type": target_id_type,
                "external_userid": target["external_userid"],
                "sender_userid": owner_userid,
                "step_index": int(step["step_index"]),
                "warnings": target.get("warnings") or [],
            },
        )
        created.append(
            {
                "broadcast_job_id": int(job.get("id") or 0),
                "job_id": int(job.get("id") or 0),
                "target_id": target_id,
                "target_id_type": target_id_type,
                "unionid": target["unionid"],
                "external_userid": target["external_userid"],
                "sender_userid": owner_userid,
                "status": "exists" if job.get("idempotent_existing") else (_text(job.get("status")) or "queued"),
                "scheduled_for": scheduled_for,
                "step_index": int(step["step_index"]),
                "idempotency_key": idempotency_key,
                "target_source": target["source"],
                "warnings": target.get("warnings") or [],
            }
        )
    return created


def create_external_campaigns(payload: JsonDict, repo: ExternalCampaignRepository | None = None) -> JsonDict:
    if not isinstance(payload, dict):
        raise ExternalCampaignError("json object body is required")
    repository = _build_repo(repo)
    owner_userid = _text(payload.get("owner_userid") or payload.get("sender"))
    if not owner_userid:
        raise ExternalCampaignError("owner_userid/sender is required")
    timezone_name = _text(payload.get("timezone")) or _DEFAULT_TIMEZONE
    group_code = _slug(payload.get("group_code") or payload.get("idempotency_key") or payload.get("intent"))
    group_label = _text(payload.get("group_label")) or _text(payload.get("intent")) or group_code
    strict_owner_match = _truthy(payload.get("strict_owner_match"))
    bypass_dnd = _truthy(payload.get("bypass_dnd"))
    recipients = _normalize_recipients(payload)
    dry_run = _truthy(payload.get("dry_run")) or _truthy(payload.get("preview"))
    use_campaign_workflow = _truthy(payload.get("use_campaign_workflow"))
    if use_campaign_workflow:
        raise ExternalCampaignError(
            "campaign_workflow_retired",
            status_code=410,
            message="use_campaign_workflow is retired; external campaign requests must enter AI assistant review before any send job is queued",
            phase="request_validation",
            owner_userid=owner_userid,
            details={
                "send_path": _EXTERNAL_CAMPAIGN_REQUIRED_SEND_PATH,
                "required_send_path": _EXTERNAL_CAMPAIGN_REQUIRED_SEND_PATH,
                "forbidden_send_path": _EXTERNAL_CAMPAIGN_FORBIDDEN_SEND_PATH,
                "retired_path": "campaign_workflow",
                "review_status": "pending_review",
                "run_status": "draft",
            },
        )
    if _truthy(payload.get("auto_backfill_automation_member")):
        raise ExternalCampaignError(
            "automation_member_backfill_retired",
            status_code=410,
            message="automation_member backfill is retired; resolve crm_user_identity or use direct send targets instead",
            phase="target_lookup",
            owner_userid=owner_userid,
        )

    effective_recipients = recipients
    if dry_run:
        previews = [
            _preview_single_recipient_direct_send(
                payload=payload,
                recipient=recipient,
                owner_userid=owner_userid,
                group_code=group_code,
                group_label=group_label,
                timezone_name=timezone_name,
                strict_owner_match=strict_owner_match,
                bypass_dnd=bypass_dnd,
                repo=repository,
            )
            for recipient in effective_recipients
        ]
        repository.rollback()
        return {
            "ok": True,
            "dry_run": True,
            "side_effect_executed": False,
            "route_owner": "ai_crm_next",
            "source": "external_token_api",
            "send_path": _EXTERNAL_CAMPAIGN_REQUIRED_SEND_PATH,
            "required_send_path": _EXTERNAL_CAMPAIGN_REQUIRED_SEND_PATH,
            "forbidden_send_path": _EXTERNAL_CAMPAIGN_FORBIDDEN_SEND_PATH,
            "review_required": True,
            "review_status": "pending_review",
            "run_status": "draft",
            "scheduled_jobs": 0,
            "group_code": group_code,
            "group_label": group_label,
            "owner_userid": owner_userid,
            "recipient_count": len(previews),
            "campaigns": [],
            "jobs": [],
            "previews": previews,
        }

    previews = [
        _preview_single_recipient_direct_send(
            payload=payload,
            recipient=recipient,
            owner_userid=owner_userid,
            group_code=group_code,
            group_label=group_label,
            timezone_name=timezone_name,
            strict_owner_match=strict_owner_match,
            bypass_dnd=bypass_dnd,
            repo=repository,
        )
        for recipient in effective_recipients
    ]
    repository.rollback()
    raise ExternalCampaignError(
        "ai_assist_review_required",
        status_code=409,
        message="external campaign requests must create an AI assistant pending_review draft before any broadcast job is queued",
        phase="ai_assist_review_guard",
        owner_userid=owner_userid,
        group_code=group_code,
        campaign_code=_text(payload.get("campaign_code") or payload.get("idempotency_key") or group_code),
        details=_external_campaign_review_details(
            owner_userid=owner_userid,
            group_code=group_code,
            group_label=group_label,
            recipient_count=len(previews),
            previews=previews,
        ),
    )


def create_direct_wecom_private_send(payload: JsonDict, repo: ExternalCampaignRepository | None = None, *, source: str = "direct_send_api") -> JsonDict:
    if not isinstance(payload, dict):
        raise ExternalCampaignError("json object body is required")
    repository = _build_repo(repo)
    sender_userid = _text(payload.get("sender_userid") or payload.get("sender") or payload.get("owner_userid"))
    if not sender_userid:
        raise ExternalCampaignError("sender_userid_required", status_code=400, phase="target_lookup")
    target_id = _text(payload.get("target_id") or payload.get("unionid") or payload.get("external_userid"))
    if not target_id:
        raise ExternalCampaignError("target_identity_not_found", status_code=404, phase="target_lookup")
    content_text = _text(payload.get("content_text") or payload.get("message"))
    content_package = _content_package_from_sources(payload)
    if not content_text and not _has_content_package_materials(content_package):
        if _has_material_refs(content_package):
            raise ExternalCampaignError("material_invalid", status_code=400, phase="content_validation")
        raise ExternalCampaignError("content_required", status_code=400, phase="content_validation")
    scheduled_for = (
        _text(payload.get("scheduled_for") or payload.get("scheduled_at") or payload.get("send_at")) or datetime.now(ZoneInfo(_DEFAULT_TIMEZONE)).isoformat()
    )
    direct_payload = {
        **payload,
        "owner_userid": sender_userid,
        "sender": sender_userid,
        "target_id": target_id,
        "target_id_type": _text(payload.get("target_id_type")) or "auto",
        "scheduled_for": scheduled_for,
        "message": content_text,
        "content_text": content_text,
        "steps": [
            {
                "scheduled_for": scheduled_for,
                "content_text": content_text,
                "content_payload": content_package,
            }
        ],
        "group_code": _slug(payload.get("group_code") or payload.get("idempotency_key") or "direct_send"),
    }
    recipient = {
        "target_id": target_id,
        "target_id_type": _text(payload.get("target_id_type")) or "auto",
        "content_text": content_text,
        "content_payload": content_package,
    }
    if _truthy(payload.get("dry_run")) or _truthy(payload.get("preview")):
        preview = _preview_single_recipient_direct_send(
            payload=direct_payload,
            recipient=recipient,
            owner_userid=sender_userid,
            group_code=direct_payload["group_code"],
            group_label=_text(payload.get("group_label") or payload.get("intent") or "direct_send"),
            timezone_name=_text(payload.get("timezone")) or _DEFAULT_TIMEZONE,
            strict_owner_match=_truthy(payload.get("strict_owner_match")),
            bypass_dnd=_truthy(payload.get("bypass_dnd")),
            repo=repository,
        )
        return {
            "ok": True,
            "route_owner": "ai_crm_next",
            "source": source,
            "send_path": "direct_broadcast_job",
            "dry_run": True,
            "side_effect_executed": False,
            "created_count": 0,
            "existing_count": 0,
            "jobs": [preview],
        }
    jobs = _create_single_recipient_direct_send(
        payload=direct_payload,
        recipient=recipient,
        owner_userid=sender_userid,
        operator=_text(payload.get("operator")) or f"direct:{sender_userid}",
        group_code=direct_payload["group_code"],
        group_label=_text(payload.get("group_label") or payload.get("intent") or "direct_send"),
        timezone_name=_text(payload.get("timezone")) or _DEFAULT_TIMEZONE,
        strict_owner_match=_truthy(payload.get("strict_owner_match")),
        bypass_dnd=_truthy(payload.get("bypass_dnd")),
        repo=repository,
        source_type="direct_send",
    )
    repository.commit()
    return {
        "ok": True,
        "route_owner": "ai_crm_next",
        "source": source,
        "send_path": "direct_broadcast_job",
        "created_count": sum(1 for item in jobs if item.get("status") == "queued"),
        "existing_count": sum(1 for item in jobs if item.get("status") == "exists"),
        "jobs": jobs,
    }


def create_external_campaigns_response(payload: JsonDict) -> JsonDict | JSONResponse:
    try:
        return create_external_campaigns(payload)
    except ExternalCampaignError as exc:
        if isinstance(payload, dict):
            exc.add_context(
                group_code=_slug(payload.get("group_code") or payload.get("idempotency_key") or payload.get("intent")),
                trace_id=_text(payload.get("trace_id")),
                owner_userid=_text(payload.get("owner_userid") or payload.get("sender")),
            )
        return JSONResponse(exc.to_response(), status_code=exc.status_code)
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": "internal_error", "message": str(exc), "route_owner": "ai_crm_next"},
            status_code=500,
        )


def create_direct_wecom_private_send_response(
    payload: JsonDict,
    *,
    source: str,
) -> JsonDict | JSONResponse:
    try:
        return create_direct_wecom_private_send(payload, source=source)
    except ExternalCampaignError as exc:
        return JSONResponse(exc.to_response(), status_code=exc.status_code)
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": "internal_error", "message": str(exc), "route_owner": "ai_crm_next"},
            status_code=500,
        )


def get_external_campaign_status(campaign_code: str, repo: ExternalCampaignRepository | None = None) -> JsonDict:
    normalized_code = _text(campaign_code)
    if not normalized_code:
        raise ExternalCampaignError("campaign_code is required")
    repository = _build_repo(repo)
    campaign = repository.get_campaign_by_code(normalized_code)
    if not campaign:
        raise ExternalCampaignError("campaign_not_found", status_code=404)
    overview = repository.assemble_campaign_overview(campaign_id=int(campaign["id"]))
    return {
        "ok": True,
        "route_owner": "ai_crm_next",
        "campaign": overview.get("campaign") or campaign,
        "segments": overview.get("segments") or [],
        "member_status_counts": overview.get("member_status_counts") or {},
        "total_members": int(overview.get("total_members") or 0),
        "scheduled_jobs": repository.count_open_campaign_jobs(campaign_id=int(campaign["id"])),
    }


def get_external_campaign_status_response(campaign_code: str) -> JsonDict | JSONResponse:
    try:
        return get_external_campaign_status(campaign_code)
    except ExternalCampaignError as exc:
        return JSONResponse(exc.to_response(), status_code=exc.status_code)
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": "internal_error", "message": str(exc), "route_owner": "ai_crm_next"},
            status_code=500,
        )
