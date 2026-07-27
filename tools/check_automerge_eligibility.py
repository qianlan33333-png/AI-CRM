#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PR_BODY = ROOT / "docs/development/autonomous_development_loop.md"
STOP = ROOT / "docs/development/autonomous_stop_conditions.yaml"

REQUIRED_PR_BODY_SECTIONS = {
    "Business value",
    "Business continuity",
    "Risk / rollback",
    "Next action",
}
LOW_RISK_PREFIXES = (
    "docs/architecture/",
    "docs/development/",
    "tools/check_",
    "tools/generate_",
    "tests/test_",
)
DELETED_LOW_RISK_PREFIXES = (
    "docs/",
    "tools/check_",
    "tools/compare_",
    "tools/run_",
    "tests/test_",
)
DELETED_LOW_RISK_SUFFIXES = (
    "_gray_smoke.py",
)
DELETED_LOW_RISK_EXACT = {
    "wecom_ability" + "_service/LEGACY_FROZEN.md",
    "wecom_ability" + "_service/__init__.py",
}
DELETED_PRODUCTION_COMPAT_PREFIX = "aicrm_next/production_compat/"
RUNTIME_FALLBACK_ALLOWED_EXACT: set[str] = set()
LOW_RISK_EXACT = {
    "README.md",
    "requirements.txt",
    "aicrm_next/automation/automation_engine/channels_api.py",
    "aicrm_next/extensions/ai/ai_audience_ops/agent_copywriting.py",
    "aicrm_next/extensions/ai/ai_audience_ops/agent_gateway.py",
    "scripts/run_lint.py",
    "tests/test_ai_audience_agent_copywriting.py",
    "aicrm_next/crm/customer_read_model/api.py",
    "aicrm_next/automation/automation_engine/api.py",
    "docs/claude_code_integration/README.md",
    "docs/claude_code_integration/patterns.md",
    "docs/claude_code_integration/rules.md",
    "docs/claude_code_integration/tools.md",
    "docs/claude_code_integration/troubleshooting.md",
    "docs/mcp_usage.md",
    "tools/collect_server_readonly_evidence.py",
    "tools/run_codex_autopilot_tick.py",
    "scripts/codex_autopilot_tick.sh",
}
AUTOPILOT_DELIVERABLE_RUNTIME_PATHS = {
    "aicrm_next/automation/automation_engine/api.py",
    "aicrm_next/automation/automation_engine/application.py",
    "aicrm_next/automation/automation_engine/dto.py",
    "aicrm_next/automation/automation_engine/repo.py",
    "aicrm_next/automation/automation_engine/agent_sqlalchemy_repository.py",
    "aicrm_next/automation/automation_engine/agent_output_sqlalchemy_repository.py",
    "aicrm_next/automation/automation_engine/agent_run_sqlalchemy_repository.py",
    "aicrm_next/automation/automation_engine/agents.py",
    "aicrm_next/automation/automation_engine/agent_outputs.py",
    "aicrm_next/automation/automation_engine/agent_runs.py",
    "aicrm_next/automation/automation_engine/task_groups.py",
    "aicrm_next/automation/automation_engine/tasks.py",
    "aicrm_next/automation/automation_engine/workflows.py",
    "aicrm_next/automation/automation_engine/workflow_nodes.py",
    "aicrm_next/automation/automation_engine/group_ops/domain.py",
    "aicrm_next/channels/integration_gateway/legacy_flask_facade.py",
    "aicrm_next/channels/integration_gateway/wecom_group_adapter.py",
    "aicrm_next/crm/customer_tags/api.py",
    "aicrm_next/crm/customer_tags/dto.py",
    "aicrm_next/crm/customer_tags/wecom_tag_live_adapter.py",
    "aicrm_next/channels/integration_gateway/wecom_tag_live_gateway.py",
    "aicrm_next/channels/integration_gateway/wecom_contact_callback_adapter.py",
    "aicrm_next/channels/integration_gateway/wecom_contact_callback_application.py",
    "aicrm_next/channels/integration_gateway/wecom_contact_callback_contract.py",
    "aicrm_next/channels/integration_gateway/wecom_contact_callback_live_adapter.py",
    "aicrm_next/channels/integration_gateway/wecom_contact_callback_live_gateway.py",
    "aicrm_next/channels/integration_gateway/oauth_identity_adapter.py",
    "aicrm_next/channels/integration_gateway/oauth_identity_application.py",
    "aicrm_next/channels/integration_gateway/oauth_identity_contract.py",
    "aicrm_next/channels/integration_gateway/oauth_identity_live_adapter.py",
    "aicrm_next/channels/integration_gateway/oauth_identity_live_gateway.py",
    "aicrm_next/channels/integration_gateway/media_live_adapter.py",
    "aicrm_next/channels/integration_gateway/media_live_gateway.py",
    "aicrm_next/channels/integration_gateway/payment_commerce_live_adapter.py",
    "aicrm_next/channels/integration_gateway/payment_commerce_live_gateway.py",
    "aicrm_next/channels/integration_gateway/openclaw_mcp_ai_assist_live_adapter.py",
    "aicrm_next/channels/integration_gateway/openclaw_mcp_ai_assist_live_gateway.py",
    "aicrm_next/extensions/forms/questionnaire/external_submit_adapter.py",
    "aicrm_next/extensions/forms/questionnaire/external_submit_live_adapter.py",
    "aicrm_next/extensions/forms/questionnaire/external_submit_live_gateway.py",
}
OWNER_DECISION_PACKAGE_PATHS = {
}
POLICY_FILES_CAN_DEFINE_STOP_TERMS = {
        "docs/architecture/hxc_dashboard_route_inventory.md",
        "docs/development/autonomous_development_loop.md",
        "docs/development/codex_autopilot_runtime_runbook.md",
        "docs/development/ai_crm_next_architecture_skill.md",
        "docs/development/codex_task_template.md",
        "docs/development/phase_execution_state.yaml",
        "docs/development/autonomous_stop_conditions.yaml",
        "aicrm_next/extensions/ai/ai_audience_ops/agent_copywriting.py",
        "aicrm_next/extensions/ai/ai_audience_ops/agent_gateway.py",
        "aicrm_next/channels/integration_gateway/legacy_flask_facade.py",
        "scripts/codex_autopilot_tick.sh",
        "tools/check_autonomous_development_loop.py",
        "tools/check_automerge_eligibility.py",
        "tools/collect_server_readonly_evidence.py",
        "tools/run_codex_autopilot_tick.py",
    }
PROTECTED_EXACT = {
    "aicrm_next/main.py",
    "app.py",
}
PROTECTED_PREFIXES = (
    "aicrm_next/production_compat/",
    "deploy/",
    "systemd/",
    "nginx/",
)
MIGRATION_PREFIXES = (
    "migrations/",
)
DESTRUCTIVE_MIGRATION_PATTERNS = (
    r"\bdrop\s+table\b",
    r"\bdrop\s+column\b",
    r"\balter\s+table\b.*\bdrop\b",
    r"\btruncate\b",
    r"\bdelete\s+from\b",
    r"\brename\s+(table|column)\b",
)
UNAUTHORIZED_CLAIM_PATTERNS = (
    r"\bproduction_ready\b",
    r"\bdelete_ready\s*[:=]\s*true\b",
    r"\bdelete_ready\s+true\b",
    r"\bcanary_approved\b",
    r"\bcanary approved\b",
    r"\broute_switch_ready\s*[:=]\s*true\b",
)
STOP_CONDITION_PATTERNS = (
    r"\bproduction owner switch\b",
    r"\broute ownership switch\b",
    r"\bfallback removal\b",
    r"\bremove legacy fallback\b",
    r"\bproduction write\b",
    r"\breal external call\b",
    r"\bwecom external\b",
    r"\brun-due\b",
    r"\bautomation execution\b",
    r"\boutbound send\b",
    r"\bdeploy config\b",
    r"\bdestructive migration\b",
)


def _run_git(args: list[str]) -> tuple[bool, str, str]:
    proc = subprocess.run(["git", *args], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return proc.returncode == 0, proc.stdout, proc.stderr


def _changed_files(base_ref: str, head_ref: str) -> tuple[set[str], list[str]]:
    changed: set[str] = set()
    warnings: list[str] = []
    for args in (
        ["diff", "--name-only", f"{base_ref}...{head_ref}"],
        ["diff", "--name-only"],
        ["diff", "--name-only", "--cached"],
    ):
        ok, stdout, stderr = _run_git(args)
        if ok:
            changed.update(line.strip() for line in stdout.splitlines() if line.strip())
        else:
            warnings.append(f"git {' '.join(args)} unavailable: {(stderr or stdout).strip()}")
    ok, stdout, stderr = _run_git(["ls-files", "--others", "--exclude-standard"])
    if ok:
        changed.update(line.strip() for line in stdout.splitlines() if line.strip())
    else:
        warnings.append(f"git ls-files --others unavailable: {(stderr or stdout).strip()}")
    return changed, warnings


def _file_text(path: str, base_ref: str) -> str:
    full_path = ROOT / path
    if full_path.exists():
        return full_path.read_text(encoding="utf-8", errors="ignore")
    ok, stdout, _ = _run_git(["show", f"{base_ref}:{path}"])
    return stdout if ok else ""


def _diff_text(paths: set[str], base_ref: str, head_ref: str) -> str:
    ok, stdout, _ = _run_git(["diff", f"{base_ref}...{head_ref}", "--", *sorted(paths)])
    parts = [stdout] if ok else []
    for path in sorted(paths):
        if (ROOT / path).exists() and path not in stdout:
            parts.append(f"\n--- {path}\n{_file_text(path, base_ref)}")
    return "\n".join(parts)


def _is_deleted_path(path: str) -> bool:
    return not (ROOT / path).exists()


def _is_low_risk_path(path: str) -> bool:
    if path in RUNTIME_FALLBACK_ALLOWED_EXACT:
        return True
    if _is_deleted_path(path) and (
        path in DELETED_LOW_RISK_EXACT
        or path.startswith(DELETED_LOW_RISK_PREFIXES)
        or path.endswith(DELETED_LOW_RISK_SUFFIXES)
    ):
        return True
    return (
        path in LOW_RISK_EXACT
        or path in OWNER_DECISION_PACKAGE_PATHS
        or path in AUTOPILOT_DELIVERABLE_RUNTIME_PATHS
        or path.startswith(LOW_RISK_PREFIXES)
    )


def _has_owner_approval(path: str | None) -> bool:
    if not path:
        return False
    approval = Path(path)
    if not approval.is_absolute():
        approval = ROOT / approval
    return approval.exists() and approval.read_text(encoding="utf-8", errors="ignore").strip() != ""


def _protected_path_reason(path: str) -> str | None:
    if path in RUNTIME_FALLBACK_ALLOWED_EXACT:
        return None
    if path.startswith(DELETED_PRODUCTION_COMPAT_PREFIX) and not (ROOT / path).exists():
        return None
    if path in PROTECTED_EXACT:
        return f"protected exact path: {path}"
    if any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES):
        return f"protected runtime/deploy path: {path}"
    return None


def _destructive_migration_reason(path: str, text: str) -> str | None:
    if not any(path.startswith(prefix) for prefix in MIGRATION_PREFIXES):
        return None
    lowered = text.lower()
    for pattern in DESTRUCTIVE_MIGRATION_PATTERNS:
        if re.search(pattern, lowered, flags=re.DOTALL):
            return f"destructive migration pattern in {path}: {pattern}"
    return None


def _pr_body_blockers(pr_body_file: Path) -> list[str]:
    if not pr_body_file.exists():
        return [f"PR body file missing: {pr_body_file}"]
    text = pr_body_file.read_text(encoding="utf-8", errors="ignore")
    blockers = [f"PR body missing required section: {section}" for section in sorted(REQUIRED_PR_BODY_SECTIONS) if section not in text]
    lowered = text.lower()
    for pattern in UNAUTHORIZED_CLAIM_PATTERNS:
        if re.search(pattern, lowered):
            blockers.append(f"PR body contains unauthorized readiness claim: {pattern}")
    return blockers


def build_report(
    base_ref: str = "origin/main",
    head_ref: str = "HEAD",
    pr_body_file: str | None = None,
    owner_approval_file: str | None = None,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    manual_merge_required: list[str] = []

    changed, git_warnings = _changed_files(base_ref, head_ref)
    warnings.extend(git_warnings)
    pr_body_path = Path(pr_body_file) if pr_body_file else DEFAULT_PR_BODY
    if not pr_body_path.is_absolute():
        pr_body_path = ROOT / pr_body_path
    blockers.extend(_pr_body_blockers(pr_body_path))

    owner_approval_present = _has_owner_approval(owner_approval_file)
    if not changed:
        blockers.append("no changed files detected")

    non_low_risk = sorted(path for path in changed if not _is_low_risk_path(path))
    if non_low_risk:
        blockers.append(f"auto-merge eligibility only allows low-risk docs/tools/tests paths: {non_low_risk}")

    protected_hits: list[str] = []
    destructive_hits: list[str] = []
    stop_hits: list[str] = []
    claim_hits: list[str] = []
    for path in sorted(changed):
        text = _file_text(path, base_ref)
        reason = _protected_path_reason(path)
        if reason:
            protected_hits.append(reason)
        destructive_reason = _destructive_migration_reason(path, text)
        if destructive_reason:
            destructive_hits.append(destructive_reason)
        if _is_deleted_path(path):
            continue
        lowered = text.lower()
        if path not in POLICY_FILES_CAN_DEFINE_STOP_TERMS and path not in RUNTIME_FALLBACK_ALLOWED_EXACT and not path.startswith("tests/"):
            for pattern in STOP_CONDITION_PATTERNS:
                if re.search(pattern, lowered):
                    stop_hits.append(f"{path}: {pattern}")
        for pattern in UNAUTHORIZED_CLAIM_PATTERNS:
            if path not in POLICY_FILES_CAN_DEFINE_STOP_TERMS and re.search(pattern, lowered):
                claim_hits.append(f"{path}: {pattern}")

    if protected_hits or destructive_hits:
        if owner_approval_present:
            manual_merge_required.extend(protected_hits + destructive_hits)
        else:
            blockers.extend(protected_hits + destructive_hits)
            blockers.append("protected/high-risk diff requires explicit owner approval file")
    owner_decision_hits = sorted(path for path in changed if path in OWNER_DECISION_PACKAGE_PATHS)
    if owner_decision_hits:
        manual_merge_required.append(f"owner decision package is not auto-merge eligible: {owner_decision_hits}")
    if stop_hits:
        blockers.append(f"diff touches stop condition outside policy/checker files: {stop_hits}")
    if claim_hits:
        blockers.append(f"diff contains unauthorized readiness claim: {claim_hits}")

    if not STOP.exists():
        blockers.append("autonomous_stop_conditions.yaml missing")

    eligible = not blockers and not manual_merge_required
    return {
        "overall": "PASS" if not blockers else "FAIL",
        "ok": not blockers,
        "eligible": eligible,
        "owner_approval_present": owner_approval_present,
        "manual_merge_required": manual_merge_required,
        "blockers": blockers,
        "warnings": warnings,
        "details": {
            "base_ref": base_ref,
            "head_ref": head_ref,
            "changed_files": sorted(changed),
            "pr_body_file": str(pr_body_path.relative_to(ROOT) if pr_body_path.is_relative_to(ROOT) else pr_body_path),
        },
    }


def _write_outputs(report: dict[str, Any], output_json: str | None, output_md: str | None) -> None:
    if output_json:
        Path(output_json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if output_md:
        lines = [
            "# Auto-Merge Eligibility Check",
            "",
            f"- overall: {report['overall']}",
            f"- ok: {str(report['ok']).lower()}",
            f"- eligible: {str(report['eligible']).lower()}",
            "",
            "## Blockers",
            *(f"- {item}" for item in report["blockers"]),
            "",
            "## Manual Merge Required",
            *(f"- {item}" for item in report["manual_merge_required"]),
        ]
        Path(output_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-ref", default="origin/main")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--pr-body-file")
    parser.add_argument("--owner-approval-file")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    args = parser.parse_args()
    report = build_report(
        base_ref=args.base_ref,
        head_ref=args.head_ref,
        pr_body_file=args.pr_body_file,
        owner_approval_file=args.owner_approval_file,
    )
    _write_outputs(report, args.output_json, args.output_md)
    print(json.dumps({"overall": report["overall"], "ok": report["ok"], "eligible": report["eligible"], "blockers": report["blockers"]}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
