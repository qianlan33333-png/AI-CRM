from __future__ import annotations

from aicrm_next.shared.errors import NotFoundError
from aicrm_next.shared.typing import JsonDict

from .repo import CustomerReadRepository, build_customer_read_model_repository


class SidebarCustomerTimelineQuery:
    """Strict primary read for the signed sidebar's four business activity types."""

    EVENT_TYPES = (
        "channel_entry",
        "questionnaire_submitted",
        "product_enrolled",
        "radar_opened",
    )
    METADATA_ALLOWLIST = {
        "channel_entry": {"channel_name", "channel_code"},
        "questionnaire_submitted": {"questionnaire_id", "questionnaire_title"},
        "product_enrolled": {"product_id", "product_title", "product_type"},
        "radar_opened": {"radar_id", "radar_title", "target_type"},
    }

    def __init__(self, repo: CustomerReadRepository | None = None) -> None:
        self._repo = repo

    def execute(self, *, external_userid: str, limit: int = 20, offset: int = 0) -> JsonDict:
        owned_repo = self._repo is None
        repo = self._repo or build_customer_read_model_repository()
        try:
            customer = repo.get_customer(str(external_userid or "").strip())
            if not customer:
                raise NotFoundError("customer not found")
            unionid = str(customer.get("unionid") or (customer.get("identity") or {}).get("unionid") or "").strip()
            if not unionid:
                return {"ok": True, "items": [], "total": 0, "has_more": False, "next_offset": 0}
            filters = {"event_types": list(self.EVENT_TYPES)}
            items = repo.list_timeline_by_unionid(
                unionid,
                filters,
                limit=max(1, min(int(limit), 100)),
                offset=max(0, int(offset)),
            )
            counter = getattr(repo, "count_timeline_by_unionid", None)
            total = int(counter(unionid, filters)) if callable(counter) else len(repo.list_timeline_by_unionid(unionid, filters))
            safe_items = [self._safe_item(item) for item in items]
            next_offset = max(0, int(offset)) + len(safe_items)
            return {
                "ok": True,
                "items": safe_items,
                "total": total,
                "has_more": next_offset < total,
                "next_offset": next_offset,
            }
        finally:
            if owned_repo:
                self._close_repository(repo)

    def _safe_item(self, item: JsonDict) -> JsonDict:
        event_type = str(item.get("event_type") or "")
        metadata = dict(item.get("metadata") or {})
        allowed = self.METADATA_ALLOWLIST.get(event_type, set())
        safe_metadata = {
            key: metadata[key]
            for key in allowed
            if key in metadata and isinstance(metadata[key], (str, int, float, bool))
        }
        return {
            "event_time": str(item.get("event_time") or ""),
            "event_type": event_type,
            "title": str(item.get("title") or ""),
            "summary": str(item.get("summary") or ""),
            "metadata": safe_metadata,
        }

    @staticmethod
    def _close_repository(repo: CustomerReadRepository) -> None:
        close = getattr(repo, "close", None)
        if callable(close):
            close()

    __call__ = execute


__all__ = ["SidebarCustomerTimelineQuery"]
