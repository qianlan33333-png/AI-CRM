from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from aicrm_next.shared.errors import NotFoundError
from aicrm_next.shared.typing import JsonDict

from .repo import CustomerReadRepository, build_customer_read_model_repository


@dataclass(frozen=True)
class ResolvedSidebarCustomerContext:
    external_userid: str
    unionid: str
    owner_userid: str
    corp_id: str = ""
    owner_verified: bool = False


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

    def execute(
        self,
        *,
        external_userid: str = "",
        context: ResolvedSidebarCustomerContext | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> JsonDict:
        owned_repo = self._repo is None
        repo = self._repo or build_customer_read_model_repository()
        try:
            resolved_external_userid = str(
                (context.external_userid if context else external_userid) or ""
            ).strip()
            unionid = str((context.unionid if context else "") or "").strip()
            if not unionid:
                customer = repo.get_customer(resolved_external_userid)
                if not customer:
                    raise NotFoundError("customer not found")
                unionid = str(
                    customer.get("unionid")
                    or (customer.get("identity") or {}).get("unionid")
                    or ""
                ).strip()
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

    @classmethod
    def _safe_item(cls, item: JsonDict) -> JsonDict:
        event_type = str(item.get("event_type") or "")
        metadata = dict(item.get("metadata") or {})
        allowed = cls.METADATA_ALLOWLIST.get(event_type, set())
        safe_metadata = {
            key: metadata[key]
            for key in allowed
            if key in metadata and isinstance(metadata[key], (str, int, float, bool))
        }
        safe_item = {
            "event_time": str(item.get("event_time") or ""),
            "event_type": event_type,
            "title": str(item.get("title") or ""),
            "summary": str(item.get("summary") or ""),
            "metadata": safe_metadata,
        }
        source_action = cls._source_action(item, event_type=event_type, metadata=safe_metadata)
        if source_action:
            safe_item["source_action"] = source_action
        return safe_item

    @classmethod
    def _source_action(cls, item: JsonDict, *, event_type: str, metadata: JsonDict) -> JsonDict | None:
        source_id = str(item.get("source_id") or "").strip()
        if event_type == "questionnaire_submitted" and source_id:
            return {
                "kind": "questionnaire_submission",
                "label": "查看原纪录",
                "submission_id": source_id,
                "questionnaire_id": str(metadata.get("questionnaire_id") or "").strip(),
            }
        if event_type != "product_enrolled":
            return None
        detail_url = cls._order_detail_url(item)
        if not detail_url:
            return None
        return {
            "kind": "order_detail",
            "label": "查看原纪录",
            "detail_url": detail_url,
        }

    @staticmethod
    def _order_detail_url(item: JsonDict) -> str:
        source_table = str(item.get("source_table") or "").strip()
        source_id = str(item.get("source_id") or "").strip()
        event_id = str(item.get("event_id") or "").strip()
        detail_base = ""
        identifier = ""
        if source_table == "wechat_shop_orders" and source_id:
            detail_base = "/admin/wechat-shop/transactions"
            identifier = source_id
        elif source_table == "wechat_pay_orders" and source_id:
            detail_base = "/admin/wechat-pay/transactions"
            identifier = source_id
        elif event_id.startswith("product:payment:"):
            detail_base = "/admin/wechat-pay/transactions"
            identifier = event_id.removeprefix("product:payment:").strip()
        if not detail_base or not identifier:
            return ""
        return f"{detail_base}/{quote(identifier, safe='')}"

    @staticmethod
    def _close_repository(repo: CustomerReadRepository) -> None:
        close = getattr(repo, "close", None)
        if callable(close):
            close()

    __call__ = execute


__all__ = ["ResolvedSidebarCustomerContext", "SidebarCustomerTimelineQuery"]
