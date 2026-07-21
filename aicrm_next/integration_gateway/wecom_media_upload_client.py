from __future__ import annotations

import time
from typing import Any, Callable

from aicrm_next.shared.runtime_settings import runtime_setting

JsonDict = dict[str, Any]
HttpCallable = Callable[..., Any]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_env(*names: str) -> str:
    for name in names:
        value = _text(runtime_setting(name, ""))
        if value:
            return value
    return ""


def _timeout_value(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 15
    return parsed if parsed > 0 else 15


def _provider_errcode(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class WeComMediaUploadClientError(RuntimeError):
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
        self.provider_errcode = _provider_errcode(self.payload.get("errcode"))
        self.provider_result_received = "errcode" in self.payload or "media_id" in self.payload
        self.errmsg_present = bool(_text(self.payload.get("errmsg")))


class WeComMediaUploadClient:
    def __init__(
        self,
        *,
        corp_id: str | None = None,
        secret: str | None = None,
        api_base: str | None = None,
        timeout: int | None = None,
        http_get: HttpCallable | None = None,
        http_post: HttpCallable | None = None,
    ) -> None:
        self.corp_id = _text(corp_id) or _first_env("AICRM_WECOM_MEDIA_CORP_ID", "AICRM_WECOM_GROUP_CORP_ID", "WECOM_CORP_ID")
        self.secret = _text(secret) or _first_env("AICRM_WECOM_MEDIA_SECRET", "AICRM_WECOM_GROUP_SECRET", "WECOM_SECRET", "WECOM_CONTACT_SECRET")
        self.api_base = (
            _text(api_base)
            or _first_env("AICRM_WECOM_MEDIA_API_BASE", "AICRM_WECOM_GROUP_API_BASE", "WECOM_API_BASE")
            or "https://qyapi.weixin.qq.com"
        ).rstrip("/")
        self.timeout = int(timeout or _timeout_value(_first_env("AICRM_WECOM_MEDIA_TIMEOUT", "AICRM_WECOM_GROUP_TIMEOUT", "WECOM_ARCHIVE_TIMEOUT")))
        self.http_get = http_get or self._requests_get
        self.http_post = http_post or self._requests_post
        self._access_token = ""
        self._token_expires_at = 0.0

    @staticmethod
    def _requests_get(*args: Any, **kwargs: Any) -> Any:
        import requests

        return requests.get(*args, **kwargs)

    @staticmethod
    def _requests_post(*args: Any, **kwargs: Any) -> Any:
        import requests

        return requests.post(*args, **kwargs)

    def _require_config(self) -> None:
        if self.corp_id and self.secret:
            return
        missing = []
        if not self.corp_id:
            missing.append("corp_id")
        if not self.secret:
            missing.append("secret")
        raise WeComMediaUploadClientError(
            f"wecom media config missing: {','.join(missing)}",
            stage="config",
            error_code="wecom_media_config_missing",
            payload={"missing": missing},
        )

    def _json_payload(self, response: Any, *, stage: str) -> JsonDict:
        if isinstance(response, dict):
            return dict(response)
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        if not hasattr(response, "json"):
            raise WeComMediaUploadClientError(
                "wecom media response invalid",
                stage=stage,
                error_code="wecom_media_response_invalid",
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise WeComMediaUploadClientError(
                "wecom media response invalid",
                stage=stage,
                error_code="wecom_media_response_invalid",
            )
        return dict(payload)

    def get_access_token(self) -> str:
        self._require_config()
        if self._access_token and self._token_expires_at > time.time():
            return self._access_token
        try:
            response = self.http_get(
                f"{self.api_base}/cgi-bin/gettoken",
                params={"corpid": self.corp_id, "corpsecret": self.secret},
                timeout=self.timeout,
            )
            payload = self._json_payload(response, stage="gettoken")
        except WeComMediaUploadClientError:
            raise
        except Exception as exc:
            raise WeComMediaUploadClientError(
                f"wecom media gettoken failed: {exc}",
                stage="gettoken",
                error_code="wecom_media_http_error",
            ) from exc

        errcode = _provider_errcode(payload.get("errcode"))
        token = _text(payload.get("access_token"))
        if errcode != 0 or not token:
            raise WeComMediaUploadClientError(
                "wecom media gettoken failed",
                stage="gettoken",
                error_code="wecom_media_gettoken_failed",
                payload=payload,
            )
        expires_in = _timeout_value(payload.get("expires_in") or 7200)
        cache_seconds = max(expires_in - 60, 60)
        self._access_token = token
        self._token_expires_at = time.time() + cache_seconds
        return token

    def upload_image(self, file_name: str, file_bytes: bytes, content_type: str) -> JsonDict:
        token = self.get_access_token()
        try:
            response = self.http_post(
                f"{self.api_base}/cgi-bin/media/upload",
                params={"access_token": token, "type": "image"},
                files={"media": (_text(file_name) or "image.png", file_bytes, _text(content_type) or "image/png")},
                timeout=self.timeout,
            )
            payload = self._json_payload(response, stage="upload")
        except WeComMediaUploadClientError:
            raise
        except Exception as exc:
            raise WeComMediaUploadClientError(
                f"wecom media upload failed: {exc}",
                stage="upload",
                error_code="wecom_media_http_error",
            ) from exc

        errcode = _provider_errcode(payload.get("errcode"))
        if errcode != 0:
            raise WeComMediaUploadClientError(
                "wecom media upload failed",
                stage="upload",
                error_code="wecom_media_upload_failed",
                payload=payload,
            )
        if not _text(payload.get("media_id")):
            raise WeComMediaUploadClientError(
                "wecom media upload failed: empty media_id",
                stage="upload",
                error_code="wecom_media_upload_failed",
                payload=payload,
            )
        return payload

    def upload_attachment(self, file_name: str, file_bytes: bytes, content_type: str) -> JsonDict:
        token = self.get_access_token()
        try:
            response = self.http_post(
                f"{self.api_base}/cgi-bin/media/upload",
                params={"access_token": token, "type": "file"},
                files={"media": (_text(file_name) or "attachment.bin", file_bytes, _text(content_type) or "application/octet-stream")},
                timeout=self.timeout,
            )
            payload = self._json_payload(response, stage="upload_attachment")
        except WeComMediaUploadClientError:
            raise
        except Exception as exc:
            raise WeComMediaUploadClientError(
                f"wecom media attachment upload failed: {exc}",
                stage="upload_attachment",
                error_code="wecom_media_http_error",
            ) from exc

        errcode = _provider_errcode(payload.get("errcode"))
        if errcode != 0:
            raise WeComMediaUploadClientError(
                "wecom media attachment upload failed",
                stage="upload_attachment",
                error_code="wecom_media_upload_failed",
                payload=payload,
            )
        if not _text(payload.get("media_id")):
            raise WeComMediaUploadClientError(
                "wecom media attachment upload failed: empty media_id",
                stage="upload_attachment",
                error_code="wecom_media_upload_failed",
                payload=payload,
            )
        return payload


def build_wecom_media_upload_client() -> WeComMediaUploadClient:
    return WeComMediaUploadClient()
