from __future__ import annotations

from typing import Any, Callable

import requests

from aicrm_next.platform.shared.runtime_settings import managed_runtime_setting


JsonDict = dict[str, Any]


class WeComAdminAuthClientError(RuntimeError):
    def __init__(self, message: str, *, error_code: str = "wecom_admin_auth_error", payload: JsonDict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.payload = payload or {}


class WeComAdminAuthClient:
    def __init__(
        self,
        *,
        timeout: int | float | None = None,
        http_get: Callable[..., Any] | None = None,
        api_base_url: str | None = None,
    ) -> None:
        self.timeout = timeout if timeout is not None else _timeout()
        self._http_get = http_get or requests.get
        self._api_base_url = (
            api_base_url
            or managed_runtime_setting("WECOM_API_BASE")
            or "https://qyapi.weixin.qq.com"
        ).rstrip("/")

    def fetch_access_token(self, *, corp_id: str, corp_secret: str) -> JsonDict:
        return self._get_json(
            "/cgi-bin/gettoken",
            params={"corpid": corp_id, "corpsecret": corp_secret},
        )

    def fetch_user_info(self, *, access_token: str, code: str) -> JsonDict:
        return self._get_json(
            "/cgi-bin/user/getuserinfo",
            params={"access_token": access_token, "code": code},
        )

    def _get_json(self, path: str, *, params: JsonDict) -> JsonDict:
        url = f"{self._api_base_url}{path}"
        try:
            response = self._http_get(url, params=params, timeout=self.timeout)
            raise_for_status = getattr(response, "raise_for_status", None)
            if callable(raise_for_status):
                raise_for_status()
        except Exception as exc:
            raise WeComAdminAuthClientError(
                "WeCom admin auth HTTP request failed",
                error_code="wecom_admin_auth_http_error",
                payload={"endpoint": path, "exception": exc.__class__.__name__},
            ) from exc

        try:
            payload = response.json()
        except Exception as exc:
            raise WeComAdminAuthClientError(
                "WeCom admin auth response is invalid",
                error_code="wecom_admin_auth_response_invalid",
                payload={"endpoint": path, "exception": exc.__class__.__name__},
            ) from exc

        if not isinstance(payload, dict):
            raise WeComAdminAuthClientError(
                "WeCom admin auth response is invalid",
                error_code="wecom_admin_auth_response_invalid",
                payload={"endpoint": path, "payload_type": type(payload).__name__},
            )
        return payload


def _timeout() -> int | float:
    raw = (
        managed_runtime_setting("AICRM_WECOM_ADMIN_AUTH_TIMEOUT")
        or managed_runtime_setting("WECOM_AUTH_TIMEOUT")
        or "10"
    )
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 10
    return int(value) if value.is_integer() else value


def build_wecom_admin_auth_client() -> WeComAdminAuthClient:
    return WeComAdminAuthClient()
