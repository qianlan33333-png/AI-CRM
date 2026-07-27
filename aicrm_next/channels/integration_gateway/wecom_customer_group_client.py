from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from aicrm_next.platform.shared.runtime_settings import managed_runtime_setting


HttpGet = Callable[..., Any]
HttpPost = Callable[..., Any]
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_WECOM_GROUP_API_BASE",
        "AICRM_WECOM_GROUP_CORP_ID",
        "AICRM_WECOM_GROUP_SECRET",
        "AICRM_WECOM_GROUP_TIMEOUT",
        "WECOM_API_BASE",
        "WECOM_ARCHIVE_TIMEOUT",
        "WECOM_CONTACT_SECRET",
        "WECOM_CORP_ID",
        "WECOM_SECRET",
    }
)


class WeComCustomerGroupClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        stage: str = "",
        payload: dict[str, Any] | None = None,
        error_code: str = "",
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.payload = payload or {}
        self.error_code = error_code


def _env_first(*names: str) -> str:
    for name in names:
        value = managed_runtime_setting(name)
        if value:
            return value
    return ""


def _env_timeout() -> int:
    value = _env_first("AICRM_WECOM_GROUP_TIMEOUT", "WECOM_ARCHIVE_TIMEOUT")
    try:
        return int(value or 15)
    except ValueError:
        return 15


def _default_http_get(*args: Any, **kwargs: Any) -> Any:
    import requests

    return requests.get(*args, **kwargs)


def _default_http_post(*args: Any, **kwargs: Any) -> Any:
    import requests

    return requests.post(*args, **kwargs)


def _response_json(response: Any) -> dict[str, Any]:
    if hasattr(response, "raise_for_status"):
        response.raise_for_status()
    if hasattr(response, "json"):
        payload = response.json()
    else:
        payload = response
    if not isinstance(payload, dict):
        raise WeComCustomerGroupClientError(
            "WeCom customer group response is not a JSON object",
            stage="response",
            payload={"response": payload},
            error_code="wecom_group_client_invalid_response",
        )
    return payload


@dataclass
class WeComCustomerGroupClient:
    corp_id: str | None = None
    secret: str | None = None
    api_base: str | None = None
    timeout: int | None = None
    http_get: HttpGet | None = None
    http_post: HttpPost | None = None
    _access_token: str = field(default="", init=False)
    _token_expires_at: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self.corp_id = str(self.corp_id or _env_first("AICRM_WECOM_GROUP_CORP_ID", "WECOM_CORP_ID")).strip()
        self.secret = str(
            self.secret
            or _env_first("AICRM_WECOM_GROUP_SECRET", "WECOM_SECRET", "WECOM_CONTACT_SECRET")
        ).strip()
        self.api_base = str(
            self.api_base
            or _env_first("AICRM_WECOM_GROUP_API_BASE", "WECOM_API_BASE")
            or "https://qyapi.weixin.qq.com"
        ).strip().rstrip("/")
        self.timeout = int(self.timeout if self.timeout is not None else _env_timeout())
        self.http_get = self.http_get or _default_http_get
        self.http_post = self.http_post or _default_http_post

    def get_access_token(self) -> str:
        if self._access_token and self._token_expires_at > time.time():
            return self._access_token
        if not self.corp_id or not self.secret:
            raise WeComCustomerGroupClientError(
                "WeCom customer group corp_id or secret is not configured",
                stage="token",
                payload={"corp_id_configured": bool(self.corp_id), "secret_configured": bool(self.secret)},
                error_code="wecom_group_client_missing_config",
            )
        try:
            response = self.http_get(
                f"{self.api_base}/cgi-bin/gettoken",
                params={"corpid": self.corp_id, "corpsecret": self.secret},
                timeout=self.timeout,
            )
            payload = _response_json(response)
        except WeComCustomerGroupClientError:
            raise
        except Exception as exc:
            raise WeComCustomerGroupClientError(
                f"WeCom customer group token request failed: {exc}",
                stage="token",
                payload={},
                error_code="wecom_group_client_http_error",
            ) from exc
        if int(payload.get("errcode") or 0) != 0 or not str(payload.get("access_token") or "").strip():
            raise WeComCustomerGroupClientError(
                f"WeCom customer group token request failed: {payload}",
                stage="token",
                payload=payload,
                error_code="wecom_group_client_token_error",
            )
        self._access_token = str(payload["access_token"]).strip()
        expires_in = int(payload.get("expires_in") or 7200)
        self._token_expires_at = time.time() + max(60, expires_in - 60)
        return self._access_token

    def post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        access_token = self.get_access_token()
        try:
            response = self.http_post(
                f"{self.api_base}{path}",
                params={"access_token": access_token},
                json=payload or {},
                timeout=self.timeout,
            )
            return _response_json(response)
        except WeComCustomerGroupClientError:
            raise
        except Exception as exc:
            raise WeComCustomerGroupClientError(
                f"WeCom customer group request failed: {exc}",
                stage=path,
                payload={},
                error_code="wecom_group_client_http_error",
            ) from exc

    def create_group_message_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post("/cgi-bin/externalcontact/add_msg_template", payload)

    def list_group_chats(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post("/cgi-bin/externalcontact/groupchat/list", payload)

    def get_group_chat(self, chat_id: str, need_name: int = 1) -> dict[str, Any]:
        return self.post(
            "/cgi-bin/externalcontact/groupchat/get",
            {"chat_id": str(chat_id or "").strip(), "need_name": int(need_name)},
        )
