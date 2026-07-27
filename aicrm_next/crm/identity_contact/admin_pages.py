from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "frontend_compat" / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


@router.get("/sidebar/bind-mobile", name="api.sidebar_bind_mobile_page")
async def sidebar_bind_mobile_page(request: Request):
    return templates.TemplateResponse(
        request,
        "sidebar_customer_workbench.html",
        {"request": request, "debug_enabled": False},
    )
