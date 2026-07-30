#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from scripts.script_runtime import ensure_repo_root_on_path
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.script_runtime import ensure_repo_root_on_path

ensure_repo_root_on_path()

from scripts.ops.acknowledge_pre_cutover_welcome_terminal import (
    EXPECTED_CONFIRMATION as PRE_CUTOVER_WELCOME_CONFIRMATION,
)
from scripts.ops.acknowledge_pre_cutover_welcome_terminal import (
    acknowledge as acknowledge_pre_cutover_welcome,
)
from scripts.ops.acknowledge_production_terminal_history import (
    GROUP_CONFIRMATION,
    PRIVATE_CONFIRMATION,
    REFUND_CONFIRMATION,
)
from scripts.ops.acknowledge_production_terminal_history import (
    acknowledge as acknowledge_production_terminal_histories,
)
from scripts.ops.acknowledge_production_private_message_contact_absence import (
    AUTHORIZATION_BASE_SHA as PRIVATE_MESSAGE_CONTACT_ABSENCE_AUTHORIZATION_BASE_SHA,
)
from scripts.ops.acknowledge_production_private_message_contact_absence import (
    EXPECTED_CONFIRMATION as PRIVATE_MESSAGE_CONTACT_ABSENCE_CONFIRMATION,
)
from scripts.ops.acknowledge_production_private_message_contact_absence import (
    acknowledge as acknowledge_production_private_message_contact_absence,
)
from scripts.ops.acknowledge_production_welcome_timeout import (
    AUTHORIZATION_BASE_SHA as PRODUCTION_WELCOME_AUTHORIZATION_BASE_SHA,
)
from scripts.ops.acknowledge_production_welcome_timeout import (
    EXPECTED_CONFIRMATION as PRODUCTION_WELCOME_CONFIRMATION,
)
from scripts.ops.acknowledge_production_welcome_timeout import (
    acknowledge as acknowledge_production_welcome_timeout,
)


ROOT = Path(__file__).resolve().parents[2]
ACKNOWLEDGEMENT_MODES = {"required", "disabled"}


def acknowledge_release_terminal_histories(
    *,
    release_sha: str,
    actor: str,
    apply: bool,
    mode: str = "required",
) -> dict[str, Any]:
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in ACKNOWLEDGEMENT_MODES:
        raise ValueError("mode must be required or disabled")
    if len(release_sha) != 40 or any(character not in "0123456789abcdef" for character in release_sha):
        raise ValueError("release_sha must be one full lowercase SHA")
    if not str(actor or "").strip():
        raise ValueError("actor is required")
    if normalized_mode == "disabled":
        return {
            "ok": True,
            "applied": False,
            "mode": normalized_mode,
            "reason": "deployment_configuration_disabled",
            "provider_success_claimed": False,
            "real_external_call_executed": False,
            "replay_prohibited": True,
            "target_values_redacted": True,
        }

    pre_cutover = acknowledge_pre_cutover_welcome(
        manifest_path=ROOT / "docs" / "releases" / "queue_all_scope_cutover.json",
        release_sha=release_sha,
        authorization_base_sha="7369fa6c7858165097f25dff26f324d109cf7b80",
        confirmation=PRE_CUTOVER_WELCOME_CONFIRMATION,
        actor=actor,
        reason="authorized pre-cutover welcome 41050 no-replay history",
        apply=apply,
    )
    production_histories = acknowledge_production_terminal_histories(
        manifest_path=(
            ROOT / "docs" / "releases" / "production_terminal_history_acknowledgements.json"
        ),
        release_sha=release_sha,
        authorization_base_sha="8ab2f80ec8a6808a357a5911ace38128599a3d3d",
        private_confirmation=PRIVATE_CONFIRMATION,
        refund_confirmation=REFUND_CONFIRMATION,
        group_confirmation=GROUP_CONFIRMATION,
        actor=actor,
        reason="operator-authorized production terminal histories; no replay",
        apply=apply,
        acknowledge_refund_histories=False,
    )
    production_welcome = acknowledge_production_welcome_timeout(
        manifest_path=(
            ROOT / "docs" / "releases" / "production_welcome_timeout_acknowledgement.json"
        ),
        release_sha=release_sha,
        authorization_base_sha=PRODUCTION_WELCOME_AUTHORIZATION_BASE_SHA,
        confirmation=PRODUCTION_WELCOME_CONFIRMATION,
        actor=actor,
        reason="operator-authorized welcome job 2157 timeout history; no replay",
        apply=apply,
    )
    production_private_message_contact_absence = (
        acknowledge_production_private_message_contact_absence(
            manifest_path=(
                ROOT
                / "docs"
                / "releases"
                / "production_private_message_contact_absence_20260728_acknowledgement.json"
            ),
            release_sha=release_sha,
            authorization_base_sha=PRIVATE_MESSAGE_CONTACT_ABSENCE_AUTHORIZATION_BASE_SHA,
            confirmation=PRIVATE_MESSAGE_CONTACT_ABSENCE_CONFIRMATION,
            actor=actor,
            reason=(
                "operator-authorized 2026-07-28 private-message contact-absence "
                "terminal histories; no replay"
            ),
            apply=apply,
        )
    )
    return {
        "ok": True,
        "applied": apply,
        "pre_cutover_welcome": pre_cutover,
        "production_terminal_histories": production_histories,
        "production_welcome_timeout": production_welcome,
        "production_private_message_contact_absence": (
            production_private_message_contact_absence
        ),
        "provider_success_claimed": False,
        "real_external_call_executed": False,
        "replay_prohibited": True,
        "target_values_redacted": True,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply the exact append-only terminal acknowledgements required by a release.",
    )
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--mode", choices=sorted(ACKNOWLEDGEMENT_MODES), default="required")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = acknowledge_release_terminal_histories(
        release_sha=args.release_sha,
        actor=args.actor,
        apply=bool(args.apply),
        mode=args.mode,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
