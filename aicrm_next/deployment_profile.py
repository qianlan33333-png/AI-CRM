from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from .capability_registry import CAPABILITY_SPECS, CORE_VERSION, default_capability_ids, get_capability_spec
from .shared.runtime_settings import startup_environment_setting


ActivationMode = Literal["observe", "enforce"]
CorpMode = Literal["single_corp_multi_app"]

PROFILE_SCHEMA_VERSION = 1
RUNTIME_ROLES = ("web", "callback", "internal_worker", "external_worker", "scheduler")
DEPLOYMENT_PROFILE_PATH_ENV = "AICRM_DEPLOYMENT_PROFILE_PATH"
_PROFILE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
_SENSITIVE_KEY_MARKERS = ("secret", "password", "private_key", "access_token", "api_key")


@dataclass(frozen=True)
class DeploymentProfile:
    profile_id: str
    core_version: str
    enabled_capabilities: tuple[str, ...]
    runtime_roles: tuple[str, ...]
    config_schema_version: int
    schema_version: int = PROFILE_SCHEMA_VERSION
    corp_mode: CorpMode = "single_corp_multi_app"
    activation_mode: ActivationMode = "observe"
    description: str = ""

    def is_enabled(self, capability_id: str) -> bool:
        return str(capability_id or "").strip() in self.enabled_capabilities

    def allows_runtime(self, capability_id: str) -> bool:
        """Observe mode inventories every legacy contribution; enforce mode activates only enabled packages."""

        return self.activation_mode == "observe" or self.is_enabled(capability_id)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def default_deployment_profile() -> DeploymentProfile:
    return DeploymentProfile(
        profile_id="wecom-core",
        core_version=CORE_VERSION,
        enabled_capabilities=default_capability_ids(),
        runtime_roles=RUNTIME_ROLES,
        config_schema_version=1,
        activation_mode="observe",
        description="Single-corp, multi-app WeCom SCRM core profile",
    )


def load_deployment_profile(path: str | Path) -> DeploymentProfile:
    source = Path(path)
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("deployment profile must contain a JSON object")
    sensitive_paths = _find_sensitive_paths(raw)
    if sensitive_paths:
        raise ValueError("deployment profile must not contain secrets: " + ", ".join(sensitive_paths))
    profile = DeploymentProfile(
        profile_id=str(raw.get("profile_id") or "").strip(),
        core_version=str(raw.get("core_version") or "").strip(),
        enabled_capabilities=tuple(str(item or "").strip() for item in raw.get("enabled_capabilities", []) or []),
        runtime_roles=tuple(str(item or "").strip() for item in raw.get("runtime_roles", []) or []),
        config_schema_version=int(raw.get("config_schema_version") or 0),
        schema_version=int(raw.get("schema_version") or 0),
        corp_mode=str(raw.get("corp_mode") or "").strip(),  # type: ignore[arg-type]
        activation_mode=str(raw.get("activation_mode") or "").strip(),  # type: ignore[arg-type]
        description=str(raw.get("description") or "").strip(),
    )
    errors = validate_deployment_profile(profile)
    if errors:
        raise ValueError("invalid deployment profile: " + "; ".join(errors))
    return profile


def deployment_profile_from_environment(environ: Mapping[str, str] | None = None) -> DeploymentProfile:
    path = startup_environment_setting(
        DEPLOYMENT_PROFILE_PATH_ENV,
        environment=environ,
    ).strip()
    return load_deployment_profile(path) if path else default_deployment_profile()


def validate_deployment_profile(profile: DeploymentProfile) -> list[str]:
    errors: list[str] = []
    if not _PROFILE_ID_PATTERN.fullmatch(profile.profile_id):
        errors.append("profile_id must match ^[a-z][a-z0-9_-]{2,63}$")
    if profile.schema_version != PROFILE_SCHEMA_VERSION:
        errors.append(f"schema_version must be {PROFILE_SCHEMA_VERSION}")
    if profile.core_version != CORE_VERSION:
        errors.append(f"core_version must be {CORE_VERSION}")
    if profile.corp_mode != "single_corp_multi_app":
        errors.append("corp_mode must be single_corp_multi_app")
    if profile.activation_mode not in {"observe", "enforce"}:
        errors.append("activation_mode must be observe or enforce")
    if profile.config_schema_version < 1:
        errors.append("config_schema_version must be positive")

    if len(profile.enabled_capabilities) != len(set(profile.enabled_capabilities)):
        errors.append("enabled_capabilities must be unique")
    known_capabilities = {spec.capability_id for spec in CAPABILITY_SPECS}
    for capability_id in profile.enabled_capabilities:
        if capability_id not in known_capabilities:
            errors.append(f"unknown capability: {capability_id}")
    missing_core = sorted(set(default_capability_ids()) - set(profile.enabled_capabilities))
    if missing_core:
        errors.append("core capabilities cannot be disabled: " + ", ".join(missing_core))
    for capability_id in profile.enabled_capabilities:
        spec = get_capability_spec(capability_id)
        if spec is None:
            continue
        missing_dependencies = sorted(set(spec.dependencies) - set(profile.enabled_capabilities))
        if missing_dependencies:
            errors.append(f"{capability_id} missing dependencies: {', '.join(missing_dependencies)}")

    if len(profile.runtime_roles) != len(set(profile.runtime_roles)):
        errors.append("runtime_roles must be unique")
    unknown_roles = sorted(set(profile.runtime_roles) - set(RUNTIME_ROLES))
    if unknown_roles:
        errors.append("unknown runtime roles: " + ", ".join(unknown_roles))
    missing_roles = sorted(set(RUNTIME_ROLES) - set(profile.runtime_roles))
    if missing_roles:
        errors.append("required runtime roles are missing: " + ", ".join(missing_roles))
    return errors


def _find_sensitive_paths(value: object, path: str = "") -> list[str]:
    if isinstance(value, dict):
        result: list[str] = []
        for key, nested in value.items():
            normalized_key = str(key or "").strip().lower()
            current_path = f"{path}.{key}" if path else str(key)
            if any(marker in normalized_key for marker in _SENSITIVE_KEY_MARKERS):
                result.append(current_path)
            result.extend(_find_sensitive_paths(nested, current_path))
        return result
    if isinstance(value, list):
        return [item for index, nested in enumerate(value) for item in _find_sensitive_paths(nested, f"{path}[{index}]")]
    return []


__all__ = [
    "PROFILE_SCHEMA_VERSION",
    "RUNTIME_ROLES",
    "DEPLOYMENT_PROFILE_PATH_ENV",
    "DeploymentProfile",
    "deployment_profile_from_environment",
    "default_deployment_profile",
    "load_deployment_profile",
    "validate_deployment_profile",
]
