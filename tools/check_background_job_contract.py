from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aicrm_next.platform.platform_foundation.background_jobs.contract import WebhookRouteContract, webhook_route_contracts
from aicrm_next.platform.shared.route_ownership import load_route_manifest, route_key

DEFAULT_MANIFEST = ROOT / "docs" / "architecture" / "route_ownership_manifest.yml"
WEBHOOK_MARKERS = ("webhook", "callback", "notify")


@dataclass(frozen=True)
class BackgroundJobContractViolation:
    route: str
    rule: str
    route_name: str
    reason: str
    suggestion: str

    def format(self) -> str:
        return f"route={self.route}: route_name={self.route_name}: {self.rule}: {self.reason} Suggestion: {self.suggestion}"


def check_background_job_contract(manifest_path: str | Path = DEFAULT_MANIFEST) -> list[BackgroundJobContractViolation]:
    manifest = load_route_manifest(manifest_path)
    return validate_background_job_route_contracts(manifest, webhook_route_contracts())


def validate_background_job_route_contracts(
    manifest: list[dict[str, Any]],
    contracts: tuple[WebhookRouteContract, ...],
) -> list[BackgroundJobContractViolation]:
    violations: list[BackgroundJobContractViolation] = []
    contract_by_key: dict[str, WebhookRouteContract] = {}
    for contract in contracts:
        key = route_key(contract.path, contract.methods, contract.route_name)
        if key in contract_by_key:
            violations.append(
                BackgroundJobContractViolation(
                    route=contract.path,
                    route_name=contract.route_name,
                    rule="duplicate_webhook_route_contract",
                    reason="Duplicate webhook/background job route contract entry.",
                    suggestion="Keep exactly one contract entry for this route key.",
                )
            )
        contract_by_key[key] = contract

    manifest_by_key = {
        route_key(str(entry.get("path", "")), entry.get("methods") or (), str(entry.get("route_name", ""))): entry
        for entry in manifest
    }
    webhook_entries = [entry for entry in manifest if _is_webhook_like(entry)]
    webhook_keys = {
        route_key(str(entry.get("path", "")), entry.get("methods") or (), str(entry.get("route_name", "")))
        for entry in webhook_entries
    }

    for entry in webhook_entries:
        key = route_key(str(entry.get("path", "")), entry.get("methods") or (), str(entry.get("route_name", "")))
        contract = contract_by_key.get(key)
        route = str(entry.get("path", ""))
        route_name = str(entry.get("route_name", ""))
        if contract is None:
            violations.append(
                BackgroundJobContractViolation(
                    route=route,
                    route_name=route_name,
                    rule="missing_webhook_route_contract",
                    reason="Webhook/callback/notify route is present in route manifest but not registered in the background job route contract.",
                    suggestion="Add a WebhookRouteContract with expected external_effects, data_source, and rationale.",
                )
            )
            continue
        violations.extend(_validate_manifest_entry(entry, contract))

    for key, contract in contract_by_key.items():
        if key not in manifest_by_key:
            violations.append(
                BackgroundJobContractViolation(
                    route=contract.path,
                    route_name=contract.route_name,
                    rule="stale_webhook_route_contract",
                    reason="Background job route contract is not present in the route ownership manifest.",
                    suggestion="Remove stale contract entries or restore the route manifest entry.",
                )
            )
        elif key not in webhook_keys:
            violations.append(
                BackgroundJobContractViolation(
                    route=contract.path,
                    route_name=contract.route_name,
                    rule="contract_route_not_webhook_layer",
                    reason="Contract route is not classified as webhook/callback/notify in the route ownership manifest.",
                    suggestion="Keep the route manifest layer/path/name aligned with the background job route contract.",
                )
            )
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate AI-CRM Next webhook/background job route contracts.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    args = parser.parse_args(argv)

    violations = check_background_job_contract(args.manifest)
    if violations:
        print("Background job contract check failed:")
        for violation in violations:
            print(f"- {violation.format()}")
        return 1
    print(f"Background job contract check OK: {args.manifest}")
    return 0


def _is_webhook_like(entry: dict[str, Any]) -> bool:
    path = str(entry.get("path", "")).lower()
    route_name = str(entry.get("route_name", "")).lower()
    layer = str(entry.get("layer", "")).lower()
    return layer == "webhook" or any(marker in path or marker in route_name for marker in WEBHOOK_MARKERS)


def _validate_manifest_entry(entry: dict[str, Any], contract: WebhookRouteContract) -> list[BackgroundJobContractViolation]:
    violations: list[BackgroundJobContractViolation] = []
    route = str(entry.get("path", ""))
    route_name = str(entry.get("route_name", ""))
    owner = str(entry.get("capability_owner", "")).strip()
    rollback = str(entry.get("rollback", "")).strip()
    if not owner or owner == "unknown":
        violations.append(
            _violation(route, route_name, "missing_webhook_route_owner", "Webhook route must have a concrete capability_owner.", "Set the route manifest capability_owner to the owning Next context.")
        )
    if not rollback or rollback == "unknown":
        violations.append(
            _violation(route, route_name, "missing_webhook_rollback", "Webhook route must have a concrete rollback policy.", "Set rollback to previous_release or an approved route-specific rollback policy.")
        )
    if str(entry.get("external_effects", "")) != contract.expected_external_effects:
        violations.append(
            _violation(
                route,
                route_name,
                "webhook_external_effects_mismatch",
                f"external_effects={entry.get('external_effects')} does not match contract expected_external_effects={contract.expected_external_effects}.",
                "Update the manifest or the WebhookRouteContract with an explicit rationale.",
            )
        )
    if str(entry.get("data_source", "")) != contract.expected_data_source:
        violations.append(
            _violation(
                route,
                route_name,
                "webhook_data_source_mismatch",
                f"data_source={entry.get('data_source')} does not match contract expected_data_source={contract.expected_data_source}.",
                "Keep webhook route data_source aligned with the contract: read_model, command, or external_adapter.",
            )
        )
    if contract.expected_external_effects == "none" and not contract.external_effects_rationale.strip():
        violations.append(
            _violation(
                route,
                route_name,
                "missing_none_external_effects_rationale",
                "Webhook route contract uses external_effects=none without rationale.",
                "Explain why this route only records/enqueues work and does not perform direct external effects.",
            )
        )
    return violations


def _violation(route: str, route_name: str, rule: str, reason: str, suggestion: str) -> BackgroundJobContractViolation:
    return BackgroundJobContractViolation(route=route, route_name=route_name, rule=rule, reason=reason, suggestion=suggestion)


if __name__ == "__main__":
    raise SystemExit(main())
