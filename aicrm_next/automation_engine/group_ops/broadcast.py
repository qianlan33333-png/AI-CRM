from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from aicrm_next.integration_gateway.lesson_card_cover_client import (
    LessonCardCoverClientError,
    build_lesson_card_cover_client,
)
from aicrm_next.platform_foundation.external_effects.adapters import ExternalEffectAdapterRegistry
from aicrm_next.platform_foundation.external_effects.models import utcnow
from aicrm_next.platform_foundation.external_effects.repo import (
    ExternalEffectRepository,
    build_external_effect_repository,
)

from .durable_effects_repository import (
    GroupOpsEffectGraphRepository,
    GroupOpsEffectGraphRequest,
    GroupOpsEffectMaterial,
    build_group_ops_effect_graph_repository,
)
from .domain import clean_text
from .dto import GroupOpsTokenBroadcastRequest
from .repo import GroupOpsRepository, build_group_ops_repository


ROUTE_OWNER = "ai_crm_next"
SOURCE_ROUTE = "/api/automation/group-ops/broadcast"
DEFAULT_PLAN_ID = 11
DEFAULT_MINIPROGRAM_APPID = "wx0ca836834b18e989"
MAX_TEXT_LENGTH = 4000
MAX_IMAGES = 3
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = MAX_IMAGES * MAX_IMAGE_BYTES
_CARD_PATH = "pages/article/article"
_TITLE_PATTERN = re.compile(r"《([^》]+)》")


class GroupOpsBroadcastError(RuntimeError):
    def __init__(self, error_code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code


@dataclass(frozen=True)
class BroadcastImage:
    file_name: str
    content_type: str
    file_bytes: bytes


@dataclass(frozen=True)
class ParsedCardPath:
    normalized_path: str
    lesson_id: str


def parse_card_path(value: str) -> ParsedCardPath:
    raw = clean_text(value)
    parsed = urlsplit(raw)
    if parsed.scheme or parsed.netloc or parsed.path != _CARD_PATH or parsed.fragment:
        raise GroupOpsBroadcastError("invalid_card_path", "card_path must be a canonical article mini-program path")
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise GroupOpsBroadcastError("invalid_card_path", "card_path query is invalid") from exc
    lesson_values = query.get("lesson_id") or []
    from_values = query.get("from") or []
    if len(lesson_values) != 1 or from_values != ["learn"] or set(query) != {"lesson_id", "from"}:
        raise GroupOpsBroadcastError("invalid_card_path", "card_path must include lesson_id and from=learn")
    try:
        lesson_id = str(UUID(clean_text(lesson_values[0])))
    except (ValueError, AttributeError) as exc:
        raise GroupOpsBroadcastError("invalid_card_path", "card_path lesson_id must be a UUID") from exc
    return ParsedCardPath(
        normalized_path=f"{_CARD_PATH}?lesson_id={lesson_id}&from=learn",
        lesson_id=lesson_id,
    )


def _bounded_utf8(value: str, *, max_bytes: int) -> str:
    result = ""
    for char in clean_text(value):
        candidate = result + char
        if len(candidate.encode("utf-8")) > max_bytes:
            break
        result = candidate
    return result


def derive_card_title(text: str, explicit_title: str = "") -> str:
    title = clean_text(explicit_title)
    if not title:
        match = _TITLE_PATTERN.search(clean_text(text))
        title = clean_text(match.group(1)) if match else "黄小璨 AI 日课"
    return _bounded_utf8(title, max_bytes=128) or "黄小璨 AI 日课"


def _detected_image_type(file_bytes: bytes) -> str:
    data = bytes(file_bytes or b"")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def validate_image(image: BroadcastImage) -> BroadcastImage:
    size = len(image.file_bytes or b"")
    if not size:
        raise GroupOpsBroadcastError("empty_image", "uploaded image is empty")
    if size > MAX_IMAGE_BYTES:
        raise GroupOpsBroadcastError("image_too_large", "each image must be at most 10 MB")
    detected = _detected_image_type(image.file_bytes)
    if not detected:
        raise GroupOpsBroadcastError("invalid_image_content", "uploaded image content is not supported")
    declared = clean_text(image.content_type).lower()
    if declared and declared not in {detected, "image/jpg" if detected == "image/jpeg" else detected}:
        raise GroupOpsBroadcastError("image_content_type_mismatch", "uploaded image content type does not match its bytes")
    return BroadcastImage(
        file_name=clean_text(image.file_name) or "broadcast-image",
        content_type=detected,
        file_bytes=bytes(image.file_bytes),
    )


class ExecuteGroupOpsTokenBroadcastCommand:
    def __init__(
        self,
        *,
        group_repo: GroupOpsRepository | None = None,
        external_effect_repo: ExternalEffectRepository | None = None,
        external_effect_adapter_registry: ExternalEffectAdapterRegistry | None = None,
        effect_graph_repo: GroupOpsEffectGraphRepository | None = None,
    ) -> None:
        self._group_repo = group_repo or build_group_ops_repository()
        self._external_effect_repo = external_effect_repo or build_external_effect_repository()
        self._effect_graph_repo = effect_graph_repo or build_group_ops_effect_graph_repository(
            external_effect_repo=self._external_effect_repo,
        )
        # Retained only as a constructor compatibility parameter. HTTP no
        # longer owns a worker or provider adapter.
        self._external_effect_adapter_registry = external_effect_adapter_registry

    def __call__(
        self,
        request: GroupOpsTokenBroadcastRequest,
        *,
        idempotency_key: str,
        images: list[BroadcastImage] | None = None,
        actor_id: str = "external_group_ops_api",
    ) -> dict[str, Any]:
        key = clean_text(idempotency_key or request.idempotency_key)
        if not key:
            raise GroupOpsBroadcastError("idempotency_key_required", "Idempotency-Key is required")
        if len(key) > 200:
            raise GroupOpsBroadcastError("invalid_idempotency_key", "Idempotency-Key is too long")

        text = clean_text(request.text)
        if len(text) > MAX_TEXT_LENGTH:
            raise GroupOpsBroadcastError("text_too_long", f"text must be at most {MAX_TEXT_LENGTH} characters")
        uploaded_images = list(images or [])
        existing_media_ids = [clean_text(item) for item in request.image_media_ids if clean_text(item)]
        if any(len(item) > 255 or any(char.isspace() for char in item) for item in existing_media_ids):
            raise GroupOpsBroadcastError("invalid_image_media_id", "image media ids must be non-whitespace values up to 255 characters")
        if len(uploaded_images) + len(existing_media_ids) > MAX_IMAGES:
            raise GroupOpsBroadcastError("too_many_images", f"at most {MAX_IMAGES} images are allowed")
        if sum(len(item.file_bytes or b"") for item in uploaded_images) > MAX_TOTAL_IMAGE_BYTES:
            raise GroupOpsBroadcastError("images_too_large", "total uploaded images are too large")

        parsed_card = parse_card_path(request.card_path) if clean_text(request.card_path) else None
        if not text and not parsed_card and not uploaded_images and not existing_media_ids:
            raise GroupOpsBroadcastError("broadcast_content_required", "text, images, or card_path is required")

        plan_id = self._plan_id()
        plan = self._group_repo.get_plan(plan_id)
        if not plan:
            raise GroupOpsBroadcastError("broadcast_plan_not_found", "configured group broadcast plan was not found", status_code=404)
        if plan.get("plan_type") != "webhook":
            raise GroupOpsBroadcastError("broadcast_plan_invalid", "configured group broadcast plan must be a webhook plan", status_code=409)
        if plan.get("status") != "active":
            raise GroupOpsBroadcastError("broadcast_plan_inactive", "configured group broadcast plan is not active", status_code=409)
        if not self._group_repo.list_bound_groups(plan_id):
            raise GroupOpsBroadcastError("broadcast_groups_missing", "configured group broadcast plan has no bound groups", status_code=409)
        attachments: list[dict[str, Any]] = [{"msgtype": "image", "image": {"media_id": media_id}} for media_id in existing_media_ids]
        materials: list[GroupOpsEffectMaterial] = []
        for index, image in enumerate(uploaded_images):
            normalized = validate_image(image)
            materials.append(
                GroupOpsEffectMaterial(
                    material_key=f"image:{index + 1}",
                    role="image",
                    file_name=normalized.file_name,
                    content_type=normalized.content_type,
                    file_bytes=normalized.file_bytes,
                )
            )

        card_title = ""
        if parsed_card:
            card_image = self._download_lesson_cover(parsed_card.lesson_id)
            card_title = derive_card_title(text, request.card_title)
            materials.append(
                GroupOpsEffectMaterial(
                    material_key="card-cover",
                    role="card_cover",
                    file_name=card_image.file_name,
                    content_type=card_image.content_type,
                    file_bytes=card_image.file_bytes,
                    attachment_payload={
                        "appid": self._miniprogram_appid(),
                        "page": parsed_card.normalized_path,
                        "title": card_title,
                    },
                )
            )
        chat_ids = [clean_text(item.get("chat_id")) for item in self._group_repo.list_bound_groups(plan_id) if clean_text(item.get("chat_id"))]
        graph = self._effect_graph_repo.plan(
            GroupOpsEffectGraphRequest(
                idempotency_key=f"group-ops-token-broadcast:{plan_id}:{key}",
                source_kind="direct_send",
                plan_id=plan_id,
                chat_ids=chat_ids,
                content_payload={
                    "channel": "wecom_customer_group",
                    "sender": clean_text(plan.get("owner_userid")),
                    "chat_ids": chat_ids,
                    "text": {"content": text} if text else {},
                    "attachments": attachments,
                },
                content_summary=text or f"{len(attachments) + len(materials)} attachments",
                actor_id=clean_text(actor_id) or "external_group_ops_api",
                owner_userid=clean_text(plan.get("owner_userid")),
                webhook_key=clean_text(plan.get("webhook_key")),
                source_module="automation_engine.group_ops.token_broadcast",
                source_route=SOURCE_ROUTE,
                source_command_id=f"group-ops-token-broadcast:{key}",
                scheduled_at=utcnow(),
                materials=tuple(materials),
            )
        )
        return {
            "ok": True,
            "accepted": True,
            **graph,
            "external_effect_job_id": int(graph["final_effect_job_id"]),
            "event_id": 0,
            "content": {
                "text_present": bool(text),
                "image_count": len(existing_media_ids) + len(uploaded_images),
                "uploaded_image_count": len(uploaded_images),
                "card_attached": bool(parsed_card),
                "card_title": card_title,
            },
            "route_owner": ROUTE_OWNER,
        }

    def _plan_id(self) -> int:
        try:
            value = int(clean_text(os.getenv("AICRM_GROUP_OPS_BROADCAST_PLAN_ID")) or DEFAULT_PLAN_ID)
        except ValueError as exc:
            raise GroupOpsBroadcastError("broadcast_plan_not_configured", "group broadcast plan id is invalid", status_code=503) from exc
        if value <= 0:
            raise GroupOpsBroadcastError("broadcast_plan_not_configured", "group broadcast plan id is invalid", status_code=503)
        return value

    def _miniprogram_appid(self) -> str:
        appid = clean_text(os.getenv("AICRM_GROUP_OPS_MINIPROGRAM_APPID")) or DEFAULT_MINIPROGRAM_APPID
        if not appid:
            raise GroupOpsBroadcastError("miniprogram_appid_not_configured", "mini-program appid is not configured", status_code=503)
        return appid

    def _download_lesson_cover(self, lesson_id: str) -> BroadcastImage:
        try:
            cover = build_lesson_card_cover_client().download(lesson_id)
        except LessonCardCoverClientError as exc:
            raise GroupOpsBroadcastError("lesson_cover_download_failed", "lesson card cover download failed", status_code=502) from exc
        return validate_image(
            BroadcastImage(
                file_name=cover.file_name,
                content_type=cover.content_type,
                file_bytes=cover.file_bytes,
            )
        )
