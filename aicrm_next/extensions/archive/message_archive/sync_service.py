from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Protocol

from aicrm_next.shared.runtime_settings import runtime_setting

from .archive_sdk import (
    WeComArchiveError,
    decrypt_chat_payload,
    decrypt_chat_payloads,
    extract_text_record,
    fetch_chatdata_page,
)
from .repo import ArchiveSyncRepository, build_archive_sync_repository


DEFAULT_START_TIME = "2000-01-01 00:00:00"
DEFAULT_END_TIME = "2099-12-31 23:59:59"
DEFAULT_LIMIT = 100
DEFAULT_MAX_PAGES = 1000
MAX_LIMIT = 1000
RUNTIME_SETTING_KEYS = frozenset(
    {
        "WECOM_CORP_ID",
        "WECOM_ARCHIVE_SECRET",
        "WECOM_PRIVATE_KEY_PATH",
        "WECOM_SDK_LIB_PATH",
        "WECOM_DEFAULT_OWNER_USERID",
        "WECOM_ARCHIVE_TIMEOUT",
    }
)


class ArchiveChatDataClient(Protocol):
    def health(self) -> dict[str, Any]: ...

    def fetch_page(self, *, seq: int, limit: int) -> dict[str, Any]: ...

    def decrypt_record(self, record: dict[str, Any]) -> dict[str, Any]: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class ArchiveSyncConfig:
    corp_id: str
    archive_secret: str
    private_key_path: str
    sdk_lib_path: str
    default_owner_userid: str
    timeout: int = 60


class WeComArchiveSdkClient:
    def __init__(self, config: ArchiveSyncConfig) -> None:
        self._config = config

    def health(self) -> dict[str, Any]:
        return {
            "ok": bool(self._config.corp_id and self._config.archive_secret),
            "mode": "official-sdk",
            "sdk_lib_path": self._config.sdk_lib_path,
            "sdk_lib_exists": Path(self._config.sdk_lib_path).exists(),
            "private_key_path": self._config.private_key_path,
            "private_key_exists": Path(self._config.private_key_path).exists(),
        }

    def _validate_config(self) -> None:
        if not self._config.corp_id or not self._config.archive_secret:
            raise WeComArchiveError("WECOM_CORP_ID or WECOM_ARCHIVE_SECRET is not configured")
        if not self._config.sdk_lib_path:
            raise WeComArchiveError("WECOM_SDK_LIB_PATH is not configured")
        if not self._config.private_key_path:
            raise WeComArchiveError("WECOM_PRIVATE_KEY_PATH is not configured")

    def fetch_page(self, *, seq: int, limit: int) -> dict[str, Any]:
        self._validate_config()
        return fetch_chatdata_page(
            self._config.sdk_lib_path,
            self._config.corp_id,
            self._config.archive_secret,
            seq,
            limit,
            self._config.timeout,
        )

    def decrypt_record(self, record: dict[str, Any]) -> dict[str, Any]:
        self._validate_config()
        return decrypt_chat_payload(
            self._config.sdk_lib_path,
            self._config.private_key_path,
            record,
            timeout=self._config.timeout,
        )

    def decrypt_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self._validate_config()
        return decrypt_chat_payloads(
            self._config.sdk_lib_path,
            self._config.private_key_path,
            records,
            timeout=self._config.timeout,
        )

    def close(self) -> None:
        return None


def build_archive_sdk_client() -> WeComArchiveSdkClient:
    return WeComArchiveSdkClient(load_archive_sync_config())


def load_archive_sync_config() -> ArchiveSyncConfig:
    return ArchiveSyncConfig(
        corp_id=_setting("WECOM_CORP_ID"),
        archive_secret=_setting("WECOM_ARCHIVE_SECRET"),
        private_key_path=_setting("WECOM_PRIVATE_KEY_PATH"),
        sdk_lib_path=_setting("WECOM_SDK_LIB_PATH"),
        default_owner_userid=_setting("WECOM_DEFAULT_OWNER_USERID"),
        timeout=_int_setting("WECOM_ARCHIVE_TIMEOUT", 60),
    )


def archive_health_payload(client: ArchiveChatDataClient | None = None) -> dict[str, Any]:
    client = client or build_archive_sdk_client()
    try:
        return {"ok": True, "adapter": client.health(), "route_owner": "ai_crm_next", "source_status": "next_archive_sync"}
    finally:
        client.close()


def execute_archive_sync(
    *,
    start_time: str = DEFAULT_START_TIME,
    end_time: str = DEFAULT_END_TIME,
    owner_userid: str = "",
    cursor: str = "",
    limit: int = DEFAULT_LIMIT,
    max_pages: int = DEFAULT_MAX_PAGES,
    repo: ArchiveSyncRepository | None = None,
    client: ArchiveChatDataClient | None = None,
) -> dict[str, Any]:
    repo = repo or build_archive_sync_repository()
    client = client or build_archive_sdk_client()
    request = _normalize_request(
        start_time=start_time,
        end_time=end_time,
        owner_userid=owner_userid,
        cursor=cursor,
        limit=limit,
        max_pages=max_pages,
    )
    run_id = repo.create_sync_run(
        start_time=request["start_time"],
        end_time=request["end_time"],
        owner_userid=request["owner_userid"],
        cursor=request["cursor"],
    )
    total_fetched = 0
    total_accepted = 0
    total_inserted = 0
    max_seq = int(request["cursor"] or repo.get_archive_last_seq())
    pages: list[dict[str, Any]] = []
    external_userids: set[str] = set()
    group_chat_ids: set[str] = set()
    try:
        for _page_index in range(int(request["max_pages"])):
            payload = client.fetch_page(seq=max_seq, limit=int(request["limit"]))
            encrypted_records = list(payload.get("chatdata") or [])
            if not encrypted_records:
                break
            decrypt_many = getattr(client, "decrypt_records", None)
            if callable(decrypt_many):
                decrypted_records = list(decrypt_many(encrypted_records))
                if len(decrypted_records) != len(encrypted_records):
                    raise WeComArchiveError("archive SDK returned a mismatched decrypt batch")
            else:
                decrypted_records = [client.decrypt_record(record) for record in encrypted_records]
            total_fetched += len(encrypted_records)
            accepted_rows: list[dict[str, Any]] = []
            seq_from = int(encrypted_records[0].get("seq") or max_seq)
            seq_to = seq_from
            for record, decrypted in zip(encrypted_records, decrypted_records):
                seq = int(record.get("seq") or 0)
                seq_to = max(seq_to, seq)
                max_seq = max(max_seq, seq)
                normalized = extract_text_record(seq, record, decrypted, fallback_owner_userid=str(request["owner_userid"]))
                if not normalized:
                    continue
                if str(normalized["send_time"]) < str(request["start_time"]) or str(normalized["send_time"]) > str(request["end_time"]):
                    continue
                accepted_rows.append(normalized)
                if normalized.get("external_userid"):
                    external_userids.add(str(normalized["external_userid"]))
                roomid = str((decrypted or {}).get("roomid") or "")
                if normalized.get("chat_type") == "group" and roomid:
                    group_chat_ids.add(roomid)
            inserted = repo.insert_messages_and_advance_seq(accepted_rows, last_seq=max_seq)
            total_accepted += len(accepted_rows)
            total_inserted += inserted
            pages.append(
                {
                    "seq_from": seq_from,
                    "seq_to": seq_to,
                    "fetched": len(encrypted_records),
                    "accepted": len(accepted_rows),
                    "inserted": inserted,
                }
            )
            if len(encrypted_records) < int(request["limit"]):
                break
        result = {
            "ok": True,
            "sync_run": {
                "id": run_id,
                "status": "success",
                "fetched_count": total_fetched,
                "accepted_count": total_accepted,
                "inserted_count": total_inserted,
                "last_seq": max_seq,
                "next_cursor": str(max_seq),
                "pages": len(pages),
            },
            "fetched_count": total_fetched,
            "accepted_count": total_accepted,
            "inserted_count": total_inserted,
            "last_seq": max_seq,
            "next_cursor": str(max_seq),
            "has_more": len(pages) >= int(request["max_pages"]),
            "batches": pages,
            "external_userids": sorted(external_userids),
            "group_chat_ids": sorted(group_chat_ids),
            "contacts_sync_skipped": True,
            "reply_monitor_skipped": True,
            "route_owner": "ai_crm_next",
            "source_status": "next_archive_sync",
        }
        repo.finish_sync_run(
            run_id,
            status="success",
            fetched_count=total_fetched,
            inserted_count=total_inserted,
            raw_response=_summary_for_storage(result),
        )
        return result
    except Exception as exc:
        repo.finish_sync_run(run_id, status="failed", fetched_count=total_fetched, inserted_count=total_inserted, error_message=str(exc))
        raise
    finally:
        client.close()


def _normalize_request(
    *,
    start_time: str,
    end_time: str,
    owner_userid: str,
    cursor: str,
    limit: int,
    max_pages: int,
) -> dict[str, Any]:
    normalized_owner = str(owner_userid or _setting("WECOM_DEFAULT_OWNER_USERID") or "").strip()
    if not normalized_owner:
        raise ValueError("owner_userid is required")
    safe_limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    safe_pages = max(1, min(int(max_pages or DEFAULT_MAX_PAGES), 10000))
    cursor_text = str(cursor or "").strip()
    if cursor_text and int(cursor_text) < 0:
        raise ValueError("cursor must be a non-negative sequence")
    return {
        "start_time": str(start_time or DEFAULT_START_TIME).strip() or DEFAULT_START_TIME,
        "end_time": str(end_time or DEFAULT_END_TIME).strip() or DEFAULT_END_TIME,
        "owner_userid": normalized_owner,
        "cursor": cursor_text,
        "limit": safe_limit,
        "max_pages": safe_pages,
    }


def _summary_for_storage(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": result.get("ok"),
        "fetched_count": result.get("fetched_count"),
        "accepted_count": result.get("accepted_count"),
        "inserted_count": result.get("inserted_count"),
        "last_seq": result.get("last_seq"),
        "has_more": result.get("has_more"),
        "batches": result.get("batches"),
        "external_userid_count": len(result.get("external_userids") or []),
        "group_chat_count": len(result.get("group_chat_ids") or []),
        "contacts_sync_skipped": True,
        "reply_monitor_skipped": True,
    }


def _setting(key: str) -> str:
    return runtime_setting(key, "")


def _int_setting(key: str, default: int) -> int:
    try:
        return int(_setting(key) or default)
    except ValueError:
        return default


def dump_archive_sync_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)
