#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()

from aicrm_next.admin_config.config_definitions import (  # noqa: E402
    CONFIG_DEFINITIONS_BY_KEY,
)
from aicrm_next.admin_config.config_release_repository import (  # noqa: E402
    ConfigReleaseRepository,
)
from aicrm_next.admin_config.config_releases import ConfigReleaseService  # noqa: E402
from aicrm_next.deployment_profile import (  # noqa: E402
    DeploymentProfile,
    deployment_profile_from_environment,
)
from aicrm_next.runtime_configuration import (  # noqa: E402
    CUTOVER_ELIGIBLE_RUNTIME_SETTING_KEYS,
    RUNTIME_CONFIG_CUTOVER_KEYS_KEY,
    parse_runtime_config_cutover_keys,
)
from aicrm_next.shared.db_session import get_engine  # noqa: E402
from aicrm_next.shared.secret_store import is_secret_reference  # noqa: E402


_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_MAX_ACTIVATION_KEYS = 25


class RuntimeConfigOperationError(RuntimeError):
    """An operator-safe failure whose message never contains a setting value."""


def _normalized_requested_keys(keys: Sequence[str]) -> tuple[str, ...]:
    result = sorted({str(key or "").strip() for key in keys if str(key or "").strip()})
    unknown = sorted(set(result) - CUTOVER_ELIGIBLE_RUNTIME_SETTING_KEYS)
    if unknown:
        raise RuntimeConfigOperationError("unknown or non-cutover-eligible keys: " + ", ".join(unknown))
    return tuple(result)


def _active_keys(
    *,
    service: ConfigReleaseService,
    environment: Mapping[str, str],
) -> frozenset[str]:
    stored = service.repo.get_app_settings([RUNTIME_CONFIG_CUTOVER_KEYS_KEY])
    return parse_runtime_config_cutover_keys(
        stored.get(
            RUNTIME_CONFIG_CUTOVER_KEYS_KEY,
            str(environment.get(RUNTIME_CONFIG_CUTOVER_KEYS_KEY) or ""),
        )
    )


def runtime_config_inventory(
    *,
    service: ConfigReleaseService,
    environment: Mapping[str, str],
    requested_keys: Sequence[str] = (),
) -> dict[str, Any]:
    requested = _normalized_requested_keys(requested_keys)
    candidate_keys = requested or tuple(sorted(CUTOVER_ELIGIBLE_RUNTIME_SETTING_KEYS))
    stored = service.repo.get_app_settings([*candidate_keys, RUNTIME_CONFIG_CUTOVER_KEYS_KEY])
    active = _active_keys(service=service, environment=environment)
    items: list[dict[str, Any]] = []
    stage_changes: dict[str, str] = {}
    blockers: list[dict[str, str]] = []

    for key in candidate_keys:
        definition = CONFIG_DEFINITIONS_BY_KEY.get(key)
        if definition is None:
            blockers.append({"key": key, "error": "config_definition_missing"})
            continue
        capability_enabled = service.profile.is_enabled(definition.capability_id)
        source = "missing"
        value = ""
        if key in environment:
            source = "environment_compat"
            value = str(environment.get(key) or "")
        elif key in stored:
            source = "app_settings"
            value = str(stored.get(key) or "")

        status = "stageable"
        if not capability_enabled:
            status = "capability_disabled"
        elif key in active:
            status = "already_active"
        elif source == "missing":
            status = "missing"
        elif definition.sensitive and value and not is_secret_reference(value):
            status = "raw_secret_blocked"
            blockers.append({"key": key, "error": "raw_secret_must_be_migrated_first"})
        else:
            try:
                normalized = definition.validate(value)
            except ValueError:
                status = "invalid_value"
                blockers.append({"key": key, "error": "definition_validation_failed"})
            else:
                stage_changes[key] = normalized

        if requested and status in {"capability_disabled", "already_active", "missing"}:
            blockers.append({"key": key, "error": status})
        items.append(
            {
                "key": key,
                "capability_id": definition.capability_id,
                "source": source,
                "sensitive": definition.sensitive,
                "active": key in active,
                "status": status,
            }
        )

    return {
        "ok": not blockers,
        "profile_id": service.profile.profile_id,
        "activation_mode": service.profile.activation_mode,
        "requested_count": len(requested),
        "candidate_count": len(candidate_keys),
        "stageable_count": len(stage_changes),
        "active_count": len(active),
        "blocker_count": len(blockers),
        "blockers": blockers,
        "items": items,
        "stage_changes": stage_changes,
        "secrets_redacted": True,
        "real_external_call_executed": False,
    }


def required_stage_confirmation(release_sha: str) -> str:
    normalized = str(release_sha or "").strip()
    if not _FULL_SHA.fullmatch(normalized):
        raise RuntimeConfigOperationError("expected release SHA must be a full lowercase SHA")
    return f"STAGE_RUNTIME_CONFIG_{normalized}"


def required_activation_confirmation(release_sha: str, keys: Sequence[str]) -> str:
    normalized_keys = _normalized_requested_keys(keys)
    if not normalized_keys:
        raise RuntimeConfigOperationError("activation requires at least one new key")
    digest = hashlib.sha256("\n".join(normalized_keys).encode("utf-8")).hexdigest()[:16]
    normalized_sha = str(release_sha or "").strip()
    if not _FULL_SHA.fullmatch(normalized_sha):
        raise RuntimeConfigOperationError("expected release SHA must be a full lowercase SHA")
    return f"ACTIVATE_RUNTIME_CONFIG_{normalized_sha}_{digest}"


def required_rollback_confirmation(release_sha: str, release_id: int) -> str:
    normalized_sha = str(release_sha or "").strip()
    if not _FULL_SHA.fullmatch(normalized_sha):
        raise RuntimeConfigOperationError("expected release SHA must be a full lowercase SHA")
    return f"ROLLBACK_RUNTIME_CONFIG_{normalized_sha}_{int(release_id)}"


def stage_runtime_config(
    *,
    service: ConfigReleaseService,
    environment: Mapping[str, str],
    requested_keys: Sequence[str],
    operator: str,
    release_sha: str,
    confirmation: str,
    execute: bool,
) -> dict[str, Any]:
    inventory = runtime_config_inventory(
        service=service,
        environment=environment,
        requested_keys=requested_keys,
    )
    expected_confirmation = required_stage_confirmation(release_sha)
    public_inventory = {key: value for key, value in inventory.items() if key != "stage_changes"}
    public_inventory["required_confirmation"] = expected_confirmation
    public_inventory["execute"] = bool(execute)
    if not execute:
        return public_inventory
    if confirmation != expected_confirmation:
        raise RuntimeConfigOperationError("stage confirmation does not match the exact release")
    if inventory["blockers"]:
        raise RuntimeConfigOperationError("runtime config inventory contains blockers")
    changes = dict(inventory["stage_changes"])
    if not changes:
        return {
            **public_inventory,
            "ok": True,
            "no_op": True,
            "published": False,
        }

    draft = service.create_draft(changes, operator=operator)
    comparison = service.shadow_compare(int(draft["id"]))
    mismatches = sorted(str(row["key"]) for row in comparison["rows"] if not row["equivalent"])
    if comparison["errors"] or mismatches:
        raise RuntimeConfigOperationError(
            "shadow comparison failed for keys: " + ", ".join(mismatches or ["unknown"])
        )
    published = service.publish(
        int(draft["id"]),
        expected_checksum=str(draft["checksum"]),
        operator=operator,
    )
    return {
        "ok": True,
        "no_op": False,
        "published": True,
        "profile_id": service.profile.profile_id,
        "release_id": int(published["id"]),
        "release_key": str(published["release_key"]),
        "changed_keys": sorted(changes),
        "changed_count": len(changes),
        "shadow_equivalent_count": int(comparison["equivalent_count"]),
        "secrets_redacted": True,
        "real_external_call_executed": False,
    }


def _activation_keys(
    *,
    service: ConfigReleaseService,
    environment: Mapping[str, str],
    requested_keys: Sequence[str],
    capability_id: str,
) -> tuple[str, ...]:
    requested = _normalized_requested_keys(requested_keys)
    normalized_capability = str(capability_id or "").strip()
    if bool(requested) == bool(normalized_capability):
        raise RuntimeConfigOperationError("provide exactly one of explicit keys or capability_id")
    stored = service.repo.get_app_settings(
        [*CUTOVER_ELIGIBLE_RUNTIME_SETTING_KEYS, RUNTIME_CONFIG_CUTOVER_KEYS_KEY]
    )
    active = _active_keys(service=service, environment=environment)
    if normalized_capability:
        requested = tuple(
            sorted(
                key
                for key in CUTOVER_ELIGIBLE_RUNTIME_SETTING_KEYS
                if key in stored
                and key not in active
                and CONFIG_DEFINITIONS_BY_KEY[key].capability_id == normalized_capability
            )
        )
    missing = sorted(set(requested) - set(stored))
    if missing:
        raise RuntimeConfigOperationError("activation keys have no staged value: " + ", ".join(missing))
    disabled = sorted(
        key
        for key in requested
        if not service.profile.is_enabled(CONFIG_DEFINITIONS_BY_KEY[key].capability_id)
    )
    if disabled:
        raise RuntimeConfigOperationError("activation capability is disabled for keys: " + ", ".join(disabled))
    return tuple(sorted(set(requested) - set(active)))


def activate_runtime_config(
    *,
    service: ConfigReleaseService,
    environment: Mapping[str, str],
    requested_keys: Sequence[str],
    capability_id: str,
    operator: str,
    release_sha: str,
    confirmation: str,
    execute: bool,
    max_keys: int = DEFAULT_MAX_ACTIVATION_KEYS,
) -> dict[str, Any]:
    new_keys = _activation_keys(
        service=service,
        environment=environment,
        requested_keys=requested_keys,
        capability_id=capability_id,
    )
    if not new_keys:
        return {
            "ok": True,
            "no_op": True,
            "execute": bool(execute),
            "new_keys": [],
            "real_external_call_executed": False,
        }
    if len(new_keys) > int(max_keys):
        raise RuntimeConfigOperationError(
            f"activation batch exceeds max_keys={int(max_keys)}; split the capability into smaller batches"
        )
    expected_confirmation = required_activation_confirmation(release_sha, new_keys)
    preview = {
        "ok": True,
        "no_op": False,
        "execute": bool(execute),
        "profile_id": service.profile.profile_id,
        "new_keys": list(new_keys),
        "new_key_count": len(new_keys),
        "required_confirmation": expected_confirmation,
        "secrets_redacted": True,
        "real_external_call_executed": False,
    }
    if not execute:
        return preview
    if confirmation != expected_confirmation:
        raise RuntimeConfigOperationError("activation confirmation does not match the exact release and key set")

    current = _active_keys(service=service, environment=environment)
    catalog = ",".join(sorted(current | set(new_keys)))
    draft = service.create_draft(
        {RUNTIME_CONFIG_CUTOVER_KEYS_KEY: catalog},
        operator=operator,
    )
    validated = service.validate(int(draft["id"]))
    if validated["status"] != "validated":
        raise RuntimeConfigOperationError("activation validation failed")
    published = service.publish(
        int(draft["id"]),
        expected_checksum=str(draft["checksum"]),
        operator=operator,
    )
    return {
        **preview,
        "published": True,
        "release_id": int(published["id"]),
        "release_key": str(published["release_key"]),
        "active_key_count": len(current | set(new_keys)),
    }


def rollback_runtime_config(
    *,
    service: ConfigReleaseService,
    release_id: int,
    operator: str,
    release_sha: str,
    confirmation: str,
    execute: bool,
) -> dict[str, Any]:
    expected_confirmation = required_rollback_confirmation(release_sha, release_id)
    preview = {
        "ok": True,
        "execute": bool(execute),
        "target_release_id": int(release_id),
        "required_confirmation": expected_confirmation,
        "secrets_redacted": True,
        "real_external_call_executed": False,
    }
    if not execute:
        return preview
    if confirmation != expected_confirmation:
        raise RuntimeConfigOperationError("rollback confirmation does not match the exact release")
    rolled_back = service.rollback(int(release_id), operator=operator)
    return {
        **preview,
        "published": True,
        "rollback_release_id": int(rolled_back["id"]),
        "rollback_of_release_id": int(rolled_back["rollback_of_release_id"]),
        "changed_keys": list(rolled_back["changed_keys"]),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inventory, stage, activate, or roll back managed runtime configuration without exposing values."
    )
    parser.add_argument("action", choices=("inventory", "stage", "activate", "rollback"))
    parser.add_argument("--database-url", default="")
    parser.add_argument("--key", action="append", default=[])
    parser.add_argument("--capability-id", default="")
    parser.add_argument("--operator", default="codex-runtime-config")
    parser.add_argument("--expected-release-sha", default="")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--release-id", type=int, default=0)
    parser.add_argument("--max-keys", type=int, default=DEFAULT_MAX_ACTIVATION_KEYS)
    parser.add_argument("--execute", action="store_true")
    return parser


def _service(
    *,
    database_url: str,
    environment: Mapping[str, str],
) -> ConfigReleaseService:
    profile: DeploymentProfile = deployment_profile_from_environment(environment)
    engine = get_engine(database_url or None)
    return ConfigReleaseService(
        ConfigReleaseRepository(engine=engine),
        profile=profile,
        environment=environment,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        service = _service(database_url=args.database_url, environment=os.environ)
        if args.action == "inventory":
            report = runtime_config_inventory(
                service=service,
                environment=os.environ,
                requested_keys=args.key,
            )
            report.pop("stage_changes", None)
        elif args.action == "stage":
            report = stage_runtime_config(
                service=service,
                environment=os.environ,
                requested_keys=args.key,
                operator=args.operator,
                release_sha=args.expected_release_sha,
                confirmation=args.confirmation,
                execute=bool(args.execute),
            )
        elif args.action == "activate":
            report = activate_runtime_config(
                service=service,
                environment=os.environ,
                requested_keys=args.key,
                capability_id=args.capability_id,
                operator=args.operator,
                release_sha=args.expected_release_sha,
                confirmation=args.confirmation,
                execute=bool(args.execute),
                max_keys=args.max_keys,
            )
        else:
            if args.release_id <= 0:
                raise RuntimeConfigOperationError("rollback requires a positive release_id")
            report = rollback_runtime_config(
                service=service,
                release_id=args.release_id,
                operator=args.operator,
                release_sha=args.expected_release_sha,
                confirmation=args.confirmation,
                execute=bool(args.execute),
            )
    except RuntimeConfigOperationError as exc:
        print_json({"ok": False, "error_code": "runtime_config_operation_blocked", "error": str(exc)}, sort_keys=True)
        return 2
    except Exception as exc:  # pragma: no cover - defensive redaction at the CLI boundary
        print_json({"ok": False, "error_code": type(exc).__name__}, sort_keys=True)
        return 1
    print_json(report, sort_keys=True)
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
