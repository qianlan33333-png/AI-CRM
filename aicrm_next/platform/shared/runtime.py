from __future__ import annotations

import os

from aicrm_next.platform.shared.release import current_release_sha


ALLOW_MISSING_WECHAT_SHOP_CALLBACK_TOKEN_KEY = "AICRM_ALLOW_MISSING_WECHAT_SHOP_CALLBACK_TOKEN"


def _env_flag(name: str, *, default: bool = False) -> bool:
    value = str(os.getenv(name, "") or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def raw_database_url() -> str:
    return str(os.getenv("DATABASE_URL", "") or "").strip()


def runtime_setting(name: str, default: str = "") -> str:
    from aicrm_next.platform.shared.runtime_settings import runtime_setting as resolve_runtime_setting

    return resolve_runtime_setting(name, default)


def database_mode() -> str:
    url = raw_database_url()
    if url.startswith(("postgresql://", "postgres://", "postgresql+psycopg://")):
        return "postgres"
    return "fixture"


def fixture_mode() -> bool:
    return database_mode() != "postgres"


def production_environment() -> bool:
    values = {
        str(os.getenv("AICRM_NEXT_ENV", "") or "").strip().lower(),
        str(os.getenv("ENVIRONMENT", "") or "").strip().lower(),
        str(os.getenv("APP_ENV", "") or "").strip().lower(),
        str(os.getenv("FLASK_ENV", "") or "").strip().lower(),
    }
    return bool(values & {"prod", "production"})


def test_environment() -> bool:
    if pytest_environment():
        return True
    values = {
        str(os.getenv("AICRM_NEXT_ENV", "") or "").strip().lower(),
        str(os.getenv("ENVIRONMENT", "") or "").strip().lower(),
        str(os.getenv("APP_ENV", "") or "").strip().lower(),
        str(os.getenv("FLASK_ENV", "") or "").strip().lower(),
    }
    return "test" in values


def pytest_environment() -> bool:
    return bool(str(os.getenv("PYTEST_CURRENT_TEST", "") or "").strip())


def public_https_environment() -> bool:
    """Whether configured browser-facing origins are HTTPS.

    Production can sit behind an HTTP loopback proxy, so cookie and HSTS
    policy must not depend only on the ASGI request scheme or an optional env
    label.
    """

    return any(
        str(runtime_setting(name, "") or "").strip().lower().startswith("https://")
        for name in ("AICRM_PUBLIC_BASE_URL", "AICRM_AUTH_ISSUER")
    )


def secure_cookie_environment() -> bool:
    return production_environment() or public_https_environment()


def require_signing_secret(
    env_key: str = "SECRET_KEY",
    *,
    local_fallback: str,
    fallback_env_keys: tuple[str, ...] = (),
) -> bytes:
    for key in (env_key, *fallback_env_keys):
        value = runtime_setting(key)
        if value:
            return value.encode("utf-8")
    if production_environment():
        keys = ", ".join((env_key, *fallback_env_keys))
        raise RuntimeError(f"{keys} must be configured in production")
    return local_fallback.encode("utf-8")


def wechat_shop_callback_token_required() -> bool:
    return production_environment() and not _env_flag(ALLOW_MISSING_WECHAT_SHOP_CALLBACK_TOKEN_KEY)


def assert_required_runtime_secrets() -> None:
    require_signing_secret("SECRET_KEY", local_fallback="aicrm-next-local-secret")
    if wechat_shop_callback_token_required():
        require_signing_secret("WECHAT_SHOP_CALLBACK_TOKEN", local_fallback="")


def production_repository_required() -> bool:
    return database_mode() == "postgres" or production_environment()


def production_data_mode_enabled() -> bool:
    return database_mode() == "postgres"


def production_data_ready() -> bool:
    return production_data_mode_enabled()


def runtime_health_state() -> dict:
    mode = database_mode()
    fixture = fixture_mode()
    data_ready = production_data_ready()
    degraded = fixture and production_environment()
    warning = ""
    if degraded:
        warning = "production runtime is using fixture data; production data is not ready"
    elif fixture:
        warning = "fixture data mode"
    return {
        "ok": not degraded,
        "status": "degraded" if degraded else "ok",
        "service": "aicrm-next",
        "secret_key_present": bool(runtime_setting("SECRET_KEY")),
        "wechat_shop_callback_token_present": bool(runtime_setting("WECHAT_SHOP_CALLBACK_TOKEN")),
        "wechat_shop_callback_token_required": wechat_shop_callback_token_required(),
        "database": mode,
        "database_mode": mode,
        "fixture_mode": fixture,
        "production_data_ready": data_ready,
        "production_data_mode": production_data_mode_enabled(),
        "repository_policy": (
            "production_repositories_required"
            if production_repository_required()
            else "fixture_repositories_allowed"
        ),
        "runtime_owner": "ai_crm_next",
        "legacy_runtime_enabled": False,
        "warning": warning,
    }


def runtime_route_map_state() -> dict:
    return {
        "web_release_sha": current_release_sha(),
        "worker_release_sha": str(os.getenv("WORKER_RELEASE_SHA") or "unknown").strip() or "unknown",
        "route_owner": "ai_crm_next",
        "app_name": "aicrm-next",
        "task_queue_backend": "next_task_queue",
        "task_queue_pending": None,
        "callback_async_enabled": "next_task_queue",
        "redis_url_active": bool(str(os.getenv("REDIS_URL") or "").strip()),
        "wecom_callback_routes": {
            "/wecom/external-contact/callback": "aicrm_next.channels.channel_entry.api",
            "/api/wecom/events": "aicrm_next.channels.channel_entry.api",
            "/api/admin/channels/{channel_id}/qrcode/generate": "aicrm_next.channels.channel_entry.api",
        },
        "next_live_callback_gateway_enabled": True,
        "legacy_callback_fallback_enabled": False,
    }
