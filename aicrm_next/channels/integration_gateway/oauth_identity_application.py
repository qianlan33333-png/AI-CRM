from __future__ import annotations

from typing import Any

from .oauth_identity_adapter import build_fake_stub_oauth_identity_adapter
from .oauth_identity_contract import OAuthIdentityAdapterContract
from .oauth_identity_live_adapter import LiveOAuthIdentityAdapter, build_live_oauth_identity_adapter


Json = dict[str, Any]
_DEFAULT_ADAPTER = build_fake_stub_oauth_identity_adapter()


def reset_oauth_identity_fake_stub_state() -> None:
    _DEFAULT_ADAPTER.reset_state()


class OAuthIdentityApplicationService:
    def __init__(self, adapter: OAuthIdentityAdapterContract | None = None) -> None:
        self._adapter = adapter or _DEFAULT_ADAPTER

    def build_oauth_authorize_url_contract(self, *, slug: str, state: str, redirect_uri: str, scope: str = "snsapi_base") -> Json:
        return self._adapter.build_oauth_authorize_url_contract(slug=slug, state=state, redirect_uri=redirect_uri, scope=scope)

    def parse_oauth_callback_contract(self, *, code: str, state: str, openid: str = "", unionid: str = "") -> Json:
        return self._adapter.parse_oauth_callback_contract(code=code, state=state, openid=openid, unionid=unionid)

    def normalize_oauth_identity_event(self, event: Json) -> Json:
        return self._adapter.normalize_oauth_identity_event(event)

    def dry_run_record_oauth_identity(self, *, event: Json, operator: str, idempotency_key: str) -> Json:
        return self._adapter.dry_run_record_oauth_identity(event=event, operator=operator, idempotency_key=idempotency_key)

    def dry_run_session_identity_evidence(self, *, event: Json, operator: str, idempotency_key: str) -> Json:
        return self._adapter.dry_run_session_identity_evidence(event=event, operator=operator, idempotency_key=idempotency_key)

    def live_oauth_callback_attempt(self) -> Json:
        if hasattr(self._adapter, "live_oauth_callback_attempt"):
            return self._adapter.live_oauth_callback_attempt()  # type: ignore[attr-defined]
        return {"ok": False, "error_code": "live_oauth_callback_not_enabled", "live_oauth_call_executed": False}


def build_oauth_identity_application_service() -> OAuthIdentityApplicationService:
    return OAuthIdentityApplicationService()


def build_live_oauth_identity_application_service(*, confirm_live_oauth_callback: bool = False) -> LiveOAuthIdentityAdapter:
    return build_live_oauth_identity_adapter(confirm_live_oauth_callback=confirm_live_oauth_callback)
