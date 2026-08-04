from __future__ import annotations

from dataclasses import dataclass

import pytest

from aicrm_next.engagement.media_library.variants import add_image_variant_urls, thumbnail_url, variant_url
from aicrm_next.engagement.send_targets.dto import SendTargetRequest
from aicrm_next.engagement.send_targets.resolver import SendTargetError, SendTargetResolver


pytestmark = pytest.mark.unit


@dataclass
class FakeTargetRepository:
    row: dict | None
    dnd: list[dict]

    def fetch_send_target_by_unionid(self, unionid: str) -> dict | None:
        return self.row if self.row and self.row.get("unionid") == unionid else None

    def fetch_send_target_by_external_userid(self, external_userid: str) -> dict | None:
        return self.row if self.row and self.row.get("primary_external_userid") == external_userid else None

    def fetch_do_not_disturb_reasons(self, unionid: str) -> list[dict]:
        return list(self.dnd)


def test_send_target_resolves_canonical_identity() -> None:
    repo = FakeTargetRepository(
        row={"unionid": "union-1", "primary_external_userid": "ext-1", "owner_userid": "owner-1"},
        dnd=[],
    )
    target = SendTargetResolver(repo).resolve(SendTargetRequest(target_id="ext-1", target_id_type="external_userid", sender_userid="staff-1"))
    assert target.unionid == "union-1"
    assert target.external_userid == "ext-1"
    assert target.target_source == "crm_user_identity"


def test_send_target_respects_do_not_disturb_unless_explicitly_bypassed() -> None:
    repo = FakeTargetRepository(
        row={"unionid": "union-1", "primary_external_userid": "ext-1"},
        dnd=[{"reason": "manual_opt_out"}],
    )
    resolver = SendTargetResolver(repo)
    with pytest.raises(SendTargetError) as exc_info:
        resolver.resolve(SendTargetRequest(target_id="union-1", sender_userid="staff-1"))
    assert exc_info.value.error_code == "do_not_disturb"
    bypassed = resolver.resolve(SendTargetRequest(target_id="union-1", sender_userid="staff-1", bypass_dnd=True))
    assert bypassed.warnings[0]["code"] == "do_not_disturb_bypassed"


def test_media_variant_urls_are_derived_from_current_image_id() -> None:
    assert variant_url(12, "web") == "/api/admin/image-library/12/variants/web"
    assert thumbnail_url(12, 320) == "/api/admin/image-library/12/thumbnail?size=320"
    projected = add_image_variant_urls({"id": 12, "updated_at": "2026-08-04T00:00:00Z"})
    assert projected["thumb_320_url"] == variant_url(12, "thumb_320")
    assert projected["preview_url"] == variant_url(12, "mobile_1080")
