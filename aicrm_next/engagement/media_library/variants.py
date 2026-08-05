from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from aicrm_next.platform.shared.errors import ContractError


VARIANT_KEYS = {"original", "thumb_160", "thumb_320", "preview_720", "mobile_1080", "large_1440"}
THUMBNAIL_SIZE_TO_VARIANT = {160: "thumb_160", 320: "thumb_320", 720: "preview_720"}
ALLOWED_THUMBNAIL_SIZES = set(THUMBNAIL_SIZE_TO_VARIANT)


def pending_image_placeholder(*, image_id: int | str, size: int = 160) -> dict[str, Any]:
    safe_size = max(1, min(int(size or 160), 720))
    body = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="{0}" height="{0}" viewBox="0 0 {0} {0}">'
        '<rect width="100%" height="100%" fill="#f3f4f6"/>'
        '<text x="50%" y="50%" text-anchor="middle" dominant-baseline="middle" '
        'font-family="sans-serif" font-size="12" fill="#9ca3af">图片处理中</text></svg>'
    ).format(safe_size).encode("utf-8")
    checksum = hashlib.sha256(body).hexdigest()
    return {
        "image_id": image_id,
        "variant_key": f"pending_{safe_size}",
        "bytes": body,
        "mime_type": "image/svg+xml",
        "width": safe_size,
        "height": safe_size,
        "file_size": len(body),
        "checksum": checksum,
        "etag": f'"{checksum}"',
        "generation_required": True,
    }


@dataclass(frozen=True)
class ImageVariant:
    image_id: int | str
    variant_key: str
    storage_backend: str
    storage_key: str
    public_url: str
    mime_type: str
    width: int
    height: int
    file_size: int
    checksum: str
    data_base64: str

    def metadata(self) -> dict[str, Any]:
        return {
            "image_id": self.image_id,
            "variant_key": self.variant_key,
            "storage_backend": self.storage_backend,
            "storage_key": self.storage_key,
            "public_url": self.public_url,
            "mime_type": self.mime_type,
            "width": self.width,
            "height": self.height,
            "file_size": self.file_size,
            "checksum": self.checksum,
        }


def variant_url(image_id: int | str, variant_key: str) -> str:
    return f"/api/admin/image-library/{image_id}/variants/{variant_key}"


def thumbnail_url(image_id: int | str, size: int, updated_at: str = "") -> str:
    url = f"/api/admin/image-library/{image_id}/thumbnail?size={size}"
    if updated_at:
        url += f"&v={updated_at}"
    return url


def add_image_variant_urls(item: dict[str, Any], image_id: int | str | None = None, *, use_thumbnail_fallback: bool = False) -> dict[str, Any]:
    target_id = image_id if image_id not in (None, "") else item.get("id")
    if target_id in (None, ""):
        return item
    updated_at = str(item.get("updated_at") or "")
    if use_thumbnail_fallback:
        item["thumb_160_url"] = thumbnail_url(target_id, 160, updated_at)
        item["thumb_320_url"] = thumbnail_url(target_id, 320, updated_at)
        item["thumb_url"] = item["thumb_160_url"]
        item["preview_url"] = thumbnail_url(target_id, 720, updated_at)
    else:
        item["thumb_160_url"] = variant_url(target_id, "thumb_160")
        item["thumb_320_url"] = variant_url(target_id, "thumb_320")
        item["thumb_url"] = item["thumb_320_url"]
        item["preview_url"] = variant_url(target_id, "mobile_1080")
        item["mobile_1080_url"] = variant_url(target_id, "mobile_1080")
        item["large_1440_url"] = variant_url(target_id, "large_1440")
        item["original_url"] = variant_url(target_id, "original")
    item.setdefault("width", 0)
    item.setdefault("height", 0)
    return item


def _checksum(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _mime_to_format(mime_type: str, *, has_alpha: bool = False) -> tuple[str, str]:
    try:
        from PIL import features

        if features.check("webp"):
            return "WEBP", "image/webp"
    except Exception:
        pass
    if has_alpha:
        return "PNG", "image/png"
    if mime_type in {"image/jpeg", "image/jpg"}:
        return "JPEG", "image/jpeg"
    return "PNG", "image/png"


def _encode_image(image: Any, source_mime_type: str) -> tuple[bytes, str]:
    has_alpha = image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in getattr(image, "info", {}))
    fmt, mime_type = _mime_to_format(source_mime_type, has_alpha=has_alpha)
    output = BytesIO()
    save_kwargs: dict[str, Any] = {}
    if fmt == "JPEG":
        if image.mode not in {"RGB", "L"}:
            image = image.convert("RGB")
        save_kwargs = {"quality": 82, "optimize": True}
    elif fmt == "WEBP":
        save_kwargs = {"quality": 82, "method": 4}
    image.save(output, fmt, **save_kwargs)
    return output.getvalue(), mime_type


def _encode_thumbnail_image(image: Any, source_mime_type: str) -> tuple[bytes, str]:
    has_alpha = image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in getattr(image, "info", {}))
    if has_alpha or source_mime_type == "image/png":
        output = BytesIO()
        image.save(output, "PNG", optimize=True)
        return output.getvalue(), "image/png"
    if image.mode not in {"RGB", "L"}:
        image = image.convert("RGB")
    output = BytesIO()
    image.save(output, "JPEG", quality=82, optimize=True)
    return output.getvalue(), "image/jpeg"


def generate_image_variants(*, image_id: int | str, data_base64: str, mime_type: str) -> dict[str, ImageVariant]:
    raw = validate_image_source_base64(data_base64)
    try:
        from PIL import Image, ImageOps

        source = Image.open(BytesIO(raw))
        source = ImageOps.exif_transpose(source)
        source_width, source_height = source.size
    except Exception as exc:
        raise ContractError("invalid image data") from exc

    variants: dict[str, ImageVariant] = {}

    def add_variant(key: str, image: Any, variant_mime: str | None = None, payload: bytes | None = None) -> None:
        payload_bytes = payload
        out_mime = variant_mime
        if payload_bytes is None:
            payload_bytes, out_mime = _encode_image(image, mime_type)
        width, height = image.size
        variants[key] = ImageVariant(
            image_id=image_id,
            variant_key=key,
            storage_backend="db_base64",
            storage_key=f"image_library/{image_id}/{key}",
            public_url="",
            mime_type=out_mime or mime_type or "image/png",
            width=int(width or 0),
            height=int(height or 0),
            file_size=len(payload_bytes),
            checksum=_checksum(payload_bytes),
            data_base64=base64.b64encode(payload_bytes).decode("ascii"),
        )

    add_variant("original", source, mime_type or "image/png", raw)

    for key, side in (("thumb_160", 160), ("thumb_320", 320)):
        if source_width <= side and source_height <= side:
            thumb = source.copy()
        else:
            thumb = ImageOps.fit(source, (side, side), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        thumb_payload, thumb_mime = _encode_thumbnail_image(thumb, mime_type)
        add_variant(key, thumb, thumb_mime, thumb_payload)

    preview = source.copy()
    preview.thumbnail((720, 720), Image.Resampling.LANCZOS)
    add_variant("preview_720", preview)
    mobile = source.copy()
    mobile.thumbnail((1080, 1080), Image.Resampling.LANCZOS)
    add_variant("mobile_1080", mobile)
    large = source.copy()
    large.thumbnail((1440, 1440), Image.Resampling.LANCZOS)
    add_variant("large_1440", large)
    return variants


def variant_bytes(variant: dict[str, Any]) -> bytes:
    try:
        return base64.b64decode(str(variant.get("data_base64") or ""), validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ContractError("invalid image data") from exc


def make_thumbnail_bytes(*, image_id: int | str, data: bytes, mime_type: str, size: int) -> dict[str, Any]:
    if size not in ALLOWED_THUMBNAIL_SIZES:
        raise ContractError("thumbnail size must be one of 160, 320, 720")
    if not data:
        raise ContractError("image data is empty")
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError

        source = Image.open(BytesIO(data))
        source = ImageOps.exif_transpose(source)
    except UnidentifiedImageError as exc:
        raise ContractError("unsupported image type") from exc
    except Exception as exc:
        raise ContractError("invalid image data") from exc

    try:
        if size in {160, 320}:
            if source.width <= size and source.height <= size:
                image = source.copy()
            else:
                image = ImageOps.fit(source, (size, size), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        else:
            image = source.copy()
            image.thumbnail((size, size), Image.Resampling.LANCZOS)
        payload, out_mime = _encode_thumbnail_image(image, mime_type or "image/png")
    except Exception as exc:
        raise ContractError("invalid image data") from exc
    return {
        "image_id": image_id,
        "variant_key": THUMBNAIL_SIZE_TO_VARIANT[size],
        "mime_type": out_mime,
        "width": int(image.width or 0),
        "height": int(image.height or 0),
        "file_size": len(payload),
        "checksum": _checksum(payload),
        "bytes": payload,
        "etag": '"' + _checksum(payload) + '"',
    }


def decode_image_base64(data_base64: str) -> bytes:
    try:
        return base64.b64decode(str(data_base64 or ""), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ContractError("invalid image data") from exc


def validate_image_source_base64(data_base64: str) -> bytes:
    raw = decode_image_base64(data_base64)
    if not raw:
        raise ContractError("image data is empty")
    try:
        from PIL import Image, UnidentifiedImageError

        with Image.open(BytesIO(raw)) as image:
            image.verify()
    except UnidentifiedImageError as exc:
        raise ContractError("unsupported image type") from exc
    except Exception as exc:
        raise ContractError("invalid image data") from exc
    return raw
