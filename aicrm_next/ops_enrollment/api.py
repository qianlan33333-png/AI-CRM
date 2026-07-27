from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request

from aicrm_next.platform.shared.admin_read_fallback import admin_read_unavailable_payload
from aicrm_next.platform.shared.errors import ContractError, NotFoundError
from aicrm_next.platform.shared.pii_audit import infer_pii_result_count, set_pii_audit_result_count

from .application import (
    ExecuteUserOpsBatchSendCommand,
    GetUserOpsCardsQuery,
    GetUserOpsCustomerQuery,
    GetUserOpsCustomerTimelineQuery,
    GetUserOpsFilterOptionsQuery,
    GetUserOpsSendRecordQuery,
    GetUserOpsOverviewQuery,
    ListUserOpsCustomersQuery,
    ListLeadPoolQuery,
    ListUserOpsSendRecordsQuery,
    PreviewUserOpsBroadcastCommand,
    PreviewUserOpsExportCommand,
    PreviewUserOpsBatchSendCommand,
    RefreshUserOpsSendRecordStatusCommand,
    SetUserOpsDoNotDisturbCommand,
)
from .dto import BatchSendRequest, BroadcastPreviewRequest, DoNotDisturbRequest, ExportPreviewRequest, UserOpsFilters, UserOpsListRequest

router = APIRouter()


def _filters_from_query(
    wecom_status: str = "",
    mobile_binding_status: str = "",
    activation_bucket: str = "",
    class_term_no: str = "",
    keyword: str = "",
    mobile: str = "",
    owner_userid: str = "",
    tag: str = "",
) -> UserOpsFilters:
    return UserOpsFilters(
        wecom_status=wecom_status,
        mobile_binding_status=mobile_binding_status,
        activation_bucket=activation_bucket,
        class_term_no=class_term_no,
        keyword=keyword,
        mobile=mobile,
        owner_userid=owner_userid,
        tag=tag,
    )


@router.get("/api/admin/user-ops/overview")
def user_ops_overview(
    wecom_status: str = "",
    mobile_binding_status: str = "",
    activation_bucket: str = "",
    class_term_no: str = "",
    keyword: str = "",
    mobile: str = "",
    owner_userid: str = "",
    tag: str = "",
) -> dict:
    return GetUserOpsOverviewQuery()(
        UserOpsListRequest(
            filters=_filters_from_query(
                wecom_status,
                mobile_binding_status,
                activation_bucket,
                class_term_no,
                keyword,
                mobile,
                owner_userid,
                tag,
            )
        )
    )


@router.get("/api/admin/user-ops/cards")
def user_ops_cards(
    wecom_status: str = "",
    mobile_binding_status: str = "",
    activation_bucket: str = "",
    class_term_no: str = "",
    keyword: str = "",
    mobile: str = "",
    owner_userid: str = "",
    tag: str = "",
) -> dict:
    return GetUserOpsCardsQuery()(
        UserOpsListRequest(
            filters=_filters_from_query(
                wecom_status,
                mobile_binding_status,
                activation_bucket,
                class_term_no,
                keyword,
                mobile,
                owner_userid,
                tag,
            )
        )
    )


@router.get("/api/admin/user-ops/filters")
def user_ops_filters() -> dict:
    return GetUserOpsFilterOptionsQuery()()


@router.get("/api/admin/user-ops/list")
def user_ops_list(
    wecom_status: str = "",
    mobile_binding_status: str = "",
    activation_bucket: str = "",
    class_term_no: str = "",
    keyword: str = "",
    mobile: str = "",
    owner_userid: str = "",
    tag: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    return ListLeadPoolQuery()(
        UserOpsListRequest(
            filters=_filters_from_query(
                wecom_status,
                mobile_binding_status,
                activation_bucket,
                class_term_no,
                keyword,
                mobile,
                owner_userid,
                tag,
            ),
            limit=limit,
            offset=offset,
        )
    )


@router.get("/api/admin/user-ops/customers")
def user_ops_customers(
    wecom_status: str = "",
    mobile_binding_status: str = "",
    activation_bucket: str = "",
    class_term_no: str = "",
    keyword: str = "",
    mobile: str = "",
    owner_userid: str = "",
    tag: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    return ListUserOpsCustomersQuery()(
        UserOpsListRequest(
            filters=_filters_from_query(
                wecom_status,
                mobile_binding_status,
                activation_bucket,
                class_term_no,
                keyword,
                mobile,
                owner_userid,
                tag,
            ),
            limit=limit,
            offset=offset,
        )
    )


@router.get("/api/admin/user-ops/customers/{unionid}")
def user_ops_customer_detail(unionid: str) -> dict:
    try:
        return GetUserOpsCustomerQuery()(unionid=unionid)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/admin/user-ops/customers/{unionid}/timeline")
def user_ops_customer_timeline(unionid: str, limit: int = 20, offset: int = 0) -> dict:
    try:
        return GetUserOpsCustomerTimelineQuery()(unionid=unionid, limit=limit, offset=offset)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/admin/user-ops/batch-send/preview")
def user_ops_batch_send_preview(request: BatchSendRequest) -> dict:
    try:
        return PreviewUserOpsBatchSendCommand()(request)
    except ContractError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/admin/user-ops/broadcast/preview")
def user_ops_broadcast_preview(request: BroadcastPreviewRequest, idempotency_key: str = Header(default="", alias="Idempotency-Key")) -> dict:
    try:
        return PreviewUserOpsBroadcastCommand()(request, idempotency_key=idempotency_key)
    except ContractError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/admin/user-ops/export/preview")
def user_ops_export_preview(
    web_request: Request,
    request: ExportPreviewRequest,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> dict:
    try:
        result = PreviewUserOpsExportCommand()(request, idempotency_key=idempotency_key)
        set_pii_audit_result_count(web_request, infer_pii_result_count(result))
        return result
    except ContractError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/admin/user-ops/batch-send/execute")
def user_ops_batch_send_execute(request: BatchSendRequest, idempotency_key: str = Header(default="", alias="Idempotency-Key")) -> dict:
    try:
        return ExecuteUserOpsBatchSendCommand()(request, idempotency_key=idempotency_key)
    except ContractError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/admin/user-ops/do-not-disturb")
def user_ops_do_not_disturb(request: DoNotDisturbRequest) -> dict:
    try:
        return SetUserOpsDoNotDisturbCommand()(request)
    except ContractError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/admin/user-ops/send-records")
def user_ops_send_records(limit: int = 20, offset: int = 0) -> dict:
    try:
        return ListUserOpsSendRecordsQuery()(limit=limit, offset=offset)
    except Exception as exc:
        return admin_read_unavailable_payload(
            capability_owner="aicrm_next/ops_enrollment",
            page_error="发送记录读模型暂不可用，请稍后重试。",
            exc=exc,
            items_keys=("items", "records"),
            count_keys=("count", "total"),
            extra={"limit": limit, "offset": offset},
        )


@router.get("/api/admin/user-ops/send-records/{record_id}")
def user_ops_send_record_detail(record_id: str) -> dict:
    try:
        return GetUserOpsSendRecordQuery()(record_id=record_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/admin/user-ops/send-records/{record_id}/refresh")
def user_ops_send_record_refresh(record_id: str) -> dict:
    try:
        return RefreshUserOpsSendRecordStatusCommand()(record_id=record_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/admin/user-ops/export")
def user_ops_export_stub(request: Request) -> dict:
    set_pii_audit_result_count(request, 0)
    return {"ok": True, "status": "stubbed", "items": [], "filename": "user_ops_export_stub.csv"}
