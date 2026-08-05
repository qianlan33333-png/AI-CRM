from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO
from contextlib import nullcontext

import pytest
from fastapi import BackgroundTasks
from PIL import Image
from starlette.requests import Request

from aicrm_next.crm.customer_read_model import api as sidebar_api
from aicrm_next.engagement.media_library import api as media_api
from aicrm_next.engagement.media_library.postgres_repo import PostgresMediaLibraryRepository
from aicrm_next.engagement.media_library.variants import (
    add_image_variant_urls,
    generate_image_variants,
    pending_image_placeholder,
    thumbnail_url,
    variant_url,
)
from aicrm_next.engagement.send_targets.dto import SendTargetRequest
from aicrm_next.engagement.send_targets.resolver import SendTargetError, SendTargetResolver
from aicrm_next.platform.shared.errors import ContractError


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


def test_missing_thumbnail_uses_a_non_persistent_placeholder_contract() -> None:
    placeholder = pending_image_placeholder(image_id="42", size=160)
    assert placeholder["generation_required"] is True
    assert placeholder["mime_type"] == "image/svg+xml"
    assert placeholder["bytes"].startswith(b"<svg")


def _sample_png_base64() -> str:
    output = BytesIO()
    Image.new("RGB", (480, 360), (38, 99, 235)).save(output, "PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def test_generated_thumbnail_variants_use_the_binary_contract_accepted_by_thumbnail_routes() -> None:
    variants = generate_image_variants(image_id="42", data_base64=_sample_png_base64(), mime_type="image/png")

    assert variants["thumb_160"].mime_type in {"image/png", "image/jpeg"}
    assert variants["thumb_320"].mime_type in {"image/png", "image/jpeg"}
    assert variants["thumb_160"].file_size > 0
    assert variants["thumb_320"].file_size > 0
    assert (variants["thumb_160"].width, variants["thumb_160"].height) == (160, 160)
    assert (variants["thumb_320"].width, variants["thumb_320"].height) == (320, 320)


@pytest.mark.parametrize("payload", ["", "bm90LWFuLWltYWdl"])
def test_invalid_original_image_data_is_terminal_instead_of_generating_fallback_placeholders(payload: str) -> None:
    with pytest.raises(ContractError):
        generate_image_variants(image_id="42", data_base64=payload, mime_type="image/png")


def test_pending_thumbnail_response_is_no_store_retryable_and_schedules_once(monkeypatch: pytest.MonkeyPatch) -> None:
    class PendingQuery:
        def __call__(self, image_id: str, size: int) -> dict:
            return {"thumbnail": pending_image_placeholder(image_id=image_id, size=size)}

    monkeypatch.setattr(media_api, "GetImageThumbnailQuery", lambda: PendingQuery())
    monkeypatch.setattr(media_api, "media_binary_admission", lambda **_kwargs: nullcontext())
    tasks = BackgroundTasks()
    response = media_api.get_image_thumbnail(tasks, "42", 160, None)

    assert response.status_code == 200
    assert response.headers["x-aicrm-media-generation"] == "pending"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["retry-after"] == "1"
    assert len(tasks.tasks) == 1


def test_pending_variant_response_uses_the_same_retry_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    class PendingQuery:
        def __call__(self, image_id: str, variant_key: str) -> dict:
            return {"variant": pending_image_placeholder(image_id=image_id, size=160)}

    monkeypatch.setattr(media_api, "GetImageVariantQuery", lambda: PendingQuery())
    monkeypatch.setattr(media_api, "media_binary_admission", lambda **_kwargs: nullcontext())
    tasks = BackgroundTasks()
    response = media_api.get_image_variant(tasks, "42", "thumb_160", None)

    assert response.status_code == 200
    assert response.headers["x-aicrm-media-generation"] == "pending"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["retry-after"] == "1"
    assert len(tasks.tasks) == 1


def test_sidebar_pending_thumbnail_uses_the_same_retry_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    class PendingReadModel:
        def thumbnail(self, image_id: int) -> dict:
            placeholder = pending_image_placeholder(image_id=image_id, size=160)
            return {
                "body": placeholder["bytes"],
                "mime_type": placeholder["mime_type"],
                "etag": placeholder["etag"],
                "generation_required": True,
            }

    monkeypatch.setattr(sidebar_api, "SidebarMaterialReadModel", PendingReadModel)
    monkeypatch.setattr(sidebar_api, "media_binary_admission", lambda **_kwargs: nullcontext())
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/sidebar/v2/materials/image/42/thumbnail",
        "headers": [],
        "query_string": b"v=versioned",
        "scheme": "https",
        "server": ("testserver", 443),
        "client": ("testclient", 123),
    })
    tasks = BackgroundTasks()
    response = sidebar_api.get_sidebar_v2_image_thumbnail(request, tasks, 42)

    assert response.status_code == 200
    assert response.headers["x-aicrm-media-generation"] == "pending"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["retry-after"] == "1"
    assert len(tasks.tasks) == 1


def test_ready_thumbnail_response_is_real_image_with_immutable_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = base64.b64decode(_sample_png_base64())

    class ReadyQuery:
        def __call__(self, image_id: str, size: int) -> dict:
            return {
                "thumbnail": {
                    "image_id": image_id,
                    "bytes": payload,
                    "mime_type": "image/png",
                    "etag": '"ready"',
                    "generation_required": False,
                }
            }

    monkeypatch.setattr(media_api, "GetImageThumbnailQuery", lambda: ReadyQuery())
    monkeypatch.setattr(media_api, "media_binary_admission", lambda **_kwargs: nullcontext())
    tasks = BackgroundTasks()
    response = media_api.get_image_thumbnail(tasks, "42", 160, None)

    assert response.status_code == 200
    assert response.media_type == "image/png"
    assert response.body == payload
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "x-aicrm-media-generation" not in response.headers
    assert len(tasks.tasks) == 0


class _OneRowCursor:
    def __init__(self, row: dict) -> None:
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, *_args) -> None:
        return None

    def fetchone(self) -> dict:
        return self.row


class _OneRowConnection:
    def __init__(self, row: dict) -> None:
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def cursor(self) -> _OneRowCursor:
        return _OneRowCursor(self.row)


def test_repository_returns_terminal_error_when_image_has_no_usable_source(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = PostgresMediaLibraryRepository("postgresql://unused")
    row = {"id": 42, "data_base64": "", "mime_type": "image/png", "source_url": ""}
    monkeypatch.setattr(repo, "_connect", lambda: _OneRowConnection(row))
    monkeypatch.setattr(repo, "_image_variants_table_exists", lambda _cursor: True)
    monkeypatch.setattr(repo, "_fetch_variant", lambda *_args, **_kwargs: None)

    with pytest.raises(ContractError, match="image source is unavailable"):
        repo.get_image_thumbnail("42", 160)
    with pytest.raises(ContractError, match="image source is unavailable"):
        repo.get_image_variant("42", "thumb_160")


def test_repository_returns_terminal_error_when_original_binary_is_not_an_image(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = PostgresMediaLibraryRepository("postgresql://unused")
    row = {
        "id": 42,
        "data_base64": base64.b64encode(b"not-an-image").decode("ascii"),
        "mime_type": "image/png",
        "source_url": "",
    }
    monkeypatch.setattr(repo, "_connect", lambda: _OneRowConnection(row))
    monkeypatch.setattr(repo, "_image_variants_table_exists", lambda _cursor: True)
    monkeypatch.setattr(repo, "_fetch_variant", lambda *_args, **_kwargs: None)

    with pytest.raises(ContractError, match="unsupported image type"):
        repo.get_image_thumbnail("42", 160)
