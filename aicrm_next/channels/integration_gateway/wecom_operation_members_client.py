from __future__ import annotations

import time
from typing import Any, Callable

from aicrm_next.platform.shared.runtime_settings import managed_runtime_setting


JsonDict = dict[str, Any]
HttpGet = Callable[..., Any]
RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_WECOM_OPERATION_MEMBERS_API_BASE",
        "AICRM_WECOM_OPERATION_MEMBERS_CORP_ID",
        "AICRM_WECOM_OPERATION_MEMBERS_SECRET",
        "AICRM_WECOM_OPERATION_MEMBERS_TIMEOUT",
        "WECOM_API_BASE",
        "WECOM_CONTACT_SECRET",
        "WECOM_CORP_ID",
        "WECOM_SECRET",
    }
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_env(*names: str) -> str:
    for name in names:
        value = _text(managed_runtime_setting(name))
        if value:
            return value
    return ""


class WeComOperationMembersClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        stage: str,
        error_code: str,
        payload: JsonDict | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.error_code = error_code
        self.payload = payload or {}


class WeComOperationMembersClient:
    """Read customer-contact capable staff from WeCom.

    The member picker assigns staff userids to channel/group operation surfaces.
    The authoritative WeCom source for that set is the external-contact
    follow-user list, with user/get used only to enrich display data.
    """

    def __init__(
        self,
        *,
        corp_id: str | None = None,
        secret: str | None = None,
        api_base: str | None = None,
        timeout: int | None = None,
        http_get: HttpGet | None = None,
    ) -> None:
        self.corp_id = _text(corp_id) or _first_env("AICRM_WECOM_OPERATION_MEMBERS_CORP_ID", "WECOM_CORP_ID")
        self.secret = _text(secret) or _first_env(
            "AICRM_WECOM_OPERATION_MEMBERS_SECRET",
            "WECOM_CONTACT_SECRET",
            "WECOM_SECRET",
        )
        self.api_base = (
            _text(api_base)
            or _first_env("AICRM_WECOM_OPERATION_MEMBERS_API_BASE", "WECOM_API_BASE")
            or "https://qyapi.weixin.qq.com"
        ).rstrip("/")
        self.timeout = int(
            timeout
            or _text(managed_runtime_setting("AICRM_WECOM_OPERATION_MEMBERS_TIMEOUT"))
            or 15
        )
        self.http_get = http_get or self._requests_get
        self._access_token = ""
        self._token_expires_at = 0.0

    @staticmethod
    def _requests_get(*args: Any, **kwargs: Any) -> Any:
        import requests

        return requests.get(*args, **kwargs)

    def _require_config(self) -> None:
        missing = []
        if not self.corp_id:
            missing.append("corp_id")
        if not self.secret:
            missing.append("secret")
        if missing:
            raise WeComOperationMembersClientError(
                f"wecom operation members config missing: {','.join(missing)}",
                stage="config",
                error_code="wecom_operation_members_config_missing",
                payload={"missing": missing},
            )

    def _json_payload(self, response: Any, *, stage: str) -> JsonDict:
        if isinstance(response, dict):
            return dict(response)
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        if not hasattr(response, "json"):
            raise WeComOperationMembersClientError(
                "wecom operation members response invalid",
                stage=stage,
                error_code="wecom_operation_members_response_invalid",
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise WeComOperationMembersClientError(
                "wecom operation members response invalid",
                stage=stage,
                error_code="wecom_operation_members_response_invalid",
            )
        return dict(payload)

    def _get(self, path: str, *, stage: str, params: JsonDict | None = None, allow_nonzero: bool = False) -> JsonDict:
        try:
            response = self.http_get(
                f"{self.api_base}{path}",
                params=params or {},
                timeout=self.timeout,
            )
            payload = self._json_payload(response, stage=stage)
        except WeComOperationMembersClientError:
            raise
        except Exception as exc:
            raise WeComOperationMembersClientError(
                f"wecom operation members {stage} failed: {exc}",
                stage=stage,
                error_code="wecom_operation_members_http_error",
            ) from exc
        errcode = int(payload.get("errcode") or 0)
        if errcode != 0 and not allow_nonzero:
            raise WeComOperationMembersClientError(
                f"wecom operation members {stage} failed",
                stage=stage,
                error_code="wecom_operation_members_api_error",
                payload=payload,
            )
        return payload

    def get_access_token(self) -> str:
        self._require_config()
        if self._access_token and self._token_expires_at > time.time():
            return self._access_token
        payload = self._get(
            "/cgi-bin/gettoken",
            stage="gettoken",
            params={"corpid": self.corp_id, "corpsecret": self.secret},
        )
        token = _text(payload.get("access_token"))
        if not token:
            raise WeComOperationMembersClientError(
                "wecom operation members gettoken missing access_token",
                stage="gettoken",
                error_code="wecom_operation_members_gettoken_failed",
                payload=payload,
            )
        expires_in = int(payload.get("expires_in") or 7200)
        self._access_token = token
        self._token_expires_at = time.time() + max(60, expires_in - 60)
        return token

    def _request_with_token(self, path: str, *, stage: str, params: JsonDict | None = None, allow_nonzero: bool = False) -> JsonDict:
        request_params = {"access_token": self.get_access_token()}
        request_params.update(params or {})
        return self._get(path, stage=stage, params=request_params, allow_nonzero=allow_nonzero)

    def list_follow_userids(self) -> list[str]:
        payload = self._request_with_token("/cgi-bin/externalcontact/get_follow_user_list", stage="follow_user_list")
        return [_text(userid) for userid in list(payload.get("follow_user") or []) if _text(userid)]

    def get_user_profile(self, userid: str) -> JsonDict:
        return self._request_with_token(
            "/cgi-bin/user/get",
            stage="user_get",
            params={"userid": _text(userid)},
            allow_nonzero=True,
        )

    def list_operation_members(self) -> list[JsonDict]:
        members: list[JsonDict] = []
        for userid in self.list_follow_userids():
            profile = self.get_user_profile(userid)
            if int(profile.get("errcode") or 0) != 0:
                profile = {"userid": userid, "name": userid, "status": 1, "profile_error": profile}
            profile.setdefault("userid", userid)
            members.append(_member_from_profile(profile))
        return members


def _member_from_profile(profile: JsonDict) -> JsonDict:
    userid = _text(profile.get("userid") or profile.get("user_id"))
    departments = profile.get("department") if isinstance(profile.get("department"), list) else []
    status = _text(profile.get("status") or 1)
    return {
        "wecom_userid": userid,
        "display_name": _text(profile.get("name")) or userid,
        "department_ids": [int(item) for item in departments if str(item).strip().isdigit()],
        "department_name": _text(profile.get("main_department") or profile.get("department_name")),
        "position": _text(profile.get("position")),
        "mobile": _text(profile.get("mobile")),
        "avatar_url": _text(profile.get("avatar") or profile.get("thumb_avatar")),
        "wecom_status": status,
        "is_active": status in {"", "1"},
        "raw_payload": dict(profile),
    }


def build_wecom_operation_members_client() -> WeComOperationMembersClient:
    return WeComOperationMembersClient()
