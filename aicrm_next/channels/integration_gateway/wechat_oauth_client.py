from __future__ import annotations

from typing import Any, Callable

import requests

from aicrm_next.platform.shared.runtime_settings import managed_runtime_setting


JsonDict = dict[str, Any]


class WeChatOAuthClientError(RuntimeError):
    def __init__(self, message: str, *, error_code: str = "wechat_oauth_error", payload: JsonDict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.payload = payload or {}


class WeChatOAuthClient:
    def __init__(
        self,
        *,
        timeout: int | float | None = None,
        http_get: Callable[..., Any] | None = None,
        oauth_base_url: str | None = None,
    ) -> None:
        self.timeout = timeout if timeout is not None else _oauth_timeout()
        self._http_get = http_get or requests.get
        self._oauth_base_url = (
            oauth_base_url
            or managed_runtime_setting("AICRM_NEXT_WECHAT_OAUTH_BASE_URL")
            or "https://api.weixin.qq.com"
        ).rstrip("/")

    def exchange_code(self, *, app_id: str, app_secret: str, code: str) -> JsonDict:
        return self._get_json(
            "/sns/oauth2/access_token",
            params={
                "appid": app_id,
                "secret": app_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
        )

    def fetch_userinfo(self, *, access_token: str, openid: str) -> JsonDict:
        return self._get_json(
            "/sns/userinfo",
            params={
                "access_token": access_token,
                "openid": openid,
                "lang": "zh_CN",
            },
        )

    def _get_json(self, path: str, *, params: JsonDict) -> JsonDict:
        url = f"{self._oauth_base_url}{path}"
        try:
            response = self._http_get(url, params=params, timeout=self.timeout)
            raise_for_status = getattr(response, "raise_for_status", None)
            if callable(raise_for_status):
                raise_for_status()
        except Exception as exc:
            raise WeChatOAuthClientError(
                "WeChat OAuth HTTP request failed",
                error_code="wechat_oauth_http_error",
                payload={"endpoint": path, "exception": exc.__class__.__name__},
            ) from exc

        try:
            payload = response.json()
        except Exception as exc:
            raise WeChatOAuthClientError(
                "WeChat OAuth response is invalid",
                error_code="wechat_oauth_response_invalid",
                payload={"endpoint": path, "exception": exc.__class__.__name__},
            ) from exc

        if not isinstance(payload, dict):
            raise WeChatOAuthClientError(
                "WeChat OAuth response is invalid",
                error_code="wechat_oauth_response_invalid",
                payload={"endpoint": path, "payload_type": type(payload).__name__},
            )
        return payload


def _oauth_timeout() -> int | float:
    raw = (
        managed_runtime_setting("AICRM_NEXT_WECHAT_OAUTH_TIMEOUT")
        or managed_runtime_setting("WECHAT_OAUTH_TIMEOUT")
        or "15"
    )
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 15
    return int(value) if value.is_integer() else value


def build_wechat_oauth_client() -> WeChatOAuthClient:
    return WeChatOAuthClient()
