from __future__ import annotations

from typing import Any, Protocol


Json = dict[str, Any]


class OAuthIdentityAdapterContract(Protocol):
    def build_oauth_authorize_url_contract(self, *, slug: str, state: str, redirect_uri: str, scope: str = "snsapi_base") -> Json:
        ...

    def parse_oauth_callback_contract(self, *, code: str, state: str, openid: str = "", unionid: str = "") -> Json:
        ...

    def normalize_oauth_identity_event(self, event: Json) -> Json:
        ...

    def dry_run_record_oauth_identity(self, *, event: Json, operator: str, idempotency_key: str) -> Json:
        ...

    def dry_run_session_identity_evidence(self, *, event: Json, operator: str, idempotency_key: str) -> Json:
        ...
