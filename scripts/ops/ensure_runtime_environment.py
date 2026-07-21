from __future__ import annotations

import argparse
from pathlib import Path

from scripts.ops.migrate_app_setting_secrets import _persist_environment_values


DEPRECATED_RUNTIME_ENV_KEYS = frozenset(
    {
        "AICRM_QUESTIONNAIRE_EXTERNAL_PUSH_MODE",
        "AICRM_NEXT_WECOM_REAL_CALLS_ENABLED",
        "AICRM_EXTERNAL_EFFECT_WECOM_EXECUTE",
        "AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES",
        "AICRM_EXTERNAL_EFFECT_ALLOWED_OWNER_USERIDS",
    }
)


def runtime_environment_values(
    *,
    target_environment: str,
    public_base_url: str,
    allow_missing_wechat_shop_callback_token: bool = False,
) -> dict[str, str]:
    target = str(target_environment or "").strip().lower()
    if target not in {"production", "test"}:
        raise ValueError("target_environment must be production or test")
    public_url = str(public_base_url or "").strip().rstrip("/")
    if not public_url.lower().startswith("https://"):
        raise ValueError("public_base_url must use https")
    return {
        "AICRM_NEXT_ENV": "production" if target == "production" else "test",
        "AICRM_ADMIN_SESSION_COOKIE_SECURE": "1",
        "AICRM_PUBLIC_BASE_URL": public_url,
        "AICRM_ALLOW_MISSING_WECHAT_SHOP_CALLBACK_TOKEN": (
            "1" if allow_missing_wechat_shop_callback_token else "0"
        ),
    }


def ensure_runtime_environment(
    environment_file: Path,
    *,
    target_environment: str,
    public_base_url: str,
    allow_missing_wechat_shop_callback_token: bool = False,
) -> dict[str, str]:
    values = runtime_environment_values(
        target_environment=target_environment,
        public_base_url=public_base_url,
        allow_missing_wechat_shop_callback_token=allow_missing_wechat_shop_callback_token,
    )
    _persist_environment_values(
        environment_file,
        values,
        remove_keys=DEPRECATED_RUNTIME_ENV_KEYS,
    )
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description="Persist non-secret browser/runtime security defaults.")
    parser.add_argument("--environment-file", required=True, type=Path)
    parser.add_argument("--target-environment", required=True, choices=("production", "test"))
    parser.add_argument("--public-base-url", required=True)
    parser.add_argument("--allow-missing-wechat-shop-callback-token", action="store_true")
    args = parser.parse_args()
    values = ensure_runtime_environment(
        args.environment_file,
        target_environment=args.target_environment,
        public_base_url=args.public_base_url,
        allow_missing_wechat_shop_callback_token=bool(args.allow_missing_wechat_shop_callback_token),
    )
    print(
        "runtime environment configured: "
        f"target={args.target_environment} secure_cookie={values['AICRM_ADMIN_SESSION_COOKIE_SECURE']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
