from __future__ import annotations

from typing import Any, Protocol


Json = dict[str, Any]


class OAuthIdentityLiveGateway(Protocol):
    def build_authorize_url_live(self, *, slug: str, state: str, redirect_uri: str, scope: str) -> Json:
        ...

    def exchange_code_live(self, *, code: str, state: str) -> Json:
        ...


class DisabledOAuthIdentityLiveGateway:
    def build_authorize_url_live(self, *, slug: str, state: str, redirect_uri: str, scope: str) -> Json:
        return {"ok": False, "error_code": "adapter_unavailable", "result_status": "live_gateway_not_configured"}

    def exchange_code_live(self, *, code: str, state: str) -> Json:
        return {"ok": False, "error_code": "adapter_unavailable", "result_status": "live_gateway_not_configured"}


def build_oauth_identity_live_gateway() -> OAuthIdentityLiveGateway:
    return DisabledOAuthIdentityLiveGateway()
