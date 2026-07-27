from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from aicrm_next.platform.shared.errors import ContractError
from aicrm_next.platform.shared.repository_provider import RepositoryProviderError

from .application import (
    GetMaterialAssetUsageQuery,
    ListMaterialAssetsQuery,
    ListMaterialPickerItemsQuery,
    NormalizeSendContentPackageCommand,
    PreviewSendContentPackageQuery,
    ValidateMaterialAssetsQuery,
    send_content_production_unavailable_payload,
)
from .dto import MaterialAssetsValidateRequest, MaterialPickerListRequest, SendContentPreviewRequest, SendContentValidateRequest


router = APIRouter()


@router.post("/api/admin/send-content/validate")
def validate_send_content(payload: dict[str, Any]) -> JSONResponse:
    try:
        request = SendContentValidateRequest.model_validate(payload)
        content_package = NormalizeSendContentPackageCommand()(
            request.content_package,
            text_enabled=request.text_enabled,
            require_body=request.require_body,
        )
        return JSONResponse({"ok": True, "content_package": content_package})
    except ValidationError:
        return _error("请求参数格式不正确", status_code=400)
    except ContractError as exc:
        return _error(str(exc), status_code=400)


@router.post("/api/admin/send-content/preview")
def preview_send_content(payload: dict[str, Any]) -> JSONResponse:
    try:
        request = SendContentPreviewRequest.model_validate(payload)
        return _json_result(PreviewSendContentPackageQuery()(request))
    except ValidationError:
        return _error("请求参数格式不正确", status_code=400)
    except ContractError as exc:
        return _error(str(exc), status_code=400)
    except RepositoryProviderError as exc:
        return _json_result(send_content_production_unavailable_payload(str(exc)))
    except Exception as exc:
        return _error(f"预览内容包失败：{exc}", status_code=500)


@router.get("/api/admin/material-picker/items")
def list_material_picker_items(
    type: str,
    q: str = "",
    enabled_only: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> JSONResponse:
    try:
        request = MaterialPickerListRequest(
            type=type,
            q=q,
            enabled_only=enabled_only,
            limit=max(1, min(int(limit or 50), 100)),
            offset=max(0, int(offset or 0)),
        )
        return _json_result(ListMaterialPickerItemsQuery()(request))
    except ValidationError:
        return _error("素材类型必须是 image、miniprogram、attachment 或 group_invite", status_code=400)
    except ContractError as exc:
        return _error(str(exc), status_code=400)
    except RepositoryProviderError as exc:
        return _json_result(send_content_production_unavailable_payload(str(exc)))
    except Exception as exc:
        return _error(f"读取素材列表失败：{exc}", status_code=500)


@router.get("/api/admin/material-assets")
def list_material_assets(
    type: str = "all",
    q: str = "",
    enabled_only: bool = True,
    limit: int = 50,
    offset: int = 0,
    cursor: str = "",
) -> JSONResponse:
    try:
        return _json_result(
            ListMaterialAssetsQuery()(
                asset_type=type,
                q=q,
                enabled_only=enabled_only,
                limit=max(1, min(int(limit or 50), 100)),
                offset=max(0, int(offset or 0)),
                cursor=cursor,
            )
        )
    except ContractError as exc:
        return _error(str(exc), status_code=400)
    except RepositoryProviderError as exc:
        return _json_result(send_content_production_unavailable_payload(str(exc)))
    except Exception as exc:
        return _error(f"读取统一素材资产失败：{exc}", status_code=500)


@router.get("/api/admin/material-assets/{material_asset_id}/usage")
def get_material_asset_usage(
    material_asset_id: str,
    limit: int = 100,
    offset: int = 0,
) -> JSONResponse:
    try:
        return _json_result(
            GetMaterialAssetUsageQuery()(
                material_asset_id,
                limit=max(1, min(int(limit or 100), 100)),
                offset=max(0, int(offset or 0)),
            )
        )
    except ContractError as exc:
        return _error(str(exc), status_code=400)
    except RepositoryProviderError as exc:
        return _json_result(send_content_production_unavailable_payload(str(exc)))
    except Exception as exc:
        return _error(f"读取素材使用关系失败：{exc}", status_code=500)


@router.post("/api/admin/material-assets/validate")
def validate_material_assets(payload: dict[str, Any]) -> JSONResponse:
    try:
        request = MaterialAssetsValidateRequest.model_validate(payload)
        return _json_result(
            ValidateMaterialAssetsQuery()(
                request.content_package,
                channel=request.channel,
                text_enabled=request.text_enabled,
                require_body=request.require_body,
            )
        )
    except ValidationError:
        return _error("请求参数格式不正确", status_code=400)
    except ContractError as exc:
        return _error(str(exc), status_code=400)
    except RepositoryProviderError as exc:
        return _json_result(send_content_production_unavailable_payload(str(exc)))
    except Exception as exc:
        return _error(f"校验素材失败：{exc}", status_code=500)


def _json_result(payload: dict) -> JSONResponse:
    return JSONResponse(payload, status_code=int(payload.get("status_code") or 200))


def _error(message: str, *, status_code: int) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status_code)
