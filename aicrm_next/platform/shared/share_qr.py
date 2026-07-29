from __future__ import annotations

import base64
import re
from io import BytesIO

from aicrm_next.platform.shared.errors import ContractError


def safe_qr_download_filename(title: str, *, fallback: str = "二维码") -> str:
    base = re.sub(r'[\\/:*?"<>|]+', "_", str(title or "").strip()).strip(" ._") or fallback
    return f"{base}二维码.jpg"


def image_bytes_to_jpeg(image_bytes: bytes) -> bytes:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(BytesIO(bytes(image_bytes or b""))) as source:
            source.load()
            if source.mode in {"RGBA", "LA"} or "transparency" in source.info:
                rgba = source.convert("RGBA")
                image = Image.new("RGB", rgba.size, "white")
                image.paste(rgba, mask=rgba.getchannel("A"))
            else:
                image = source.convert("RGB")
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ContractError("image payload is invalid") from exc

    output = BytesIO()
    image.save(output, format="JPEG", quality=100, subsampling=0, optimize=True)
    return output.getvalue()


def jpeg_qr_data_url(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ContractError("share url is required")

    import segno

    qr = segno.make(normalized, error="m", micro=False)
    buffer = BytesIO()
    qr.save(buffer, kind="png", scale=12, border=4, dark="black", light="white")
    encoded = base64.b64encode(image_bytes_to_jpeg(buffer.getvalue())).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"
