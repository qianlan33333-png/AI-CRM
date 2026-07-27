from __future__ import annotations

import base64
import re
from io import BytesIO
from urllib.parse import quote

from aicrm_next.platform.shared.errors import ContractError


def safe_qr_download_filename(title: str, *, fallback: str = "二维码") -> str:
    base = re.sub(r'[\\/:*?"<>|]+', "_", str(title or "").strip()) or fallback
    return f"{base}二维码.svg"


def svg_qr_data_url(value: str, *, encoding: str = "base64") -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ContractError("share url is required")

    import segno

    qr = segno.make(normalized, error="m", micro=False)
    buffer = BytesIO()
    qr.save(buffer, kind="svg", scale=6, xmldecl=False, svgns=True, nl=False)
    if encoding == "url":
        return "data:image/svg+xml;charset=UTF-8," + quote(buffer.getvalue().decode("utf-8"))
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"
