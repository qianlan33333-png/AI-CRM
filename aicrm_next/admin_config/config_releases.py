from __future__ import annotations

import hmac
from collections.abc import Mapping
from typing import Any

from aicrm_next.deployment_profile import DeploymentProfile, deployment_profile_from_environment
from aicrm_next.runtime_configuration import (
    CUTOVER_ELIGIBLE_RUNTIME_SETTING_KEYS,
    MANAGED_RUNTIME_SETTING_KEYS,
    RUNTIME_CONFIG_CUTOVER_KEYS_KEY,
    parse_runtime_config_cutover_keys,
)
from aicrm_next.shared.runtime_settings import environment_snapshot
from aicrm_next.shared.sensitive_data import SECRET_MASK
from aicrm_next.shared.secret_store import FileSecretStore, SecretStoreError, is_secret_reference

from .config_definitions import CONFIG_DEFINITIONS, get_config_definition
from .config_release_repository import ConfigReleaseRepository


class ConfigReleaseService:
    def __init__(
        self,
        repo: ConfigReleaseRepository | None = None,
        *,
        profile: DeploymentProfile | None = None,
        environment: Mapping[str, str] | None = None,
        secret_store: FileSecretStore | None = None,
    ) -> None:
        self.repo = repo or ConfigReleaseRepository()
        self.profile = profile or deployment_profile_from_environment()
        self._environment = environment_snapshot(
            (definition.key for definition in CONFIG_DEFINITIONS),
            environment=environment,
        )
        self._secret_store = secret_store

    def definitions(self) -> list[dict[str, Any]]:
        return [
            definition.to_dict()
            for definition in CONFIG_DEFINITIONS
            if self.profile.is_enabled(definition.capability_id)
        ]

    def create_draft(
        self,
        changes: dict[str, Any],
        *,
        operator: str,
        based_on_release_id: int | None = None,
        rollback_of_release_id: int | None = None,
    ) -> dict[str, Any]:
        normalized = self._normalize_changes(changes)
        if not normalized:
            raise ValueError("config release must contain at least one change")
        release = self.repo.create_draft(
            profile_id=self.profile.profile_id,
            changes=normalized,
            operator=_required_operator(operator),
            based_on_release_id=based_on_release_id,
            rollback_of_release_id=rollback_of_release_id,
        )
        return public_config_release(release)

    def validate(self, release_id: int) -> dict[str, Any]:
        release = self.repo.get_release(release_id)
        if not release:
            raise KeyError("config release not found")
        errors = self._validation_errors(release)
        validated = self.repo.set_validation(release_id=int(release_id), errors=errors)
        return public_config_release(validated)

    def publish(self, release_id: int, *, expected_checksum: str, operator: str) -> dict[str, Any]:
        release = self.repo.get_release(release_id)
        if not release:
            raise KeyError("config release not found")
        validated, expected_settings = self._validate_for_publish(release)
        if validated["status"] != "validated":
            raise ValueError("config release validation failed")
        published = self.repo.publish(
            release_id=int(release_id),
            expected_checksum=expected_checksum,
            operator=_required_operator(operator),
            profile=self.profile,
            sensitive_keys={definition.key for definition in CONFIG_DEFINITIONS if definition.sensitive},
            expected_settings=expected_settings,
        )
        return public_config_release(published)

    def rollback(self, release_id: int, *, operator: str) -> dict[str, Any]:
        target = self.repo.get_release(release_id)
        if not target:
            raise KeyError("config release not found")
        if target["status"] != "published":
            raise ValueError("only the active published config release can be rolled back")
        before = dict(target.get("before") or {})
        changes = {
            key: str(item.get("value") or "") if bool(item.get("exists")) else None
            for key, item in before.items()
        }
        draft = self.repo.create_draft(
            profile_id=self.profile.profile_id,
            changes=self._normalize_changes(changes, allow_required_removal=True),
            operator=_required_operator(operator),
            based_on_release_id=int(target["id"]),
            rollback_of_release_id=int(target["id"]),
        )
        validated, expected_settings = self._validate_for_publish(draft)
        if validated["status"] != "validated":
            raise ValueError("rollback config release validation failed")
        published = self.repo.publish(
            release_id=int(draft["id"]),
            expected_checksum=str(draft["checksum"]),
            operator=_required_operator(operator),
            profile=self.profile,
            sensitive_keys={definition.key for definition in CONFIG_DEFINITIONS if definition.sensitive},
            expected_settings=expected_settings,
        )
        return public_config_release(published)

    def get(self, release_id: int) -> dict[str, Any] | None:
        row = self.repo.get_release(release_id)
        return public_config_release(row) if row else None

    def list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return [
            public_config_release(row)
            for row in self.repo.list_releases(profile_id=self.profile.profile_id, limit=limit)
        ]

    def profile_state(self) -> dict[str, Any]:
        persisted = self.repo.get_profile_state(self.profile.profile_id)
        if not persisted:
            return {
                **self.profile.to_dict(),
                "active_config_release_id": None,
                "source": "deployment_profile",
            }
        persisted_contract = {
            key: persisted.get(key)
            for key in (
                "core_version",
                "config_schema_version",
                "activation_mode",
                "enabled_capabilities",
                "runtime_roles",
            )
        }
        current_contract = {
            key: self.profile.to_dict().get(key)
            for key in persisted_contract
        }
        return {
            **persisted,
            **self.profile.to_dict(),
            "active_config_release_id": persisted.get("active_config_release_id"),
            "persisted_profile_matches": persisted_contract == current_contract,
            "source": "deployment_profile_with_release_state",
        }

    def shadow_compare(self, release_id: int) -> dict[str, Any]:
        release = self.repo.get_release(release_id)
        if not release:
            raise KeyError("config release not found")
        changes = dict(release.get("changes") or {})
        stored = self.repo.get_app_settings([*changes, RUNTIME_CONFIG_CUTOVER_KEYS_KEY])
        active_cutover_keys = parse_runtime_config_cutover_keys(
            stored.get(
                RUNTIME_CONFIG_CUTOVER_KEYS_KEY,
                self._environment.get(RUNTIME_CONFIG_CUTOVER_KEYS_KEY, ""),
            )
        )
        rows: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for key, candidate in sorted(changes.items()):
            definition = get_config_definition(key)
            if definition is None:
                errors.append({"key": key, "error": "unknown config key"})
                continue
            fallback = str(self._environment.get(key) or definition.default or "")
            environment_authoritative = (
                key in MANAGED_RUNTIME_SETTING_KEYS
                and key not in active_cutover_keys
                and key in self._environment
            )
            if environment_authoritative:
                before_source = "environment_compat"
                before_value = str(self._environment.get(key) or "")
            elif key in stored:
                before_source = "app_settings"
                before_value = stored[key]
            elif key in self._environment:
                before_source = "environment"
                before_value = str(self._environment.get(key) or "")
            else:
                before_source = "default"
                before_value = str(definition.default or "")
            after_source = "release" if candidate is not None else "environment" if key in self._environment else "default"
            after_value = candidate if candidate is not None else fallback
            try:
                before_semantic = self._semantic_value(key, before_value)
                after_semantic = self._semantic_value(key, after_value)
                equivalent = hmac.compare_digest(before_semantic, after_semantic)
            except SecretStoreError as exc:
                equivalent = False
                errors.append({"key": key, "error": str(exc)})
            rows.append(
                {
                    "key": key,
                    "capability_id": definition.capability_id,
                    "sensitive": definition.sensitive,
                    "before_source": before_source,
                    "after_source": after_source,
                    "before": SECRET_MASK if definition.sensitive and before_value else "" if definition.sensitive else before_value,
                    "after": SECRET_MASK if definition.sensitive and after_value else "" if definition.sensitive else after_value,
                    "equivalent": equivalent,
                    "cutover_active": key in active_cutover_keys,
                }
            )
        return {
            "ok": not errors,
            "release_id": int(release_id),
            "profile_id": self.profile.profile_id,
            "rows": rows,
            "equivalent_count": sum(1 for row in rows if row["equivalent"]),
            "changed_count": sum(1 for row in rows if not row["equivalent"]),
            "errors": errors,
            "secrets_redacted": True,
        }

    def _semantic_value(self, key: str, value: Any) -> str:
        normalized = str(value or "")
        if not is_secret_reference(normalized):
            return normalized
        store = self._secret_store or FileSecretStore.from_environment()
        return store.read(normalized)

    def _normalize_changes(
        self,
        changes: dict[str, Any],
        *,
        allow_required_removal: bool = False,
    ) -> dict[str, str | None]:
        if not isinstance(changes, dict):
            raise ValueError("changes must be an object")
        result: dict[str, str | None] = {}
        for raw_key, raw_value in changes.items():
            key = str(raw_key or "").strip()
            definition = get_config_definition(key)
            if definition is None:
                raise ValueError(f"unknown config key: {key}")
            if not self.profile.is_enabled(definition.capability_id):
                raise ValueError(f"config capability is disabled in deployment profile: {definition.capability_id}")
            if raw_value is None:
                if definition.required and not allow_required_removal:
                    raise ValueError(f"required config key cannot be removed: {key}")
                result[key] = None
                continue
            normalized = definition.validate(raw_value)
            if definition.required and not normalized:
                raise ValueError(f"required config key cannot be empty: {key}")
            result[key] = normalized
        return result

    def _validation_errors(
        self,
        release: dict[str, Any],
        *,
        settings_state: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, str]]:
        errors: list[dict[str, str]] = []
        if release.get("profile_id") != self.profile.profile_id:
            errors.append({"key": "profile_id", "error": "deployment profile mismatch"})
        allow_required_removal = bool(release.get("rollback_of_release_id"))
        for key, value in dict(release.get("changes") or {}).items():
            try:
                self._normalize_changes(
                    {key: value},
                    allow_required_removal=allow_required_removal,
                )
            except ValueError as exc:
                errors.append({"key": str(key), "error": str(exc)})
        errors.extend(
            self._cutover_validation_errors(
                release,
                settings_state=settings_state,
            )
        )
        return errors

    def _cutover_validation_errors(
        self,
        release: dict[str, Any],
        *,
        settings_state: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, str]]:
        changes = dict(release.get("changes") or {})
        if RUNTIME_CONFIG_CUTOVER_KEYS_KEY not in changes:
            return []
        requested = parse_runtime_config_cutover_keys(
            changes.get(RUNTIME_CONFIG_CUTOVER_KEYS_KEY)
        )
        errors: list[dict[str, str]] = []
        unknown = sorted(requested - CUTOVER_ELIGIBLE_RUNTIME_SETTING_KEYS)
        for key in unknown:
            errors.append(
                {
                    "key": RUNTIME_CONFIG_CUTOVER_KEYS_KEY,
                    "error": f"unknown or non-cutover-eligible runtime config key: {key}",
                }
            )
        if unknown:
            return errors

        if settings_state is None:
            stored = self.repo.get_app_settings(
                [RUNTIME_CONFIG_CUTOVER_KEYS_KEY, *requested]
            )
        else:
            stored = {
                key: str(item.get("value") or "")
                for key, item in settings_state.items()
                if bool(item.get("exists"))
            }
        current = parse_runtime_config_cutover_keys(
            stored.get(
                RUNTIME_CONFIG_CUTOVER_KEYS_KEY,
                self._environment.get(RUNTIME_CONFIG_CUTOVER_KEYS_KEY, ""),
            )
        )
        for key in sorted(requested - current):
            definition = get_config_definition(key)
            if definition is None or not self.profile.is_enabled(definition.capability_id):
                errors.append(
                    {
                        "key": RUNTIME_CONFIG_CUTOVER_KEYS_KEY,
                        "error": f"runtime config capability is disabled: {key}",
                    }
                )
                continue
            if key in changes:
                errors.append(
                    {
                        "key": RUNTIME_CONFIG_CUTOVER_KEYS_KEY,
                        "error": f"stage {key} in an earlier release before activating cutover",
                    }
                )
                continue
            if key not in stored:
                errors.append(
                    {
                        "key": RUNTIME_CONFIG_CUTOVER_KEYS_KEY,
                        "error": f"runtime config has no staged app_settings value: {key}",
                    }
                )
                continue
            if key not in self._environment:
                continue
            try:
                environment_value = self._semantic_value(key, self._environment[key])
                staged_value = self._semantic_value(key, stored[key])
            except SecretStoreError as exc:
                errors.append({"key": key, "error": str(exc)})
                continue
            if not hmac.compare_digest(environment_value, staged_value):
                errors.append(
                    {
                        "key": key,
                        "error": "staged app_settings value differs from the environment compatibility source",
                    }
                )
        return errors

    def _validate_for_publish(
        self,
        release: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        precondition_keys = self._publish_precondition_keys(release)
        settings_state = self.repo.get_app_settings_state(precondition_keys)
        validated = self.repo.set_validation(
            release_id=int(release["id"]),
            errors=self._validation_errors(
                release,
                settings_state=settings_state,
            ),
        )
        return validated, settings_state

    @staticmethod
    def _publish_precondition_keys(release: dict[str, Any]) -> set[str]:
        changes = dict(release.get("changes") or {})
        keys = set(changes)
        if RUNTIME_CONFIG_CUTOVER_KEYS_KEY in changes:
            keys.update(
                parse_runtime_config_cutover_keys(
                    changes.get(RUNTIME_CONFIG_CUTOVER_KEYS_KEY)
                )
            )
        return keys


def public_config_release(release: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: value
        for key, value in dict(release or {}).items()
        if key not in {"changes", "before"}
    }
    changes = dict((release or {}).get("changes") or {})
    result["changes"] = {
        key: _public_config_value(key, value)
        for key, value in changes.items()
    }
    result["changed_keys"] = sorted(changes)
    result["before"] = {
        key: {
            "exists": bool(item.get("exists")),
            "value": _public_config_value(key, item.get("value")),
        }
        for key, item in dict((release or {}).get("before") or {}).items()
    }
    return result


def _public_config_value(key: str, value: Any) -> Any:
    definition = get_config_definition(key)
    if definition and definition.sensitive:
        return SECRET_MASK if value else ""
    return value


def _required_operator(operator: str) -> str:
    normalized = str(operator or "").strip()
    if not normalized:
        raise ValueError("operator is required")
    return normalized


__all__ = ["ConfigReleaseService", "public_config_release"]
