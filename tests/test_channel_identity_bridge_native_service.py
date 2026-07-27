from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from aicrm_next.channels.channel_entry.identity_bridge_service import IdentityBridgeService


class _DetailAdapter:
    def __init__(self, detail: dict[str, Any] | None = None) -> None:
        self.profile_updates: list[dict[str, Any]] = []
        self.detail = detail or {
            "errcode": 0,
            "external_contact": {
                "external_userid": "wm_native_001",
                "unionid": "union_native_001",
                "openid": "openid_native_001",
                "name": "native customer",
            },
            "follow_user": [{"userid": "owner_native"}],
        }

    def get_external_contact_detail(self, external_userid: str) -> dict[str, Any]:
        return self.detail

    def update_external_contact_remark(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.profile_updates.append(dict(payload))
        return {"errcode": 0, "errmsg": "ok"}


class _RemarkFailingDetailAdapter(_DetailAdapter):
    def update_external_contact_remark(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.profile_updates.append(dict(payload))
        return {"errcode": 40003, "errmsg": "invalid userid"}


class _AdapterWithoutDetail:
    pass


class _FakeRepo:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {"exists": False, "reason": "identity_missing"}
        self.binding_status: dict[str, Any] = {"is_bound": False, "mobile": ""}
        self.candidate: dict[str, Any] | None = None
        self.backfilled: dict[str, Any] | None = None
        self.upserted_record: dict[str, Any] | None = None
        self.submissions = [{"external_userid": "", "follow_user_userid": "", "matched_by": ""}]

    def identity_bridge_state(self, external_userid: str) -> dict[str, Any]:
        return dict(self.state)

    def normalize_external_contact_identity(
        self,
        corp_id: str,
        detail: dict[str, Any],
        follow_user_userid: str,
        status: str = "active",
    ) -> dict[str, Any]:
        contact = dict((detail or {}).get("external_contact") or {})
        return {
            "corp_id": corp_id,
            "external_userid": contact.get("external_userid", ""),
            "unionid": contact.get("unionid", ""),
            "openid": contact.get("openid", ""),
            "follow_user_userid": follow_user_userid,
            "name": contact.get("name", ""),
            "status": status,
            "raw_profile": "{}",
        }

    def upsert_external_contact_identity(self, record: dict[str, Any]) -> int:
        self.upserted_record = dict(record)
        return 123

    def replace_external_contact_follow_users(self, *args: Any, **kwargs: Any) -> None:
        return None

    def refresh_external_contact_identity_owner(self, *args: Any, **kwargs: Any) -> None:
        return None

    def get_contact_binding_status(self, external_userid: str, owner_userid: str = "") -> dict[str, Any]:
        return dict(self.binding_status)

    def get_unique_mobile_candidate_from_identity_sources(self, external_userid: str) -> dict[str, Any] | None:
        return dict(self.candidate) if self.candidate else None

    def get_or_create_person_for_mobile(self, mobile: str) -> tuple[int, str]:
        return 7, str(mobile)

    def upsert_external_contact_binding_record(self, **kwargs: Any) -> dict[str, Any]:
        self.binding_status = {
            "is_bound": True,
            "external_userid": kwargs["external_userid"],
            "person_id": kwargs["person_id"],
            "mobile": self.candidate["mobile"] if self.candidate else "",
            "first_owner_userid": kwargs["owner_userid"],
            "last_owner_userid": kwargs["owner_userid"],
        }
        return dict(self.binding_status)

    def resolve_binding_owner_userid(self, external_userid: str, owner_userid: str = "") -> str:
        return owner_userid or "owner_native"

    def merge_lead_pool_after_mobile_bind(self, **kwargs: Any) -> dict[str, Any]:
        return {"status": "updated", "updated_count": 1}

    def backfill_questionnaire_submissions_for_mobile_binding(self, **kwargs: Any) -> dict[str, Any]:
        self.submissions[0].update(
            {
                "external_userid": kwargs["external_userid"],
                "follow_user_userid": kwargs.get("follow_user_userid", ""),
                "matched_by": "mobile",
            }
        )
        self.backfilled = dict(kwargs)
        return {"status": "updated", "updated_count": 1, "external_userid": kwargs["external_userid"]}


def _service(repo: _FakeRepo, adapter: Any | None = None) -> IdentityBridgeService:
    return IdentityBridgeService(repository=repo, adapter_factory=lambda: adapter or _DetailAdapter())


def test_identity_bridge_state_missing() -> None:
    repo = _FakeRepo()

    state = _service(repo).identity_bridge_state("wm_missing")

    assert state["exists"] is False
    assert state["reason"] == "identity_missing"


def test_refresh_reason_identity_fresh() -> None:
    repo = _FakeRepo()
    repo.state = {
        "exists": True,
        "unionid_present": True,
        "openid_present": True,
        "mobile_bound": True,
        "updated_at": datetime.now(timezone.utc) - timedelta(seconds=5),
    }

    result = _service(repo, adapter=_AdapterWithoutDetail()).ensure_external_contact_identity_for_sidebar(
        external_userid="wm_native_001",
        min_interval_seconds=60,
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "identity_fresh"


def test_sync_external_contact_identity_skips_unsupported_event() -> None:
    result = _service(_FakeRepo()).sync_external_contact_identity_for_event(
        {"Event": "subscribe", "ChangeType": "add_external_contact"},
        corp_id="ww-native",
    )

    assert result == {"status": "skipped", "reason": "unsupported_event"}


def test_sync_external_contact_identity_adapter_missing_detail() -> None:
    result = _service(_FakeRepo(), adapter=_AdapterWithoutDetail()).sync_external_contact_identity_for_event(
        {"Event": "change_external_contact", "ChangeType": "add_external_contact", "ExternalUserID": "wm_native_001"},
        corp_id="ww-native",
    )

    assert result == {"status": "skipped", "reason": "adapter_missing_get_external_contact_detail"}


def test_sync_external_contact_identity_wecom_api_error() -> None:
    result = _service(_FakeRepo(), adapter=_DetailAdapter({"errcode": 40001, "errmsg": "bad token"})).sync_external_contact_identity_for_event(
        {"Event": "change_external_contact", "ChangeType": "add_external_contact", "ExternalUserID": "wm_native_001"},
        corp_id="ww-native",
    )

    assert result["status"] == "failed"
    assert result["reason"] == "wecom_api_error"
    assert result["wecom_result"]["errcode"] == 40001


def test_sync_external_contact_identity_allows_missing_openid() -> None:
    repo = _FakeRepo()
    adapter = _DetailAdapter(
        {
            "errcode": 0,
            "external_contact": {
                "external_userid": "wm_native_001",
                "unionid": "union_native_001",
                "name": "native customer",
            },
            "follow_user": [{"userid": "owner_native"}],
        }
    )
    result = _service(
        repo,
        adapter=adapter,
    ).sync_external_contact_identity_for_event(
        {
            "Event": "change_external_contact",
            "ChangeType": "add_external_contact",
            "ExternalUserID": "wm_native_001",
            "UserID": "owner_native",
        },
        corp_id="ww-native",
    )

    assert result["status"] == "success"
    assert result["unionid_present"] is True
    assert result["openid_present"] is False
    assert result["profile_description"] == {
        "status": "success",
        "description_source": "external_userid",
        "description": "wm_native_001",
        "real_external_call_executed": True,
    }
    assert adapter.profile_updates == [
        {
            "userid": "owner_native",
            "external_userid": "wm_native_001",
            "description": "wm_native_001",
        }
    ]
    assert repo.upserted_record["openid"] == ""


def test_sync_external_contact_identity_without_unionid_stays_pending() -> None:
    repo = _FakeRepo()
    result = _service(
        repo,
        adapter=_DetailAdapter(
            {
                "errcode": 0,
                "external_contact": {
                    "external_userid": "wm_native_001",
                    "openid": "openid_native_001",
                    "name": "native customer",
                },
                "follow_user": [{"userid": "owner_native"}],
            }
        ),
    ).sync_external_contact_identity_for_event(
        {
            "Event": "change_external_contact",
            "ChangeType": "add_external_contact",
            "ExternalUserID": "wm_native_001",
            "UserID": "owner_native",
        },
        corp_id="ww-native",
    )

    assert result["status"] == "pending_identity"
    assert result["reason"] == "missing_unionid"
    assert result["unionid_present"] is False
    assert result["profile_description"]["status"] == "success"
    assert result["mobile_binding"] == {"status": "skipped", "reason": "identity_pending_unionid"}
    assert repo.binding_status == {"is_bound": False, "mobile": ""}


def test_sync_external_contact_identity_keeps_identity_success_when_description_update_fails() -> None:
    result = _service(_FakeRepo(), adapter=_RemarkFailingDetailAdapter()).sync_external_contact_identity_for_event(
        {
            "Event": "change_external_contact",
            "ChangeType": "add_external_contact",
            "ExternalUserID": "wm_native_001",
            "UserID": "owner_native",
        },
        corp_id="ww-native",
    )

    assert result["status"] == "success"
    assert result["profile_description"]["status"] == "failed"
    assert result["profile_description"]["reason"] == "wecom_api_error"
    assert result["profile_description"]["wecom_result"]["errcode"] == 40003


def test_bind_mobile_from_identity_sources_no_candidate() -> None:
    result = _service(_FakeRepo()).bind_mobile_from_identity_sources("wm_native_001")

    assert result == {"status": "skipped", "reason": "no_single_candidate"}


def test_bind_mobile_from_identity_sources_existing_binding() -> None:
    repo = _FakeRepo()
    repo.binding_status = {"is_bound": True, "mobile": "18565883798", "external_userid": "wm_native_001"}

    result = _service(repo).bind_mobile_from_identity_sources("wm_native_001")

    assert result["status"] == "already_bound"
    assert result["mobile"] == "18565883798"


def test_bind_mobile_from_identity_sources_conflict() -> None:
    repo = _FakeRepo()
    repo.binding_status = {"is_bound": True, "mobile": "18565883798", "external_userid": "wm_native_001"}
    repo.candidate = {"mobile": "18565883799", "matched_count": 1}

    result = _service(repo).bind_mobile_from_identity_sources("wm_native_001")

    assert result["status"] == "conflict"
    assert result["reason"] == "external_userid already bound to another mobile"


def test_questionnaire_backfill_updates_unbound_submission() -> None:
    repo = _FakeRepo()
    repo.candidate = {"mobile": "18565883798", "matched_count": 1, "sources": ["wechat_pay_orders"]}

    result = _service(repo).sync_external_contact_identity_for_event(
        {
            "Event": "change_external_contact",
            "ChangeType": "add_external_contact",
            "ExternalUserID": "wm_native_001",
            "UserID": "owner_native",
        },
        corp_id="ww-native",
    )

    assert result["status"] == "success"
    assert result["mobile_binding"]["status"] == "bound"
    assert result["questionnaire_backfill"]["updated_count"] == 1
    assert repo.submissions[0] == {
        "external_userid": "wm_native_001",
        "follow_user_userid": "owner_native",
        "matched_by": "mobile",
    }
def test_identity_sync_rejects_request_corp_override_before_db_or_external_side_effect(monkeypatch) -> None:
    from aicrm_next.channels.channel_entry.identity_bridge_service import IdentityBridgeService

    calls: list[str] = []

    class Repository:
        def identity_bridge_state(self, external_userid):
            calls.append("db")
            raise AssertionError("database must not be touched for corp mismatch")

    def adapter_factory():
        calls.append("external")
        raise AssertionError("external adapter must not be touched for corp mismatch")

    monkeypatch.setenv("WECOM_CORP_ID", "corp-configured")
    service = IdentityBridgeService(repository=Repository(), adapter_factory=adapter_factory)

    result = service.sync_external_contact_identity_for_event(
        {
            "Event": "change_external_contact",
            "ChangeType": "edit_external_contact",
            "ExternalUserID": "external-r03-corp",
            "UserID": "owner-r03-corp",
        },
        corp_id="corp-request-override",
    )
    sidebar_result = service.ensure_external_contact_identity_for_sidebar(
        external_userid="external-r03-corp",
        corp_id="corp-request-override",
    )

    assert result == {
        "status": "failed",
        "reason": "corp_id_mismatch",
        "real_external_call_executed": False,
    }
    assert sidebar_result == result
    assert calls == []
