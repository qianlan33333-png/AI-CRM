#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/development/autonomous_development_loop.md"
STATE = ROOT / "docs/development/phase_execution_state.yaml"
STOP = ROOT / "docs/development/autonomous_stop_conditions.yaml"
MANIFEST = ROOT / "docs/architecture/route_ownership_manifest.yml"
BACKLOG = ROOT / "docs/development/codex_autopilot_runtime_runbook.md"

REQUIRED_STATE_FIELDS = {
    "version",
    "status",
    "autopilot",
    "current_phase",
    "last_merged_pr",
    "last_merged_cleanup_wave",
    "recommended_next_pr",
    "owner_approval_required",
    "runtime_behavior_changed",
    "delete_ready",
    "cleanup_closeout",
    "active_safety_gates",
    "completed_runtime_fallback_tracks",
    "remaining_runtime_fallback_tracks",
    "protected_runtime_boundaries",
    "next_cleanup_candidates",
}
EXPECTED_SAFETY_GATES = {
    "check_automerge_eligibility",
    "check_autonomous_development_loop",
    "pr_smoke",
}
EXPECTED_RUNTIME_BOUNDARIES = {
    "app.py",
    "aicrm_next/main.py",
    "aicrm_next/production_compat/ deleted package must not return",
    "deleted legacy package must not return",
    "migrations/deploy/nginx/systemd",
    "payment/OAuth/WeCom callback/public submit/product/checkout/order/image upload/runtime/outbound paths",
}
EXPECTED_NEXT_CANDIDATES = {
    "non_runtime_cleanup": "complete",
    "runtime_fallback_cleanup": "closed_as_global_task",
    "future_runtime_migration": "product_specific_only",
    "high_risk_external_cleanup": "separate_owner_approval_required",
}
EXPECTED_CLEANUP_CLOSEOUT = {
    "global_non_runtime_cleanup": "complete",
    "low_medium_admin_runtime_fallback_migration": "complete_enough_for_product_development",
    "global_deletion_task_status": "closed",
    "broad_legacy_runtime_deletion_authorized": False,
    "continue_deletion_mode": False,
}
EXPECTED_COMPLETED_RUNTIME_TRACKS = {
    "channels_entry_channel_next_ownership",
    "channel_legacy_runtime_cleanup_after_next_entry_bindings",
    "media_material_libraries_next_ownership",
    "sidebar_customer_broad_fallback_narrowing",
    "sidebar_customer_readonly_cleanup",
    "automation_member_manual_send_focus_sop_retired",
}
EXPECTED_REMAINING_RUNTIME_TRACKS = {
    "wecom_callback_events",
    "questionnaire_oauth_public_submit",
    "wecom_tags_tag_groups",
    "payment_checkout_orders_public_products",
    "automation_runtime_run_due_reply_monitor_jobs",
    "sidebar_write_external_paths",
    "hxc_dashboard_cloud_orchestrator",
}
STOP_IDS = {
    "production_owner_switch",
    "fallback_removal",
    "production_write",
    "real_external_call",
    "timer_or_execution",
    "outbound_send",
    "deploy_config",
    "destructive_migration",
    "delete_ready",
    "canary_approval",
}
PROTECTED_EXACT = {
    "app.py",
    "aicrm_next/main.py",
}
STARTUP_CLOSEOUT_ALLOWED_EXACT = {
    ".github/workflows/deploy.yml",
    "app.py",
}
PROTECTED_PREFIXES = (
    "migrations/",
    "deploy/",
    "systemd/",
    "nginx/",
)
RUNTIME_FALLBACK_ALLOWED_EXACT = {
    "aicrm_next/crm/customer_read_model/api.py",
    "aicrm_next/automation_engine/api.py",
}
GOVERNANCE_ALLOWED_PREFIXES = (
    "docs/",
    "tools/",
    "tests/",
)
GOVERNANCE_ALLOWED_EXACT = {
    "README.md",
    ".github/workflows/ci.yml",
    "aicrm_next/automation_engine/channels_api.py",
    "aicrm_next/extensions/ai/ai_audience_ops/agent_copywriting.py",
    "aicrm_next/extensions/ai/ai_audience_ops/agent_gateway.py",
    "scripts/codex_autopilot_tick.sh",
    "scripts/run_lint.py",
    "requirements.txt",
}
DELETED_LEGACY_PACKAGE_PREFIX = "wecom_ability" + "_service/"
DELETED_PRODUCTION_COMPAT_PREFIX = "aicrm_next/production_compat/"


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"true", "false"}:
        return value == "true"
    if value == "[]":
        return []
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        return value


def _strip_yaml_comments(line: str) -> str:
    in_single = False
    in_double = False
    for index, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return line[:index].rstrip()
    return line.rstrip()


def _yaml_lines(text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        stripped = _strip_yaml_comments(raw)
        if stripped.strip():
            lines.append((len(stripped) - len(stripped.lstrip(" ")), stripped.strip()))
    return lines


def _parse_yaml_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        return {}, index
    current_indent, current_text = lines[index]
    if current_indent < indent:
        return {}, index
    if current_text.startswith("- "):
        result: list[Any] = []
        while index < len(lines):
            line_indent, text = lines[index]
            if line_indent != indent or not text.startswith("- "):
                break
            item_text = text[2:].strip()
            index += 1
            if not item_text:
                value, index = _parse_yaml_block(lines, index, indent + 2)
                result.append(value)
            elif ":" not in item_text:
                result.append(_parse_scalar(item_text))
            else:
                key, raw_value = item_text.split(":", 1)
                item: dict[str, Any] = {}
                if raw_value.strip():
                    item[key.strip()] = _parse_scalar(raw_value)
                else:
                    value, index = _parse_yaml_block(lines, index, indent + 2)
                    item[key.strip()] = value
                while index < len(lines) and lines[index][0] > indent:
                    nested_value, index = _parse_yaml_block(lines, index, indent + 2)
                    if isinstance(nested_value, dict):
                        item.update(nested_value)
                result.append(item)
        return result, index
    result: dict[str, Any] = {}
    while index < len(lines):
        line_indent, text = lines[index]
        if line_indent != indent or text.startswith("- "):
            break
        if ":" not in text:
            index += 1
            continue
        key, raw_value = text.split(":", 1)
        raw_value = raw_value.strip()
        index += 1
        if raw_value:
            result[key.strip()] = _parse_scalar(raw_value)
        else:
            value, index = _parse_yaml_block(lines, index, indent + 2)
            result[key.strip()] = value
    return result, index


def load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text) or {}
    except ModuleNotFoundError:
        data, _ = _parse_yaml_block(_yaml_lines(text), 0, 0)
        return data if isinstance(data, dict) else {}


def _as_strings(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value}


def _run_git(args: list[str]) -> tuple[bool, str, str]:
    proc = subprocess.run(["git", *args], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return proc.returncode == 0, proc.stdout, proc.stderr


def _changed_files() -> set[str]:
    changed: set[str] = set()
    for args in (
        ["diff", "--name-only", "origin/main...HEAD"],
        ["diff", "--name-only"],
        ["diff", "--name-only", "--cached"],
        ["ls-files", "--others", "--exclude-standard"],
    ):
        ok, stdout, _ = _run_git(args)
        if ok:
            changed.update(line.strip() for line in stdout.splitlines() if line.strip())
    return changed


def _validate_current_state(state: dict[str, Any], blockers: list[str]) -> None:
    missing_state_fields = sorted(REQUIRED_STATE_FIELDS - set(state))
    if missing_state_fields:
        blockers.append(f"phase_execution_state missing fields: {missing_state_fields}")
    if state.get("current_phase") != "post_phase7_cleanup_closeout":
        blockers.append("current_phase must be post_phase7_cleanup_closeout")
    if state.get("last_merged_pr") != "#878":
        blockers.append("last_merged_pr must record merged runtime fallback cleanup PR #878")
    if state.get("last_merged_cleanup_wave") != "sidebar_customer_readonly_track3b":
        blockers.append("last_merged_cleanup_wave must record sidebar/customer readonly track 3b")
    if state.get("recommended_next_pr") != "product_development_or_targeted_runtime_migration":
        blockers.append("recommended_next_pr must point to product development or targeted runtime migration")
    if state.get("owner_approval_required") is not False:
        blockers.append("owner_approval_required must be false for the closeout checkpoint")
    if state.get("runtime_behavior_changed") is not False:
        blockers.append("runtime_behavior_changed must remain false")
    if state.get("delete_ready") is not False:
        blockers.append("delete_ready must remain false")

    autopilot = state.get("autopilot") if isinstance(state.get("autopilot"), dict) else {}
    if autopilot.get("enabled") is not True:
        blockers.append("autopilot.enabled must be true")
    if autopilot.get("mode") != "product_development_with_targeted_runtime_migration":
        blockers.append("autopilot.mode must be product_development_with_targeted_runtime_migration")
    if autopilot.get("runtime_changes_allowed") != "targeted_owner_approved_only":
        blockers.append("autopilot.runtime_changes_allowed must be targeted_owner_approved_only")
    if autopilot.get("admin_override_allowed") is not False:
        blockers.append("autopilot.admin_override_allowed must be false")

    if state.get("cleanup_closeout") != EXPECTED_CLEANUP_CLOSEOUT:
        blockers.append(f"cleanup_closeout must be exactly {EXPECTED_CLEANUP_CLOSEOUT}")
    completed_tracks = _as_strings(state.get("completed_runtime_fallback_tracks"))
    if completed_tracks != EXPECTED_COMPLETED_RUNTIME_TRACKS:
        blockers.append(f"completed_runtime_fallback_tracks must be exactly {sorted(EXPECTED_COMPLETED_RUNTIME_TRACKS)}")
    remaining_tracks = state.get("remaining_runtime_fallback_tracks")
    if not isinstance(remaining_tracks, dict):
        blockers.append("remaining_runtime_fallback_tracks must be a mapping")
    else:
        product_specific = _as_strings(remaining_tracks.get("product_specific_migration_required"))
        if product_specific != EXPECTED_REMAINING_RUNTIME_TRACKS:
            blockers.append(
                "remaining_runtime_fallback_tracks.product_specific_migration_required "
                f"must be exactly {sorted(EXPECTED_REMAINING_RUNTIME_TRACKS)}"
            )
        if remaining_tracks.get("handling_rule") != "migrate only when the related product capability is actively being developed":
            blockers.append("remaining_runtime_fallback_tracks.handling_rule must preserve product-specific migration rule")

    gates = _as_strings(state.get("active_safety_gates"))
    if gates != EXPECTED_SAFETY_GATES:
        blockers.append(f"active_safety_gates must be exactly {sorted(EXPECTED_SAFETY_GATES)}")
    boundaries = _as_strings(state.get("protected_runtime_boundaries"))
    if boundaries != EXPECTED_RUNTIME_BOUNDARIES:
        blockers.append(f"protected_runtime_boundaries must be exactly {sorted(EXPECTED_RUNTIME_BOUNDARIES)}")
    candidates = state.get("next_cleanup_candidates")
    if candidates != EXPECTED_NEXT_CANDIDATES:
        blockers.append(f"next_cleanup_candidates must be exactly {EXPECTED_NEXT_CANDIDATES}")


def _validate_stop_conditions(stop: dict[str, Any], blockers: list[str]) -> None:
    stop_conditions = stop.get("high_risk_stop_conditions")
    if not isinstance(stop_conditions, list):
        blockers.append("autonomous_stop_conditions.high_risk_stop_conditions must be a list")
        return
    stop_ids = {str(item.get("id")) for item in stop_conditions if isinstance(item, dict)}
    missing_stop_ids = sorted(STOP_IDS - stop_ids)
    if missing_stop_ids:
        blockers.append(f"autonomous_stop_conditions missing stop ids: {missing_stop_ids}")


def _validate_changed_files(changed: set[str], blockers: list[str]) -> None:
    runtime_changed = [
        path
        for path in sorted(changed)
        if path not in RUNTIME_FALLBACK_ALLOWED_EXACT
        and path not in STARTUP_CLOSEOUT_ALLOWED_EXACT
        and not (path.startswith(DELETED_LEGACY_PACKAGE_PREFIX) and not (ROOT / path).exists())
        and not (path.startswith(DELETED_PRODUCTION_COMPAT_PREFIX) and not (ROOT / path).exists())
        and (
            path in PROTECTED_EXACT
            or any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES)
            or not (path in GOVERNANCE_ALLOWED_EXACT or path.startswith(GOVERNANCE_ALLOWED_PREFIXES))
        )
    ]
    if runtime_changed:
        blockers.append(f"governance compaction must not touch runtime/protected files: {runtime_changed}")


def build_report() -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    details: dict[str, Any] = {}

    for path in (DOC, STATE, STOP, MANIFEST, BACKLOG):
        if not path.exists():
            blockers.append(f"missing required file: {path.relative_to(ROOT)}")
    if blockers:
        return {"overall": "FAIL", "ok": False, "blockers": blockers, "warnings": warnings, "details": details}

    state = load_yaml(STATE)
    stop = load_yaml(STOP)
    changed = _changed_files()
    _validate_current_state(state, blockers)
    _validate_stop_conditions(stop, blockers)
    _validate_changed_files(changed, blockers)

    details["state"] = {
        "current_phase": state.get("current_phase"),
        "last_merged_pr": state.get("last_merged_pr"),
        "recommended_next_pr": state.get("recommended_next_pr"),
    }
    details["active_safety_gates"] = sorted(_as_strings(state.get("active_safety_gates")))
    details["protected_runtime_boundaries"] = sorted(_as_strings(state.get("protected_runtime_boundaries")))
    details["next_cleanup_candidates"] = state.get("next_cleanup_candidates")
    details["changed_files"] = sorted(changed)
    return {"overall": "PASS" if not blockers else "FAIL", "ok": not blockers, "blockers": blockers, "warnings": warnings, "details": details}


def _write_outputs(report: dict[str, Any], output_json: str | None, output_md: str | None) -> None:
    if output_json:
        Path(output_json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if output_md:
        lines = [
            "# Autonomous Development Loop Check",
            "",
            f"- overall: {report['overall']}",
            f"- ok: {str(report['ok']).lower()}",
            "",
            "## Blockers",
            *(f"- {item}" for item in report["blockers"]),
        ]
        Path(output_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args()
    report = build_report()
    _write_outputs(report, args.output_json, args.output_md)
    print(json.dumps({"overall": report["overall"], "ok": report["ok"], "blockers": report["blockers"]}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
