from __future__ import annotations

from typing import Any, Callable

from .wecom_runtime import (
    TOKEN_INVALID_ERRCODES,
    SingleFlightAccessTokenProvider,
    WeComProviderError,
    classify_wecom_provider_error,
    load_wecom_execution_config,
    shared_token_provider,
)


HttpRequest = Callable[..., Any]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _default_http_request(*args: Any, **kwargs: Any) -> Any:
    import requests

    return requests.request(*args, **kwargs)


class WeComAdapterBlocked(RuntimeError):
    def __init__(self, reason: str, *, missing_config: list[str] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.missing_config = list(missing_config or [])


class WeComApiError(WeComProviderError):
    def __init__(
        self,
        message: str,
        *,
        payload: dict[str, Any] | None = None,
        error_code: str = "",
        classification: str = "",
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        real_external_call_executed: bool = True,
    ) -> None:
        safe_payload = dict(payload or {})
        provider_errcode = int(safe_payload.get("errcode") or 0)
        if not error_code or not classification:
            derived_code, derived_classification = classify_wecom_provider_error(
                provider_errcode=provider_errcode,
                status_code=status_code,
            )
            error_code = error_code or derived_code
            classification = classification or derived_classification
        super().__init__(
            message,
            error_code=error_code,
            classification=classification,
            payload=safe_payload,
            provider_errcode=provider_errcode,
            status_code=status_code,
            retry_after_seconds=retry_after_seconds,
            real_external_call_executed=real_external_call_executed,
        )


class GuardedWeComAdapter:
    """Adapter boundary for WeCom side effects.

    Real calls are intentionally not enabled here. Tests and staging wiring can
    inject an object with the same methods.
    """

    def __init__(
        self,
        *,
        welcome_reason: str = "wecom_welcome_external_call_blocked",
        tag_reason: str = "wecom_tag_external_call_blocked",
        contact_way_reason: str = "wecom_contact_way_external_call_blocked",
        detail_reason: str = "wecom_contact_detail_external_call_blocked",
        transfer_reason: str = "wecom_transfer_external_call_blocked",
        missing_config: list[str] | None = None,
    ) -> None:
        self.welcome_reason = welcome_reason
        self.tag_reason = tag_reason
        self.contact_way_reason = contact_way_reason
        self.detail_reason = detail_reason
        self.transfer_reason = transfer_reason
        self.missing_config = list(missing_config or [])

    def send_welcome_msg(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise WeComAdapterBlocked(self.welcome_reason, missing_config=self.missing_config)

    def mark_external_contact_tags(
        self,
        *,
        external_userid: str,
        follow_user_userid: str,
        add_tags: list[str],
        remove_tags: list[str],
    ) -> dict[str, Any]:
        raise WeComAdapterBlocked(self.tag_reason, missing_config=self.missing_config)

    def create_contact_way(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise WeComAdapterBlocked(self.contact_way_reason, missing_config=self.missing_config)

    def create_group_join_way(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise WeComAdapterBlocked(self.contact_way_reason, missing_config=self.missing_config)

    def get_group_join_way(self, config_id: str) -> dict[str, Any]:
        raise WeComAdapterBlocked(self.contact_way_reason, missing_config=self.missing_config)

    def update_external_contact_remark(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise WeComAdapterBlocked(self.detail_reason, missing_config=self.missing_config)

    def list_follow_users(self) -> list[str]:
        raise WeComAdapterBlocked(self.detail_reason, missing_config=self.missing_config)

    def list_contacts(self, owner_userid: str) -> list[str]:
        raise WeComAdapterBlocked(self.detail_reason, missing_config=self.missing_config)

    def get_external_contact_detail(self, external_userid: str) -> dict[str, Any]:
        raise WeComAdapterBlocked(self.detail_reason, missing_config=self.missing_config)

    def transfer_customer(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise WeComAdapterBlocked(self.transfer_reason, missing_config=self.missing_config)

    def transfer_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise WeComAdapterBlocked(self.transfer_reason, missing_config=self.missing_config)


class ProductionWeComAdapter:
    def __init__(
        self,
        *,
        corp_id: str | None = None,
        secret: str | None = None,
        api_base: str | None = None,
        timeout: float | None = None,
        http_request: HttpRequest | None = None,
        token_provider: SingleFlightAccessTokenProvider | None = None,
    ) -> None:
        config = load_wecom_execution_config()
        self.corp_id = _text(corp_id or config.corp_id)
        self.secret = _text(secret or config.contact_secret)
        self.api_base = _text(api_base or config.api_base).rstrip("/")
        self.timeout = float(timeout if timeout is not None else config.timeout_seconds)
        self.http_request = http_request or _default_http_request
        self._token_provider = token_provider or (
            SingleFlightAccessTokenProvider()
            if http_request is not None
            else shared_token_provider(corp_id=self.corp_id, secret=self.secret, api_base=self.api_base)
        )

    def get_access_token(self) -> str:
        return self._token_provider.get(self._refresh_access_token)

    def _refresh_access_token(self) -> tuple[str, int]:
        if not self.corp_id or not self.secret:
            raise WeComApiError(
                "WeCom credentials are not configured",
                error_code="config_missing",
                classification="blocked",
                real_external_call_executed=False,
            )
        payload = self._request_without_token(
            "GET",
            "/cgi-bin/gettoken",
            params={"corpid": self.corp_id, "corpsecret": self.secret},
        )
        if int(payload.get("errcode") or 0) != 0:
            raise WeComApiError("gettoken failed", payload=payload)
        token = _text(payload.get("access_token"))
        if not token:
            raise WeComApiError("gettoken missing access_token", payload=payload)
        expires_in = int(payload.get("expires_in") or 7200)
        return token, expires_in

    @staticmethod
    def _retry_after_seconds(response: Any) -> float | None:
        headers = getattr(response, "headers", None) or {}
        value = headers.get("Retry-After") if hasattr(headers, "get") else None
        try:
            return max(0.0, float(value)) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    def _request_without_token(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response: Any | None = None
        try:
            response = self.http_request(
                method,
                f"{self.api_base}{path}",
                params=params or {},
                json=json_payload,
                timeout=self.timeout,
            )
            payload = response.json()
        except Exception as exc:
            error_response = getattr(exc, "response", None) or response
            status_code = int(getattr(error_response, "status_code", 0) or 0) or None
            name = exc.__class__.__name__.lower()
            error_code, classification = classify_wecom_provider_error(
                status_code=status_code,
                transport_error=status_code is None,
                timeout="timeout" in name,
            )
            raise WeComApiError(
                str(exc),
                error_code=error_code,
                classification=classification,
                status_code=status_code,
                retry_after_seconds=self._retry_after_seconds(error_response),
            ) from exc
        status_code = int(getattr(response, "status_code", 200) or 200)
        if status_code >= 400:
            error_code, classification = classify_wecom_provider_error(
                provider_errcode=int((payload or {}).get("errcode") or 0) if isinstance(payload, dict) else 0,
                status_code=status_code,
            )
            raise WeComApiError(
                f"WeCom HTTP {status_code}",
                payload=dict(payload or {}) if isinstance(payload, dict) else {},
                error_code=error_code,
                classification=classification,
                status_code=status_code,
                retry_after_seconds=self._retry_after_seconds(response),
            )
        return dict(payload or {})

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for attempt in range(2):
            access_token = self.get_access_token()
            request_params = {"access_token": access_token}
            request_params.update(params or {})
            payload = self._request_without_token(method, path, params=request_params, json_payload=json_payload)
            errcode = int(payload.get("errcode") or 0)
            if not errcode:
                return payload
            if errcode in TOKEN_INVALID_ERRCODES and attempt == 0:
                self._token_provider.invalidate(access_token)
                continue
            raise WeComApiError(f"WeCom API failed for {path}", payload=payload)
        raise WeComApiError("WeCom token refresh retry exhausted", error_code="token_refresh_exhausted", classification="terminal")

    def send_welcome_msg(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/cgi-bin/externalcontact/send_welcome_msg", json_payload=payload)

    def mark_external_contact_tags(
        self,
        *,
        external_userid: str,
        follow_user_userid: str,
        add_tags: list[str],
        remove_tags: list[str],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "userid": _text(follow_user_userid),
            "external_userid": _text(external_userid),
            "add_tag": [_text(tag) for tag in add_tags if _text(tag)],
        }
        normalized_remove_tags = [_text(tag) for tag in (remove_tags or []) if _text(tag)]
        if normalized_remove_tags:
            payload["remove_tag"] = normalized_remove_tags
        return self._request("POST", "/cgi-bin/externalcontact/mark_tag", json_payload=payload)

    def create_contact_way(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/cgi-bin/externalcontact/add_contact_way", json_payload=payload)

    def create_group_join_way(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/cgi-bin/externalcontact/groupchat/add_join_way", json_payload=payload)

    def get_group_join_way(self, config_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/cgi-bin/externalcontact/groupchat/get_join_way",
            json_payload={"config_id": _text(config_id)},
        )

    def update_external_contact_remark(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/cgi-bin/externalcontact/remark", json_payload=payload)

    def list_follow_users(self) -> list[str]:
        payload = self._request("GET", "/cgi-bin/externalcontact/get_follow_user_list")
        return [_text(userid) for userid in list(payload.get("follow_user") or []) if _text(userid)]

    def list_contacts(self, owner_userid: str) -> list[str]:
        payload = self._request("GET", "/cgi-bin/externalcontact/list", params={"userid": _text(owner_userid)})
        return [
            _text(external_userid)
            for external_userid in list(payload.get("external_userid") or [])
            if _text(external_userid)
        ]

    def get_external_contact_detail(self, external_userid: str) -> dict[str, Any]:
        return self._request("GET", "/cgi-bin/externalcontact/get", params={"external_userid": _text(external_userid)})

    def transfer_customer(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/cgi-bin/externalcontact/transfer_customer", json_payload=payload)

    def transfer_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/cgi-bin/externalcontact/transfer_result", json_payload=payload)


def real_wecom_calls_enabled() -> bool:
    return load_wecom_execution_config().real_calls_enabled


def missing_wecom_config() -> list[str]:
    config = load_wecom_execution_config()
    missing: list[str] = []
    if not config.corp_id:
        missing.append("WECOM_CORP_ID")
    if not config.contact_secret:
        missing.append("WECOM_CONTACT_SECRET")
    return missing


def wecom_adapter_diagnostics() -> dict[str, Any]:
    config = load_wecom_execution_config()
    missing = missing_wecom_config()
    enabled = config.real_calls_enabled and not missing
    if enabled:
        reason = "enabled"
    elif config.conflict:
        reason = "wecom_execution_config_conflict"
    elif missing:
        reason = "missing_wecom_config"
    else:
        reason = "wecom_real_calls_disabled"
    return {
        **config.diagnostics(),
        "real_wecom_adapter_enabled": enabled,
        "real_wecom_adapter_reason": reason,
        "missing_config": missing,
        "can_send_welcome": enabled,
        "can_mark_tag": enabled,
        "can_create_contact_way": enabled,
        "can_create_group_join_way": enabled,
        "can_transfer_customer": enabled,
    }


def build_default_wecom_channel_entry_adapter() -> Any:
    diagnostics = wecom_adapter_diagnostics()
    reason = _text(diagnostics["real_wecom_adapter_reason"])
    if reason == "enabled":
        return ProductionWeComAdapter()
    if reason == "missing_wecom_config":
        return GuardedWeComAdapter(
            welcome_reason="missing_wecom_config",
            tag_reason="missing_wecom_config",
            contact_way_reason="missing_wecom_config",
            detail_reason="missing_wecom_config",
            transfer_reason="missing_wecom_config",
            missing_config=list(diagnostics["missing_config"]),
        )
    return GuardedWeComAdapter(
        welcome_reason="wecom_real_calls_disabled",
        tag_reason="wecom_real_calls_disabled",
        contact_way_reason="wecom_real_calls_disabled",
        detail_reason="wecom_real_calls_disabled",
        transfer_reason="wecom_real_calls_disabled",
    )
