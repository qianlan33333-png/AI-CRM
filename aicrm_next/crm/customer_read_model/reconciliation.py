from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from aicrm_next.shared.typing import JsonDict

from .repo import CustomerReadRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mask_sample(value: object) -> object:
    if value in (None, ""):
        return value
    text = str(value)
    if len(text) <= 4:
        return "***"
    return f"{text[:2]}***{text[-2:]}"


@dataclass(frozen=True)
class CustomerReadModelReconciliationRun:
    run_id: str = field(default_factory=lambda: uuid4().hex)
    status: str = "completed"
    source_count: int = 0
    target_count: int = 0
    diff_count: int = 0
    missing_in_target: list[str] = field(default_factory=list)
    missing_in_source: list[str] = field(default_factory=list)
    field_diffs: list[JsonDict] = field(default_factory=list)
    started_at: str = field(default_factory=_now)
    completed_at: str = field(default_factory=_now)

    def to_dict(self) -> JsonDict:
        return asdict(self)


def reconcile_customer_read_model(
    *,
    source_customers: list[JsonDict],
    target_repo: CustomerReadRepository,
    sample_limit: int = 5,
) -> CustomerReadModelReconciliationRun:
    source_by_id = {
        str(item.get("unionid") or dict(item.get("identity") or {}).get("unionid") or "").strip(): item
        for item in source_customers
        if str(item.get("unionid") or dict(item.get("identity") or {}).get("unionid") or "").strip()
    }
    target_customers = target_repo.list_customers(limit=None, offset=0)
    target_by_id = {
        str(item.get("unionid") or dict(item.get("identity") or {}).get("unionid") or "").strip(): item
        for item in target_customers
        if str(item.get("unionid") or dict(item.get("identity") or {}).get("unionid") or "").strip()
    }
    source_ids = set(source_by_id)
    target_ids = set(target_by_id)
    missing_in_target = sorted(source_ids - target_ids)
    missing_in_source = sorted(target_ids - source_ids)
    field_diffs: list[JsonDict] = []
    for unionid in sorted(source_ids & target_ids):
        source = source_by_id[unionid]
        target = target_by_id[unionid]
        for field_name in ("customer_name", "owner_userid", "mobile", "binding_status"):
            source_value = source.get(field_name)
            target_value = target.get(field_name)
            if source_value != target_value:
                field_diffs.append(
                    {
                        "unionid": mask_sample(unionid),
                        "field": field_name,
                        "source": mask_sample(source_value),
                        "target": mask_sample(target_value),
                    }
                )
                break
        if len(field_diffs) >= sample_limit:
            break
    return CustomerReadModelReconciliationRun(
        source_count=len(source_by_id),
        target_count=len(target_by_id),
        diff_count=len(missing_in_target) + len(missing_in_source) + len(field_diffs),
        missing_in_target=[str(mask_sample(item)) for item in missing_in_target[:sample_limit]],
        missing_in_source=[str(mask_sample(item)) for item in missing_in_source[:sample_limit]],
        field_diffs=field_diffs,
    )
