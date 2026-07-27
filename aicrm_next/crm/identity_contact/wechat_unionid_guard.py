from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .dto import IdentityResolveResult, ResolvePersonIdentityRequest
from .resolver import resolved_unionid


def _text(value: Any) -> str:
    return str(value or "").strip()


class _IdentityQuery(Protocol):
    def execute_result(self, query: ResolvePersonIdentityRequest) -> IdentityResolveResult: ...


@dataclass(frozen=True)
class WechatUnionIdAccessDecision:
    allowed: bool
    identity: dict[str, str]
    error: str = ""
    status_code: int = 200
    oauth_start_url: str = ""
    message: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "ok": self.allowed,
            "identity_ready": self.allowed,
            "error": self.error,
            "message": self.message,
            "oauth_start_url": self.oauth_start_url,
        }


def canonical_unionid_from_trusted_identity(identity: Mapping[str, Any] | None) -> str:
    """Return UnionID only from a server-verified identity container.

    Callers must pass a signed OAuth session or a provider callback result. Raw
    query/body identity hints are intentionally outside this contract.
    """

    return _text((identity or {}).get("unionid"))


def resolve_oauth_unionid(
    identity: Mapping[str, Any] | None,
    *,
    identity_query: _IdentityQuery | None = None,
) -> str:
    """Resolve an OAuth identity to one canonical UnionID without guessing.

    A provider-returned UnionID is already the stable identifier. If the
    provider omitted it, an existing canonical OpenID mapping may recover it;
    ambiguous, pending and missing mappings remain blocked.
    """

    trusted = dict(identity or {})
    explicit_unionid = canonical_unionid_from_trusted_identity(trusted)
    if explicit_unionid:
        return explicit_unionid
    openid = _text(trusted.get("openid"))
    if not openid or identity_query is None:
        return ""
    return resolved_unionid(identity_query.execute_result(ResolvePersonIdentityRequest(openid=openid)))


def evaluate_wechat_unionid_access(
    identity: Mapping[str, Any] | None,
    *,
    is_wechat_browser: bool,
    oauth_start_url: str,
) -> WechatUnionIdAccessDecision:
    """Make the shared access decision for UnionID-protected H5 features."""

    trusted = {
        key: _text((identity or {}).get(key))
        for key in ("openid", "unionid", "respondent_key", "external_userid", "payer_name")
        if _text((identity or {}).get(key))
    }
    if canonical_unionid_from_trusted_identity(trusted):
        return WechatUnionIdAccessDecision(allowed=True, identity=trusted)
    if not is_wechat_browser:
        return WechatUnionIdAccessDecision(
            allowed=False,
            identity={},
            error="wechat_browser_required",
            status_code=403,
            message="请在微信中打开后完成授权。",
        )
    return WechatUnionIdAccessDecision(
        allowed=False,
        identity={},
        error="unionid_oauth_required",
        status_code=401,
        oauth_start_url=_text(oauth_start_url),
        message="请先完成微信授权，获取稳定身份后继续。",
    )


__all__ = [
    "WechatUnionIdAccessDecision",
    "canonical_unionid_from_trusted_identity",
    "evaluate_wechat_unionid_access",
    "resolve_oauth_unionid",
]
