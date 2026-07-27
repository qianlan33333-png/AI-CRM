from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from aicrm_next.platform.shared.runtime_settings import environment_fallback


router = APIRouter()

_ROOT = Path(__file__).resolve().parents[3]
_VERIFICATION_DIR_ENV = "AICRM_DOMAIN_VERIFICATION_DIR"
_VERIFY_FILE_RE = re.compile(r"^(?:WW|MP)_verify_[A-Za-z0-9_-]+\.txt$")


@router.get("/{filename}", name="wechat_domain_verification_file")
def wechat_domain_verification_file(filename: str) -> PlainTextResponse:
    if not _VERIFY_FILE_RE.fullmatch(filename):
        raise HTTPException(status_code=404, detail="Not Found")
    verification_root = Path(environment_fallback(_VERIFICATION_DIR_ENV, str(_ROOT)) or _ROOT)
    path = verification_root / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not Found")
    return PlainTextResponse(
        path.read_text(encoding="utf-8").strip(),
        headers={"Cache-Control": "no-store"},
    )
