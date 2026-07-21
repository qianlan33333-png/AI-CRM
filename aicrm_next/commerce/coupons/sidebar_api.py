from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from aicrm_next.shared.errors import ContractError
from aicrm_next.shared.public_url import canonical_public_base_url
from aicrm_next.shared.repository_provider import RepositoryProviderError
from aicrm_next.shared.sidebar_access import sidebar_owner_context_from_request

from .application import CouponSidebarApplication


router = APIRouter()

_HEADERS = {
    "X-AICRM-Route-Owner": "ai_crm_next",
    "X-AICRM-Fallback-Used": "false",
    "X-AICRM-Real-External-Call-Executed": "false",
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}


@router.get("/api/sidebar/v2/coupons", name="api.sidebar_v2_coupons")
def list_sidebar_coupons(request: Request) -> JSONResponse:
    sidebar_owner_context_from_request(request)
    try:
        payload = CouponSidebarApplication().list_claimable(
            public_base_url=canonical_public_base_url(request),
        )
    except RepositoryProviderError as exc:
        return JSONResponse(
            {
                "ok": False,
                "degraded": True,
                "source_status": "production_unavailable",
                "error_code": "sidebar_coupons_unavailable",
                "page_error": str(exc),
                "route_owner": "ai_crm_next",
                "fallback_used": False,
            },
            status_code=503,
            headers=_HEADERS,
        )
    except ContractError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        return JSONResponse(
            {
                "ok": False,
                "degraded": True,
                "source_status": "production_unavailable",
                "error_code": "sidebar_coupons_unavailable",
                "page_error": str(exc),
                "route_owner": "ai_crm_next",
                "fallback_used": False,
            },
            status_code=503,
            headers=_HEADERS,
        )
    return JSONResponse(jsonable_encoder({**payload, "route_owner": "ai_crm_next"}), headers=_HEADERS)
