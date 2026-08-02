from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import ipaddress
import re
from typing import Any, Protocol

from sqlalchemy.exc import IntegrityError

from aicrm_next.platform.platform_foundation.admin_audit import AdminAuditRecord
from aicrm_next.platform.platform_foundation.auth_platform.context import PrincipalType
from aicrm_next.platform.platform_foundation.auth_platform.credentials import verify_client_secret
from aicrm_next.platform.platform_foundation.auth_platform.models import ApiClientRecord
from aicrm_next.platform.platform_foundation.auth_platform.profiles import (
    API_CLIENT_PROFILES,
    DIRECT_EXTERNAL_API_KEY_CLIENT_ID,
)
from aicrm_next.platform.platform_foundation.auth_platform.service import ApiClientService, AuthServiceConfig


TARGET_API_CLIENT = "api_client"
EXTERNAL_AUDIENCE = "external_integration"
ALLOWED_TTL_SECONDS = frozenset({15 * 60, 30 * 60, 60 * 60})
CLIENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{2,63}$")


@dataclass(frozen=True)
class ApiClientTemplate:
    key: str
    label: str
    purpose: str
    scopes: tuple[str, ...]
    capabilities: tuple[str, ...]
    resource_path: str


API_CLIENT_TEMPLATES = {
    "external_api": ApiClientTemplate(
        key="external_api",
        label="External API",
        purpose="external_agent",
        scopes=("read", "write"),
        capabilities=("external_read", "external_write"),
        resource_path="/api/external",
    ),
    "mcp": ApiClientTemplate(
        key="mcp",
        label="MCP",
        purpose="mcp",
        scopes=("read", "write"),
        capabilities=("mcp_read", "mcp_execute"),
        resource_path="/mcp",
    ),
}

SYSTEM_MANAGED_CLIENT_IDS = frozenset(
    {
        *(profile.client_id for profile in API_CLIENT_PROFILES if profile.purpose in {"external_agent", "mcp"}),
        DIRECT_EXTERNAL_API_KEY_CLIENT_ID,
    }
)


class ApiClientMetadataRepository(Protocol):
    def api_client(self, client_id: str) -> ApiClientRecord | None: ...

    def api_client_metadata(self, client_id: str) -> dict[str, Any] | None: ...

    def list_api_client_metadata(self) -> list[dict[str, Any]]: ...


class ApiClientAdminService:
    def __init__(self, client_service: ApiClientService) -> None:
        self.client_service = client_service
        self.repository = client_service.repository

    def list_clients(self, *, base_url: str, query: str = "", status: str = "") -> dict[str, Any]:
        normalized_query = str(query or "").strip().lower()
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in {"", "enabled", "disabled"}:
            raise ValueError("invalid_status_filter")
        rows = []
        for metadata in self._metadata_rows():
            client_type = _client_type(metadata)
            if not client_type:
                continue
            item = _public_item(metadata, base_url=base_url, client_type=client_type)
            if normalized_status and item["status"] != normalized_status:
                continue
            if normalized_query and normalized_query not in " ".join(
                (item["client_id"], item["display_name"], item["type_label"], item["permission_label"])
            ).lower():
                continue
            rows.append(item)
        all_rows = [
            _public_item(metadata, base_url=base_url, client_type=client_type)
            for metadata in self._metadata_rows()
            if (client_type := _client_type(metadata))
        ]
        return {
            "rows": rows,
            "summary": {
                "configured_count": len(all_rows),
                "enabled_count": sum(1 for row in all_rows if row["enabled"]),
                "disabled_count": sum(1 for row in all_rows if not row["enabled"]),
                "system_managed_count": sum(1 for row in all_rows if row["system_managed"]),
                "status_label": f"已配置 {len(all_rows)} 个" if all_rows else "未配置",
            },
            "templates": [_template_item(item, base_url=base_url) for item in API_CLIENT_TEMPLATES.values()],
        }

    def get_client(self, client_id: str, *, base_url: str) -> dict[str, Any]:
        metadata = self._metadata(client_id)
        client_type = _client_type(metadata)
        if not client_type:
            raise KeyError("api_client_not_found")
        return _public_item(metadata, base_url=base_url, client_type=client_type)

    def create_client(
        self,
        *,
        display_name: Any,
        client_id: Any,
        client_type: Any,
        token_ttl_minutes: Any,
        allowed_cidrs: Any,
        corp_id: str,
        operator: str,
        base_url: str,
    ) -> dict[str, Any]:
        template = _template(client_type)
        normalized_id = _client_id(client_id)
        normalized_name = _display_name(display_name)
        ttl_seconds = _ttl_seconds(token_ttl_minutes)
        cidrs = _cidrs(allowed_cidrs)
        after_audit = {
            "client_id": normalized_id,
            "client_type": template.key,
            "permission_template": template.label,
            "scopes": list(template.scopes),
            "capabilities": list(template.capabilities),
            "enabled": False,
            "auth_version": 1,
            "token_ttl_minutes": ttl_seconds // 60,
            "allowed_cidrs": list(cidrs),
        }
        try:
            issued = self.client_service.create_client(
                client_id=normalized_id,
                principal_id=f"api_client:{normalized_id}",
                principal_type=PrincipalType.API_CLIENT,
                purpose=template.purpose,
                display_name=normalized_name,
                audiences=(EXTERNAL_AUDIENCE,),
                scopes=template.scopes,
                capabilities=template.capabilities,
                allowed_cidrs=cidrs,
                corp_id=str(corp_id or "").strip(),
                token_ttl_seconds=ttl_seconds,
                enabled=False,
                expose_credential_hint=True,
                audit=_audit_record(operator, "api_client_created", normalized_id, before={}, after=after_audit),
            )
        except (IntegrityError, ValueError) as exc:
            if str(exc) in {"client_id already exists", "duplicate client"}:
                raise ValueError("client_id_already_exists") from exc
            if isinstance(exc, IntegrityError):
                raise ValueError("client_id_already_exists") from exc
            raise
        item = self.get_client(normalized_id, base_url=base_url)
        return {"client": item, "client_secret": issued.client_secret}

    def update_client(
        self,
        client_id: str,
        *,
        display_name: Any,
        token_ttl_minutes: Any,
        allowed_cidrs: Any,
        operator: str,
        base_url: str,
    ) -> dict[str, Any]:
        current = self._record(client_id)
        before = self.get_client(client_id, base_url=base_url)
        _assert_mutable(before)
        if current.enabled:
            raise PermissionError("active_client_update_requires_disable")
        desired = replace(
            current,
            display_name=_display_name(display_name),
            token_ttl_seconds=_ttl_seconds(token_ttl_minutes),
            allowed_cidrs=_cidrs(allowed_cidrs),
        )
        after_audit = {
            **_audit_item(before),
            "token_ttl_minutes": desired.token_ttl_seconds // 60,
            "allowed_cidrs": list(desired.allowed_cidrs),
        }
        self.client_service.reconcile_client(
            desired,
            audit=_audit_record(
                operator,
                "api_client_updated",
                client_id,
                before=_audit_item(before),
                after=after_audit,
            ),
        )
        after = self.get_client(client_id, base_url=base_url)
        return after

    def rotate_secret(self, client_id: str, *, operator: str, base_url: str) -> dict[str, Any]:
        before = self.get_client(client_id, base_url=base_url)
        _assert_mutable(before)
        predicted = {**_audit_item(before), "enabled": False, "auth_version": before["auth_version"] + 1}
        issued = self.client_service.rotate_secret_and_disable(
            client_id,
            expose_credential_hint=True,
            audit=_audit_record(
                operator,
                "api_client_secret_rotated",
                client_id,
                before=_audit_item(before),
                after=predicted,
            ),
        )
        after = self.get_client(client_id, base_url=base_url)
        return {"client": after, "client_secret": issued.client_secret}

    def activate(
        self,
        client_id: str,
        *,
        client_secret: Any,
        copied_confirmed: Any,
        operator: str,
        base_url: str,
    ) -> dict[str, Any]:
        before = self.get_client(client_id, base_url=base_url)
        _assert_mutable(before)
        if copied_confirmed is not True:
            raise ValueError("secret_copy_confirmation_required")
        current = self._record(client_id)
        if current.enabled:
            raise ValueError("client_already_enabled")
        secret = str(client_secret or "")
        if not secret or not verify_client_secret(secret, current.secret_hash):
            raise ValueError("client_secret_self_check_failed")
        self._self_check(current, secret)
        predicted = {**_audit_item(before), "enabled": True, "auth_version": before["auth_version"] + 1}
        self.client_service.set_enabled(
            client_id,
            True,
            audit=_audit_record(
                operator,
                "api_client_activated",
                client_id,
                before=_audit_item(before),
                after=predicted,
            ),
        )
        after = self.get_client(client_id, base_url=base_url)
        return after

    def disable(self, client_id: str, *, operator: str, base_url: str) -> dict[str, Any]:
        before = self.get_client(client_id, base_url=base_url)
        _assert_mutable(before)
        if not before["enabled"]:
            return before
        predicted = {**_audit_item(before), "enabled": False, "auth_version": before["auth_version"] + 1}
        self.client_service.set_enabled(
            client_id,
            False,
            audit=_audit_record(
                operator,
                "api_client_disabled",
                client_id,
                before=_audit_item(before),
                after=predicted,
            ),
        )
        after = self.get_client(client_id, base_url=base_url)
        return after

    def _self_check(self, current: ApiClientRecord, secret: str) -> None:
        candidate = replace(current, enabled=True)
        repository = _SingleClientRepository(candidate)
        verifier = ApiClientService(repository, AuthServiceConfig(**vars(self.client_service.config)))
        source_ip = _representative_ip(current.allowed_cidrs)
        issued = verifier.issue_client_credentials_token(
            client_id=current.client_id,
            client_secret=secret,
            audience=EXTERNAL_AUDIENCE,
            requested_scopes=current.scopes,
            source_ip=source_ip,
        )
        verifier.verify_access_token(
            issued.access_token,
            audience=EXTERNAL_AUDIENCE,
            source_ip=source_ip,
            client_purpose=current.purpose if current.purpose == "mcp" else "",
            now=datetime.now(timezone.utc),
        )

    def _metadata_rows(self) -> list[dict[str, Any]]:
        method = getattr(self.repository, "list_api_client_metadata", None)
        if callable(method):
            return [
                dict(row)
                for row in method()
                if str(row.get("client_id") or "") != DIRECT_EXTERNAL_API_KEY_CLIENT_ID
            ]
        records = getattr(self.repository, "list_api_clients")()
        return [
            _metadata_from_record(record)
            for record in records
            if record.client_id != DIRECT_EXTERNAL_API_KEY_CLIENT_ID
        ]

    def _metadata(self, client_id: str) -> dict[str, Any]:
        normalized = str(client_id or "").strip()
        method = getattr(self.repository, "api_client_metadata", None)
        row = method(normalized) if callable(method) else None
        if row is None:
            record = self.repository.api_client(normalized)
            row = _metadata_from_record(record) if record else None
        if row is None:
            raise KeyError("api_client_not_found")
        return dict(row)

    def _record(self, client_id: str) -> ApiClientRecord:
        record = self.repository.api_client(str(client_id or "").strip())
        if record is None:
            raise KeyError("api_client_not_found")
        return record

class _SingleClientRepository:
    def __init__(self, client: ApiClientRecord) -> None:
        self.client = client

    def api_client(self, client_id: str) -> ApiClientRecord | None:
        return self.client if client_id == self.client.client_id else None


def _template(value: Any) -> ApiClientTemplate:
    key = str(value or "").strip()
    template = API_CLIENT_TEMPLATES.get(key)
    if template is None:
        raise ValueError("invalid_api_client_type")
    return template


def _client_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if not CLIENT_ID_PATTERN.fullmatch(normalized):
        raise ValueError("invalid_client_id")
    if normalized in SYSTEM_MANAGED_CLIENT_IDS:
        raise ValueError("system_managed_client_id_reserved")
    return normalized


def _display_name(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 120:
        raise ValueError("invalid_display_name")
    return normalized


def _ttl_seconds(value: Any) -> int:
    try:
        seconds = int(value) * 60
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_token_ttl") from exc
    if seconds not in ALLOWED_TTL_SECONDS:
        raise ValueError("invalid_token_ttl")
    return seconds


def _cidrs(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    values = value if isinstance(value, (list, tuple)) else str(value).replace("\n", ",").split(",")
    if len(values) > 20:
        raise ValueError("too_many_allowed_cidrs")
    normalized: list[str] = []
    for item in values:
        candidate = str(item or "").strip()
        if not candidate:
            continue
        try:
            normalized.append(str(ipaddress.ip_network(candidate, strict=False)))
        except ValueError as exc:
            raise ValueError("invalid_allowed_cidr") from exc
    return tuple(sorted(set(normalized)))


def _client_type(row: dict[str, Any]) -> str:
    audiences = set(row.get("audiences") or ())
    scopes = set(row.get("scopes") or ())
    capabilities = set(row.get("capabilities") or ())
    if EXTERNAL_AUDIENCE not in audiences or not scopes:
        return ""
    for key, template in API_CLIENT_TEMPLATES.items():
        if capabilities and capabilities.issubset(set(template.capabilities)):
            return key
    return ""


def _public_item(row: dict[str, Any], *, base_url: str, client_type: str) -> dict[str, Any]:
    template = API_CLIENT_TEMPLATES[client_type]
    scopes = sorted(str(item) for item in row.get("scopes") or ())
    capabilities = sorted(str(item) for item in row.get("capabilities") or ())
    exact_template = set(scopes) == set(template.scopes) and set(capabilities) == set(template.capabilities)
    enabled = bool(row.get("enabled"))
    system_managed = str(row.get("client_id") or "") in SYSTEM_MANAGED_CLIENT_IDS
    return {
        "client_id": str(row.get("client_id") or ""),
        "display_name": str(row.get("display_name") or ""),
        "client_type": client_type,
        "type_label": template.label,
        "purpose": str(row.get("purpose") or ""),
        "audience": EXTERNAL_AUDIENCE,
        "scopes": scopes,
        "capabilities": capabilities,
        "permission_label": template.label if exact_template else "历史权限（保持不变）",
        "allowed_cidrs": sorted(str(item) for item in row.get("allowed_cidrs") or ()),
        "token_ttl_minutes": int(row.get("token_ttl_seconds") or 1800) // 60,
        "enabled": enabled,
        "status": "enabled" if enabled else "disabled",
        "status_label": "已启用" if enabled else "已停用",
        "auth_version": int(row.get("auth_version") or 1),
        "credential_hint": str(row.get("credential_hint") or "aics_••••••••••••••••••"),
        "credential_hint_available": bool(row.get("credential_hint")),
        "last_rotated_at": _time(row.get("last_rotated_at")),
        "created_at": _time(row.get("created_at")),
        "updated_at": _time(row.get("updated_at")),
        "system_managed": system_managed,
        "mutable": not system_managed,
        "base_url": base_url,
        "token_url": f"{base_url}/oauth/token",
        "resource_url": f"{base_url}{template.resource_path}",
        "grant_type": "client_credentials",
    }


def _template_item(template: ApiClientTemplate, *, base_url: str) -> dict[str, Any]:
    return {
        "key": template.key,
        "label": template.label,
        "purpose": template.purpose,
        "audience": EXTERNAL_AUDIENCE,
        "scopes": list(template.scopes),
        "capabilities": list(template.capabilities),
        "base_url": base_url,
        "token_url": f"{base_url}/oauth/token",
        "resource_url": f"{base_url}{template.resource_path}",
        "grant_type": "client_credentials",
    }


def _metadata_from_record(record: ApiClientRecord) -> dict[str, Any]:
    return {
        "client_id": record.client_id,
        "principal_id": record.principal_id,
        "principal_type": record.principal_type.value,
        "purpose": record.purpose,
        "display_name": record.display_name,
        "audiences": list(record.audiences),
        "scopes": list(record.scopes),
        "capabilities": list(record.capabilities),
        "allowed_cidrs": list(record.allowed_cidrs),
        "corp_id": record.corp_id,
        "owner_scope": dict(record.owner_scope),
        "auth_version": record.auth_version,
        "token_ttl_seconds": record.token_ttl_seconds,
        "enabled": record.enabled,
        "credential_hint": record.credential_hint,
        "last_rotated_at": None,
        "created_at": None,
        "updated_at": None,
    }


def _audit_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "client_id": item["client_id"],
        "client_type": item["client_type"],
        "permission_template": item["permission_label"],
        "scopes": list(item["scopes"]),
        "capabilities": list(item["capabilities"]),
        "enabled": item["enabled"],
        "auth_version": item["auth_version"],
        "token_ttl_minutes": item["token_ttl_minutes"],
        "allowed_cidrs": list(item["allowed_cidrs"]),
    }


def _audit_record(
    operator: str,
    action_type: str,
    client_id: str,
    *,
    before: dict[str, Any],
    after: dict[str, Any],
) -> AdminAuditRecord:
    return AdminAuditRecord(
        operator=str(operator or "crm_console").strip() or "crm_console",
        action_type=action_type,
        target_type=TARGET_API_CLIENT,
        target_id=client_id,
        before=before,
        after=after,
    )


def _assert_mutable(item: dict[str, Any]) -> None:
    if item["system_managed"]:
        raise PermissionError("system_managed_client_readonly")


def _representative_ip(cidrs: tuple[str, ...]) -> str:
    if not cidrs:
        return ""
    return str(ipaddress.ip_network(cidrs[0], strict=False).network_address)


def _time(value: Any) -> str:
    if value is None:
        return ""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


__all__ = [
    "API_CLIENT_TEMPLATES",
    "ApiClientAdminService",
    "SYSTEM_MANAGED_CLIENT_IDS",
    "TARGET_API_CLIENT",
]
