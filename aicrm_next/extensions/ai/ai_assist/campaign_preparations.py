from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Protocol
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from aicrm_next.platform.shared.query_telemetry import current_query_count

from .campaign_preparations_dto import (
    CommitCampaignPreparationRequestV1,
    CreateCampaignPreparationRequestV1,
)


class CampaignAdmissionPort(Protocol):
    def evaluate(
        self,
        inputs: Iterable[Mapping[str, Any]],
        *,
        owner_userid: str,
        week_started_at: datetime,
        weekly_limit: int,
    ) -> dict[str, dict[str, Any]]: ...


class DynamicCardMediaPort(Protocol):
    def validate_cover_ids(self, cover_image_ids: Iterable[int]) -> dict[int, str]: ...


class OperationCycleExecutionContextPort(Protocol):
    def get_execution_context(self, strategy_key: str) -> dict[str, Any] | None: ...


class CampaignPreparationCommandPort(Protocol):
    def commit(
        self,
        preparation_id: str,
        *,
        preparation_hash: str,
        actor_id: str,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class CampaignPreparationDependencies:
    context_port: OperationCycleExecutionContextPort
    admission_port: CampaignAdmissionPort
    media_port: DynamicCardMediaPort
    command_port: CampaignPreparationCommandPort


_DEPENDENCIES: CampaignPreparationDependencies | None = None


def configure_campaign_preparation_dependencies(
    dependencies: CampaignPreparationDependencies,
) -> None:
    global _DEPENDENCIES
    _DEPENDENCIES = dependencies


def _dependencies() -> CampaignPreparationDependencies:
    if _DEPENDENCIES is None:
        raise RuntimeError("campaign preparation dependencies are not configured")
    return _DEPENDENCIES
from .campaign_preparations_repo import (
    CampaignPreparationConflict,
    CampaignPreparationRepository,
    build_campaign_preparation_repository,
)
from .card_target_validation_port import (
    CardTargetValidationPort,
    build_card_target_validation_port,
)


class CampaignPreparationError(Exception):
    def __init__(self, code: str, *, status_code: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _path_error(pagepath: str, cid: str) -> str:
    parsed = urlsplit(pagepath)
    if parsed.scheme or parsed.netloc or not parsed.path or not parsed.path.startswith("/"):
        return "pagepath_must_be_relative"
    query = parse_qs(parsed.query, keep_blank_values=True)
    if query.get("cid", [""])[0] != cid:
        return "pagepath_cid_mismatch"
    if query.get("kind", [""])[0] == "review":
        for key in ("rid", "cid", "ch", "src"):
            if not query.get(key, [""])[0]:
                return f"pagepath_{key}_missing"
    return ""


def _duplicate_keys(values: list[str]) -> set[str]:
    counts = Counter(values)
    return {value for value, count in counts.items() if value and count > 1}


def create_campaign_preparation(
    payload: CreateCampaignPreparationRequestV1 | dict[str, Any],
    *,
    actor_id: str,
    repo: CampaignPreparationRepository | None = None,
    context_port: OperationCycleExecutionContextPort | None = None,
    admission_port: CampaignAdmissionPort | None = None,
    target_port: CardTargetValidationPort | None = None,
    media_port: DynamicCardMediaPort | None = None,
) -> dict[str, Any]:
    request = payload if isinstance(payload, CreateCampaignPreparationRequestV1) else CreateCampaignPreparationRequestV1.model_validate(payload)
    repository = repo or build_campaign_preparation_repository()
    query_count_started = current_query_count()
    request_payload = request.model_dump(mode="json")
    preparation_hash = _canonical_hash(request_payload)
    existing = repository.get_by_idempotency_key(request.idempotency_key)
    if existing:
        if existing.get("preparation_hash") != preparation_hash:
            raise CampaignPreparationError("idempotency_payload_conflict", status_code=409)
        return {**existing, "idempotent_existing": True}
    repository.cleanup_expired_staging()

    timings: dict[str, int] = {}
    phase_started = time.perf_counter()
    dependencies = _dependencies() if context_port is None or admission_port is None or media_port is None else None
    context = (context_port or dependencies.context_port).get_execution_context(
        request.strategy_key
    )
    timings["strategy"] = _elapsed_ms(phase_started)
    if not context:
        raise CampaignPreparationError("confirmed_strategy_context_not_found", status_code=409)
    if int(context.get("strategy_version") or 0) != request.strategy_version:
        raise CampaignPreparationError("strategy_version_drift", status_code=409)
    if str(context.get("context_hash") or "") != request.context_hash:
        raise CampaignPreparationError("strategy_context_hash_conflict", status_code=409)
    contract = dict(context.get("execution_contract") or {})
    allowed_owners = [str(value) for value in contract.get("allowed_owner_userids") or []]
    if not allowed_owners or request.owner_userid not in allowed_owners:
        raise CampaignPreparationError("owner_not_allowed_by_strategy", status_code=409)
    if contract.get("review_required") is not True or contract.get("direct_broadcast_jobs_allowed") is not False:
        raise CampaignPreparationError("unsafe_execution_contract", status_code=409)

    row_errors: dict[str, str] = {}
    card_ids = [row.card.card_id for row in request.rows]
    cids = [row.card.cid for row in request.rows]
    duplicate_card_ids = _duplicate_keys(card_ids)
    duplicate_cids = _duplicate_keys(cids)
    for row in request.rows:
        if row.card.card_id in duplicate_card_ids:
            row_errors[row.row_key] = "duplicate_card_id"
        elif row.card.cid in duplicate_cids:
            row_errors[row.row_key] = "duplicate_cid"
        else:
            path_error = _path_error(row.card.pagepath, row.card.cid)
            if path_error:
                row_errors[row.row_key] = path_error

    phase_started = time.perf_counter()
    target_results = (target_port or build_card_target_validation_port()).validate_targets(
        [
            {
                "row_key": row.row_key,
                "appid": row.card.appid,
                "card_id": row.card.card_id,
                "cid": row.card.cid,
                "pagepath": row.card.pagepath,
            }
            for row in request.rows
            if row.row_key not in row_errors
        ]
    )
    timings["path"] = _elapsed_ms(phase_started)
    for row_key, error in target_results.items():
        if error:
            row_errors[row_key] = error

    phase_started = time.perf_counter()
    cover_results = (media_port or dependencies.media_port).validate_cover_ids(
        [row.card.cover_image_id for row in request.rows]
    )
    timings["material"] = _elapsed_ms(phase_started)
    for row in request.rows:
        error = cover_results.get(row.card.cover_image_id, "cover_validation_result_missing")
        if error:
            row_errors[row.row_key] = error

    tz = ZoneInfo(request.timezone)
    local_scheduled = request.scheduled_for
    if local_scheduled.tzinfo is None:
        local_scheduled = local_scheduled.replace(tzinfo=tz)
    else:
        local_scheduled = local_scheduled.astimezone(tz)
    week_started = (local_scheduled - timedelta(days=local_scheduled.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    normalized_identities: list[dict[str, str]] = []
    for row in request.rows:
        normalized_identities.append(
            {
                "row_key": row.row_key,
                "external_userid": str(row.identity.external_userid or "").strip(),
                "unionid": str(row.identity.unionid or "").strip(),
                "mobile": str(row.identity.mobile or "").strip(),
            }
        )
    phase_started = time.perf_counter()
    admissions = (admission_port or dependencies.admission_port).evaluate(
        normalized_identities,
        owner_userid=request.owner_userid,
        week_started_at=week_started.astimezone(timezone.utc),
        weekly_limit=int(contract.get("max_weekly_private_messages") or 0),
    )
    timings["identity_policy"] = _elapsed_ms(phase_started)

    seen_unionids: set[str] = set()
    staging_rows: list[dict[str, Any]] = []
    for row, raw_identity in zip(request.rows, normalized_identities, strict=True):
        admission = dict(admissions.get(row.row_key) or {})
        identity_status = str(admission.get("identity_status") or "unmatched")
        policy_status = str(admission.get("policy_status") or "pending")
        reason_code = str(admission.get("reason_code") or "identity_not_found")
        row_status = "eligible" if identity_status == "resolved" and policy_status == "eligible" else "skipped"
        unionid = str(admission.get("resolved_unionid") or "")
        if unionid and unionid in seen_unionids:
            identity_status = "duplicate"
            policy_status = "duplicate_touch"
            reason_code = "duplicate_recipient_in_preparation"
            row_status = "skipped"
        elif unionid:
            seen_unionids.add(unionid)
        if row.row_key in row_errors:
            row_status = "blocked"
            reason_code = row_errors[row.row_key]
        card = row.card.model_dump(mode="json")
        analysis = {
            **row.analysis,
            "group": row.group,
            "reason_code": row.reason_code,
            "weekly_touch_count": int(admission.get("weekly_touch_count") or 0),
        }
        staging_rows.append(
            {
                "row_key": row.row_key,
                "identity_external_userid": raw_identity["external_userid"],
                "identity_unionid": raw_identity["unionid"],
                "identity_mobile_normalized": str(
                    admission.get("identity_mobile_normalized") or raw_identity["mobile"]
                ),
                "resolved_external_userid": str(admission.get("resolved_external_userid") or ""),
                "resolved_unionid": unionid,
                "resolved_owner_userid": str(admission.get("resolved_owner_userid") or ""),
                "identity_status": identity_status,
                "policy_status": policy_status,
                "row_status": row_status,
                "reason_code": reason_code,
                "content_text": row.content_text,
                "dynamic_card_json": card,
                "analysis_json": analysis,
                "row_hash": _canonical_hash(
                    {
                        "row_key": row.row_key,
                        "identity": raw_identity,
                        "content_text": row.content_text,
                        "card": card,
                        "analysis": analysis,
                    }
                ),
            }
        )

    blockers = [
        {"code": code, "count": count}
        for code, count in sorted(Counter(row_errors.values()).items())
    ]
    if not blockers and not any(row["row_status"] == "eligible" for row in staging_rows):
        blockers.append({"code": "no_eligible_recipients", "count": 1})
    if blockers:
        for row in staging_rows:
            if row["row_status"] == "eligible":
                row["row_status"] = "blocked"
                row["reason_code"] = "batch_structural_blocked"
    reason_counts = Counter(row["reason_code"] for row in staging_rows)
    status_counts = Counter(row["row_status"] for row in staging_rows)
    counts = {
        "input": len(staging_rows),
        "eligible": status_counts.get("eligible", 0),
        "skipped": status_counts.get("skipped", 0),
        "blocked": status_counts.get("blocked", 0),
        "reason_codes": dict(sorted(reason_counts.items())),
    }
    if counts["input"] != counts["eligible"] + counts["skipped"] + counts["blocked"]:
        raise CampaignPreparationError("preparation_conservation_failed")
    preparation_id = f"ecprep_{preparation_hash[:28]}"
    phase_started = time.perf_counter()
    try:
        detail = repository.create(
            {
                "preparation_id": preparation_id,
                "idempotency_key": request.idempotency_key,
                "preparation_hash": preparation_hash,
                "source_hash": request.md_source_hash,
                "strategy_key": request.strategy_key,
                "strategy_version": request.strategy_version,
                "context_hash": request.context_hash,
                "run_key": request.run_key,
                "owner_userid": request.owner_userid,
                "scheduled_for": local_scheduled.astimezone(timezone.utc),
                "timezone": request.timezone,
                "display_name": request.display_name,
                "status": "blocked" if blockers else "ready",
                "input_count": counts["input"],
                "eligible_count": counts["eligible"],
                "skipped_count": counts["skipped"],
                "counts": counts,
                "blockers": blockers,
                "timings_ms": timings,
                "sql_batch_count": 0,
                "_query_count_started": query_count_started,
                "created_by": actor_id or "campaign_agent",
                "expires_at": datetime.now(timezone.utc) + timedelta(hours=24),
            },
            staging_rows,
        )
    except CampaignPreparationConflict as exc:
        raise CampaignPreparationError(str(exc), status_code=409) from exc
    timings["persist"] = _elapsed_ms(phase_started)
    return detail


def get_campaign_preparation(
    preparation_id: str,
    *,
    repo: CampaignPreparationRepository | None = None,
) -> dict[str, Any] | None:
    return (repo or build_campaign_preparation_repository()).get(str(preparation_id or "").strip())


def commit_campaign_preparation(
    preparation_id: str,
    payload: CommitCampaignPreparationRequestV1 | dict[str, Any],
    *,
    actor_id: str,
    repo: CampaignPreparationRepository | None = None,
    context_port: OperationCycleExecutionContextPort | None = None,
    command_port: CampaignPreparationCommandPort | None = None,
) -> dict[str, Any]:
    request = payload if isinstance(payload, CommitCampaignPreparationRequestV1) else CommitCampaignPreparationRequestV1.model_validate(payload)
    detail = (repo or build_campaign_preparation_repository()).get(str(preparation_id or "").strip())
    if detail is None:
        raise CampaignPreparationError("preparation_not_found", status_code=404)
    if detail.get("preparation_hash") != request.preparation_hash:
        raise CampaignPreparationError("preparation_hash_conflict", status_code=409)
    if detail.get("status") != "committed":
        dependencies = _dependencies() if context_port is None or command_port is None else None
        context = (context_port or dependencies.context_port).get_execution_context(
            str(detail.get("strategy_key") or "")
        )
        if not context:
            raise CampaignPreparationError("confirmed_strategy_context_not_found", status_code=409)
        if int(context.get("strategy_version") or 0) != int(detail.get("strategy_version") or 0):
            raise CampaignPreparationError("strategy_version_drift", status_code=409)
        if str(context.get("context_hash") or "") != str(detail.get("context_hash") or ""):
            raise CampaignPreparationError("strategy_context_hash_conflict", status_code=409)
    try:
        dependencies = _dependencies() if command_port is None else None
        result = (command_port or dependencies.command_port).commit(
            str(preparation_id or "").strip(),
            preparation_hash=request.preparation_hash,
            actor_id=actor_id,
        )
    except Exception as exc:
        code = str(getattr(exc, "code", "") or exc or "campaign_preparation_commit_failed")
        status_code = int(getattr(exc, "status_code", 409) or 409)
        raise CampaignPreparationError(code, status_code=status_code) from exc
    return {**result, "preparation_id": str(preparation_id or "").strip()}


__all__ = [
    "CampaignPreparationError",
    "CampaignPreparationDependencies",
    "configure_campaign_preparation_dependencies",
    "commit_campaign_preparation",
    "create_campaign_preparation",
    "get_campaign_preparation",
]
