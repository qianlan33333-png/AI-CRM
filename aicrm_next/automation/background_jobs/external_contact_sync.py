from __future__ import annotations

from typing import Any, Protocol

from aicrm_next.channels.channel_entry.wecom_adapter import get_wecom_adapter, wecom_adapter_diagnostics
from aicrm_next.platform.shared.runtime_settings import managed_runtime_setting

from .db import connect, has_database_url


class ExternalContactClient(Protocol):
    def list_follow_users(self) -> list[str]: ...
    def list_contacts(self, owner_userid: str) -> list[str]: ...
    def get_contact(self, external_userid: str) -> dict[str, Any]: ...


class ExternalContactSyncRepository(Protocol):
    def existing_external_userids(self, *, corp_id: str) -> set[str]: ...
    def upsert_contact(self, *, corp_id: str, owner_userid: str, detail: dict[str, Any], dry_run: bool) -> dict[str, Any]: ...
    def counts(self) -> dict[str, int]: ...


class PostgresExternalContactSyncRepository:
    def existing_external_userids(self, *, corp_id: str) -> set[str]:
        with connect() as conn:
            rows = conn.execute(
                "SELECT external_userid FROM wecom_external_contact_identity_map WHERE corp_id = %s",
                (corp_id,),
            ).fetchall()
            return {str(row.get("external_userid") or "").strip() for row in rows if str(row.get("external_userid") or "").strip()}

    def upsert_contact(self, *, corp_id: str, owner_userid: str, detail: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        contact = dict((detail or {}).get("external_contact") or detail or {})
        external_userid = _text(contact.get("external_userid") or contact.get("external_userid_external") or contact.get("userid"))
        if not external_userid:
            return {"ok": False, "status": "skipped", "reason": "missing_external_userid"}
        name = _text(contact.get("name") or contact.get("customer_name"))
        unionid = _text(contact.get("unionid"))
        openid = _text(contact.get("openid"))
        if dry_run:
            return {"ok": True, "status": "upsert", "external_userid": external_userid, "dry_run": True}
        from psycopg.types.json import Jsonb

        with connect() as conn:
            conn.execute(
                """
                INSERT INTO wecom_external_contact_identity_map (
                    corp_id, external_userid, unionid, openid, follow_user_userid,
                    name, status, raw_profile, first_seen_at, last_seen_at, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (corp_id, external_userid) DO UPDATE
                SET unionid = COALESCE(NULLIF(EXCLUDED.unionid, ''), wecom_external_contact_identity_map.unionid),
                    openid = COALESCE(NULLIF(EXCLUDED.openid, ''), wecom_external_contact_identity_map.openid),
                    follow_user_userid = COALESCE(NULLIF(EXCLUDED.follow_user_userid, ''), wecom_external_contact_identity_map.follow_user_userid),
                    name = COALESCE(NULLIF(EXCLUDED.name, ''), wecom_external_contact_identity_map.name),
                    raw_profile = EXCLUDED.raw_profile,
                    last_seen_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (corp_id, external_userid, unionid, openid, owner_userid, name, Jsonb(detail)),
            )
            conn.execute(
                """
                INSERT INTO wecom_external_contact_follow_users (
                    corp_id, external_userid, user_id, relation_status, is_primary, raw_follow_user,
                    first_seen_at, last_seen_at, created_at, updated_at
                )
                VALUES (%s, %s, %s, 'active', true, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (corp_id, external_userid, user_id) DO UPDATE
                SET relation_status = 'active',
                    is_primary = true,
                    raw_follow_user = EXCLUDED.raw_follow_user,
                    last_seen_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (corp_id, external_userid, owner_userid, Jsonb({"owner_userid": owner_userid, "source": "next_native_sync"})),
            )
        return {"ok": True, "status": "upsert", "external_userid": external_userid, "dry_run": False}

    def counts(self) -> dict[str, int]:
        with connect() as conn:
            identities = conn.execute("SELECT COUNT(*) AS total FROM wecom_external_contact_identity_map").fetchone()
            identity_total = int(identities["total"] if identities else 0)
            return {
                "contacts_total": identity_total,
                "identity_map_total": identity_total,
            }


def _text(value: Any) -> str:
    return str(value or "").strip()


class AdapterExternalContactClient:
    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter

    def list_follow_users(self) -> list[str]:
        return list(self._adapter.list_follow_users())

    def list_contacts(self, owner_userid: str) -> list[str]:
        return list(self._adapter.list_contacts(owner_userid))

    def get_contact(self, external_userid: str) -> dict[str, Any]:
        return dict(self._adapter.get_external_contact_detail(external_userid))


def _default_external_contact_client() -> tuple[ExternalContactClient | None, dict[str, Any] | None]:
    diagnostics = wecom_adapter_diagnostics()
    if not diagnostics.get("real_wecom_adapter_enabled"):
        return None, {
            "component": "wecom_contact_client",
            "status": "skipped",
            "reason": diagnostics.get("real_wecom_adapter_reason") or "next_native_live_client_not_configured",
            "missing_config": list(diagnostics.get("missing_config") or []),
        }
    return AdapterExternalContactClient(get_wecom_adapter()), None


def run_external_contact_sync(
    *,
    full: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
    client: ExternalContactClient | None = None,
    repo: ExternalContactSyncRepository | None = None,
    corp_id: str | None = None,
) -> dict[str, Any]:
    mode = "full" if full else "incremental"
    summary: dict[str, Any] = {
        "ok": True,
        "job": "external_contact_sync",
        "mode": mode,
        "full": bool(full),
        "dry_run": bool(dry_run),
        "limit": limit,
        "fetched_count": 0,
        "processed": 0,
        "inserted_or_updated": 0,
        "skipped": 0,
        "owners": [],
        "warnings": [],
        "errors": [],
    }
    if client is None:
        client, skipped = _default_external_contact_client()
    else:
        skipped = None
    if client is None:
        skipped = skipped or {"component": "wecom_contact_client", "status": "skipped", "reason": "next_native_live_client_not_configured"}
        payload = {**summary, "status": "skipped", "skipped": 1, "skipped_components": [skipped]}
        return payload if dry_run else {**payload, "ok": False, "errors": [{"code": "wecom_contact_client_missing", "message": "Next-native live client is not configured"}]}
    if repo is None and not has_database_url():
        skipped = {"component": "postgres_repository", "status": "skipped", "reason": "database_url_missing"}
        payload = {**summary, "status": "skipped", "skipped": 1, "skipped_components": [skipped]}
        return payload if dry_run else {**payload, "ok": False, "errors": [{"code": "database_url_missing", "message": "DATABASE_URL is required"}]}
    repo = repo or PostgresExternalContactSyncRepository()
    corp = _text(corp_id or managed_runtime_setting("WECOM_CORP_ID")) or "default"
    existing = repo.existing_external_userids(corp_id=corp) if not full else set()
    remaining = int(limit) if limit is not None else None
    try:
        follow_users = client.list_follow_users()
    except Exception as exc:
        return {**summary, "ok": False, "errors": [{"code": "external_contact_sync_failed", "message": str(exc)}]}
    for owner_userid in follow_users:
        if remaining is not None and remaining <= 0:
            break
        owner_result = {"owner_userid": owner_userid, "ok": True, "external_count": 0, "fetched_count": 0}
        try:
            external_userids = client.list_contacts(owner_userid)
        except Exception as exc:
            owner_result.update({"ok": False, "error": str(exc)})
            summary["warnings"].append({"owner_userid": owner_userid, "code": "owner_contact_list_failed", "message": str(exc)})
            summary["owners"].append(owner_result)
            continue
        for external_userid in external_userids:
            if remaining is not None and remaining <= 0:
                break
            owner_result["external_count"] += 1
            if not full and external_userid in existing:
                summary["skipped"] += 1
                continue
            try:
                detail = dict(client.get_contact(external_userid))
                detail.setdefault("external_userid", external_userid)
                outcome = repo.upsert_contact(corp_id=corp, owner_userid=owner_userid, detail=detail, dry_run=dry_run)
            except Exception as exc:
                summary["skipped"] += 1
                warning = {"owner_userid": owner_userid, "external_userid": external_userid, "code": "contact_sync_failed", "message": str(exc)}
                owner_result.setdefault("warnings", []).append(warning)
                summary["warnings"].append(warning)
                continue
            summary["processed"] += 1
            summary["fetched_count"] += 1
            owner_result["fetched_count"] += 1
            if outcome.get("ok"):
                summary["inserted_or_updated"] += 1
            else:
                summary["skipped"] += 1
                summary["warnings"].append({"external_userid": external_userid, "reason": outcome.get("reason")})
            existing.add(external_userid)
            if remaining is not None:
                remaining -= 1
        summary["owners"].append(owner_result)
    summary.update(repo.counts())
    return summary
