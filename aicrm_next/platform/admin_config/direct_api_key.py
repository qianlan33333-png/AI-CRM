from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError

from aicrm_next.platform.platform_foundation.admin_audit import AdminAuditRecord
from aicrm_next.platform.platform_foundation.auth_platform.context import PrincipalType
from aicrm_next.platform.platform_foundation.auth_platform.models import ApiClientRecord
from aicrm_next.platform.platform_foundation.auth_platform.profiles import (
    DIRECT_EXTERNAL_API_KEY_CLIENT_ID,
    DIRECT_EXTERNAL_API_KEY_PURPOSE,
)
from aicrm_next.platform.platform_foundation.auth_platform.service import ApiClientService


DIRECT_EXTERNAL_API_KEY_DISPLAY_NAME = "CRM 开放 API Key"
DIRECT_EXTERNAL_API_KEY_AUDIENCE = "external_integration"
DIRECT_EXTERNAL_API_KEY_SCOPES = ("read",)
DIRECT_EXTERNAL_API_KEY_CAPABILITIES = ("external_read",)
TARGET_API_CLIENT = "api_client"


class DirectExternalApiKeyService:
    def __init__(self, client_service: ApiClientService) -> None:
        self.client_service = client_service
        self.repository = client_service.repository

    def status(self, *, base_url: str) -> dict[str, Any]:
        metadata = self._metadata()
        configured = metadata is not None
        enabled = bool(metadata and metadata.get("enabled"))
        return {
            "configured": configured,
            "enabled": enabled,
            "status": "enabled" if enabled else ("disabled" if configured else "unconfigured"),
            "status_label": "已启用" if enabled else ("已停用" if configured else "未配置"),
            "auth_version": int((metadata or {}).get("auth_version") or 0),
            "last_rotated_at": _time((metadata or {}).get("last_rotated_at")),
            "created_at": _time((metadata or {}).get("created_at")),
            "base_url": base_url,
            "resource_url": f"{base_url}/api/external",
            "authorization_header": "Authorization: Bearer <CRM_API_KEY>",
            "permission_label": "CRM 开放 API 只读",
        }

    def category(self, *, base_url: str) -> dict[str, Any]:
        status = self.status(base_url=base_url)
        return {
            "key": "direct_api_key",
            "label": "CRM 开放 API Key",
            "group_label": "外部集成",
            "enabled": status["enabled"],
            "status_label": status["status_label"],
            "detail_href": "/admin/config/api-key",
            "check_supported": False,
            "sort_order": 44,
            "toggleable": False,
        }

    def generate(self, *, corp_id: str, operator: str, base_url: str) -> dict[str, Any]:
        if self.repository.api_client(DIRECT_EXTERNAL_API_KEY_CLIENT_ID) is not None:
            raise ValueError("direct_api_key_already_configured")
        try:
            issued = self.client_service.create_client(
                client_id=DIRECT_EXTERNAL_API_KEY_CLIENT_ID,
                principal_id=f"api_client:{DIRECT_EXTERNAL_API_KEY_CLIENT_ID}",
                principal_type=PrincipalType.API_CLIENT,
                purpose=DIRECT_EXTERNAL_API_KEY_PURPOSE,
                display_name=DIRECT_EXTERNAL_API_KEY_DISPLAY_NAME,
                audiences=(DIRECT_EXTERNAL_API_KEY_AUDIENCE,),
                scopes=DIRECT_EXTERNAL_API_KEY_SCOPES,
                capabilities=DIRECT_EXTERNAL_API_KEY_CAPABILITIES,
                corp_id=str(corp_id or "").strip(),
                token_ttl_seconds=1800,
                enabled=True,
                audit=_audit(
                    operator,
                    "direct_external_api_key_generated",
                    before={},
                    after=_audit_item(enabled=True, auth_version=1),
                ),
            )
        except IntegrityError as exc:
            raise ValueError("direct_api_key_already_configured") from exc
        return {"api_key": issued.client_secret, "api_key_status": self.status(base_url=base_url)}

    def rotate(self, *, operator: str, base_url: str) -> dict[str, Any]:
        current = self._record()
        issued = self.client_service.rotate_secret_and_enable(
            DIRECT_EXTERNAL_API_KEY_CLIENT_ID,
            audit=_audit(
                operator,
                "direct_external_api_key_rotated",
                before=_audit_item(enabled=current.enabled, auth_version=current.auth_version),
                after=_audit_item(enabled=True, auth_version=current.auth_version + 1),
            ),
        )
        return {"api_key": issued.client_secret, "api_key_status": self.status(base_url=base_url)}

    def disable(self, *, operator: str, base_url: str) -> dict[str, Any]:
        current = self._record()
        if current.enabled:
            self.client_service.set_enabled(
                DIRECT_EXTERNAL_API_KEY_CLIENT_ID,
                False,
                audit=_audit(
                    operator,
                    "direct_external_api_key_disabled",
                    before=_audit_item(enabled=True, auth_version=current.auth_version),
                    after=_audit_item(enabled=False, auth_version=current.auth_version + 1),
                ),
            )
        return self.status(base_url=base_url)

    def _record(self) -> ApiClientRecord:
        record = self.repository.api_client(DIRECT_EXTERNAL_API_KEY_CLIENT_ID)
        if record is None:
            raise KeyError("direct_api_key_not_configured")
        if (
            record.principal_type is not PrincipalType.API_CLIENT
            or record.purpose != DIRECT_EXTERNAL_API_KEY_PURPOSE
            or record.audiences != (DIRECT_EXTERNAL_API_KEY_AUDIENCE,)
            or set(record.scopes) != set(DIRECT_EXTERNAL_API_KEY_SCOPES)
            or set(record.capabilities) != set(DIRECT_EXTERNAL_API_KEY_CAPABILITIES)
        ):
            raise PermissionError("direct_api_key_definition_conflict")
        return record

    def _metadata(self) -> dict[str, Any] | None:
        record = self.repository.api_client(DIRECT_EXTERNAL_API_KEY_CLIENT_ID)
        if record is None:
            return None
        self._record()
        method = getattr(self.repository, "api_client_metadata", None)
        if callable(method):
            metadata = method(DIRECT_EXTERNAL_API_KEY_CLIENT_ID)
            if metadata is not None:
                return dict(metadata)
        return {
            "enabled": record.enabled,
            "auth_version": record.auth_version,
            "last_rotated_at": None,
            "created_at": None,
        }


def _audit_item(*, enabled: bool, auth_version: int) -> dict[str, Any]:
    return {
        "client_id": DIRECT_EXTERNAL_API_KEY_CLIENT_ID,
        "credential_type": "direct_external_api_key",
        "purpose": DIRECT_EXTERNAL_API_KEY_PURPOSE,
        "audience": DIRECT_EXTERNAL_API_KEY_AUDIENCE,
        "scopes": list(DIRECT_EXTERNAL_API_KEY_SCOPES),
        "capabilities": list(DIRECT_EXTERNAL_API_KEY_CAPABILITIES),
        "enabled": enabled,
        "auth_version": auth_version,
    }


def _audit(operator: str, action_type: str, *, before: dict[str, Any], after: dict[str, Any]) -> AdminAuditRecord:
    return AdminAuditRecord(
        operator=str(operator or "crm_console").strip() or "crm_console",
        action_type=action_type,
        target_type=TARGET_API_CLIENT,
        target_id=DIRECT_EXTERNAL_API_KEY_CLIENT_ID,
        before=before,
        after=after,
    )


def _time(value: Any) -> str:
    if value is None:
        return ""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


__all__ = ["DirectExternalApiKeyService"]
