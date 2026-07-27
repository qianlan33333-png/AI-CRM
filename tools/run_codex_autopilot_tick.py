#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "docs/development/phase_execution_state.yaml"
STOP = ROOT / "docs/development/autonomous_stop_conditions.yaml"
PROMPT_DEFAULT = Path("/tmp/aicrm_codex_next_prompt.md")
OWNER_DECISION_DEFAULT = Path("/tmp/aicrm_codex_owner_decision_package.md")
LOG_DIR_DEFAULT = ROOT / "logs/codex-autopilot"

REQUIRED_PREFLIGHT_DOCS = [
    "docs/development/autonomous_development_loop.md",
    "docs/development/phase_execution_state.yaml",
    "docs/development/autonomous_stop_conditions.yaml",
    "docs/development/ai_crm_next_architecture_skill.md",
    "skills/ai-crm-next-architecture/SKILL.md",
    "docs/development/codex_autopilot_runtime_runbook.md",
]
STOP_TERM_EXEMPT_WORK_PACKAGES = {
    "remaining stale non-runtime docs/reports",
    "remaining stale checker/test references",
    "governance config compaction",
    "non_runtime_cleanup",
    "runtime_fallback_cleanup",
    "future_runtime_migration",
}
OWNER_DECISION_LABELS = {"owner-decision-required", "automerge-blocked"}
AUTOPILOT_SAFE_LABEL = "autopilot-safe"


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


def run_command(args: list[str], timeout: int = 60) -> tuple[int, str, str]:
    proc = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def changed_files() -> set[str]:
    changed: set[str] = set()
    for args in (
        ["git", "diff", "--name-only", "origin/main...HEAD"],
        ["git", "diff", "--name-only"],
        ["git", "diff", "--name-only", "--cached"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        code, stdout, _ = run_command(args)
        if code == 0:
            changed.update(line.strip() for line in stdout.splitlines() if line.strip())
    return changed


def stop_terms(stop: dict[str, Any]) -> set[str]:
    terms: set[str] = set()
    for item in stop.get("high_risk_stop_conditions", []):
        if isinstance(item, dict):
            terms.update(str(term).lower() for term in item.get("terms", []))
    return terms


def text_hits_stop_condition(text: str, terms: set[str]) -> list[str]:
    lowered = text.lower()
    return sorted(term for term in terms if term and term in lowered)


def diff_hits_stop_condition(paths: set[str], terms: set[str]) -> list[str]:
    hits: list[str] = []
    policy_paths = {
        "README.md",
        "docs/development/autonomous_development_loop.md",
        "docs/development/autonomous_stop_conditions.yaml",
        "docs/development/phase_execution_state.yaml",
        "docs/development/codex_autopilot_runtime_runbook.md",
        "docs/development/ai_crm_next_architecture_skill.md",
        "docs/development/codex_task_template.md",
        "docs/claude_code_integration/README.md",
        "docs/claude_code_integration/patterns.md",
        "docs/claude_code_integration/rules.md",
        "docs/claude_code_integration/tools.md",
        "docs/claude_code_integration/troubleshooting.md",
        "docs/mcp_usage.md",
        "aicrm_next/automation_engine/channels_api.py",
        "aicrm_next/extensions/ai/ai_audience_ops/agent_copywriting.py",
        "aicrm_next/extensions/ai/ai_audience_ops/agent_gateway.py",
        "scripts/codex_autopilot_tick.sh",
        "tools/check_autonomous_development_loop.py",
        "tools/check_automerge_eligibility.py",
        "tools/run_codex_autopilot_tick.py",
    }
    for path in sorted(paths):
        if (
            path in policy_paths
            or path.startswith("tests/")
            or path.startswith("docs/development/")
            or path.startswith("docs/architecture/")
            or path.startswith("tools/check_")
            or path.startswith("tools/generate_")
        ):
            continue
        full = ROOT / path
        if not full.exists() or not full.is_file():
            continue
        matched = text_hits_stop_condition(full.read_text(encoding="utf-8", errors="ignore"), terms)
        hits.extend(f"{path}: {term}" for term in matched)
    return hits


def fetch_open_autopilot_prs(skip_github: bool) -> tuple[list[dict[str, Any]], list[str]]:
    if skip_github:
        return [], ["github inspection skipped"]
    code, stdout, stderr = run_command(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--json",
            "number,title,headRefName,labels,statusCheckRollup,url",
            "--limit",
            "50",
        ],
        timeout=45,
    )
    if code != 0:
        return [], [f"gh pr list unavailable: {(stderr or stdout).strip()}"]
    try:
        prs = json.loads(stdout)
    except json.JSONDecodeError:
        return [], ["gh pr list returned invalid JSON"]
    autopilot = [
        pr
        for pr in prs
        if "autopilot" in str(pr.get("headRefName", "")).lower()
        or "autopilot" in str(pr.get("title", "")).lower()
        or any("autopilot" in str(label.get("name", "")).lower() for label in pr.get("labels", []))
    ]
    return autopilot, []


def classify_open_pr(pr: dict[str, Any]) -> dict[str, Any]:
    labels = {str(label.get("name", "")).lower() for label in pr.get("labels", [])}
    checks = pr.get("statusCheckRollup", [])
    pending = [item.get("name") for item in checks if item.get("status") != "COMPLETED"]
    failed = [
        item.get("name")
        for item in checks
        if item.get("status") == "COMPLETED" and item.get("conclusion") not in {"SUCCESS", "SKIPPED", "NEUTRAL"}
    ]
    passed = [item.get("name") for item in checks if item.get("status") == "COMPLETED" and item.get("conclusion") == "SUCCESS"]
    return {
        "number": pr.get("number"),
        "url": pr.get("url"),
        "labels": sorted(labels),
        "pending": pending,
        "failed": failed,
        "passed": passed,
        "owner_decision_label": bool(labels & OWNER_DECISION_LABELS),
        "autopilot_safe": AUTOPILOT_SAFE_LABEL in labels,
        "checks_green": bool(checks) and not pending and not failed,
    }


def admin_merge_pr(pr_number: int | str | None) -> tuple[bool, str]:
    if not pr_number:
        return False, "missing PR number"
    code, stdout, stderr = run_command(
        ["gh", "pr", "merge", str(pr_number), "--admin", "--merge", "--delete-branch"],
        timeout=120,
    )
    if code == 0:
        return True, (stdout or "").strip()
    return False, (stderr or stdout or "").strip()


def choose_next_action(state: dict[str, Any], requested: str | None = None) -> str:
    return choose_next_work_package(state, requested)


def choose_next_work_package(state: dict[str, Any], requested: str | None = None) -> str:
    raw_candidates = state.get("next_cleanup_candidates", [])
    allowed = list(raw_candidates) if isinstance(raw_candidates, dict) else [str(item) for item in raw_candidates]
    if requested:
        if requested not in allowed:
            raise ValueError(f"requested work package is not in next_cleanup_candidates: {requested}")
        return requested
    if not allowed:
        raise ValueError("phase_execution_state has no next_cleanup_candidates")
    recommended = str(state.get("recommended_next_pr", "")).strip()
    if recommended in {
        "runtime_fallback_migration_channels_track1",
        "runtime_fallback_migration_media_material_libraries_track2",
    } and "runtime_fallback_cleanup" in allowed:
        return "runtime_fallback_cleanup"
    if recommended == "product_development_or_targeted_runtime_migration" and "future_runtime_migration" in allowed:
        return "future_runtime_migration"
    if recommended == "final_non_runtime_cleanup_closeout_wave17" and "non_runtime_cleanup" in allowed:
        return "non_runtime_cleanup"
    if recommended == "stale_product_design_documentation_cleanup_wave16" and "remaining stale non-runtime docs/reports" in allowed:
        return "remaining stale non-runtime docs/reports"
    if recommended == "architecture_documentation_compaction_wave15" and "governance config compaction" in allowed:
        return "governance config compaction"
    if recommended == "runtime_switch_archaeology_cleanup_wave14" and "remaining stale non-runtime docs/reports" in allowed:
        return "remaining stale non-runtime docs/reports"
    if recommended == "legacy_retirement_package_cleanup_wave13" and "remaining stale checker/test references" in allowed:
        return "remaining stale checker/test references"
    if recommended == "residual_narrative_documentation_cleanup_wave12" and "remaining stale non-runtime docs/reports" in allowed:
        return "remaining stale non-runtime docs/reports"
    return allowed[0]


def owner_decision_package(reason: str, work_package: str | None, output_path: Path) -> None:
    lines = [
        "# AI-CRM Codex Autopilot Owner Decision Package",
        "",
        f"- reason: {reason}",
        f"- selected_work_package: {work_package or 'none'}",
        "- auto_merge_allowed: false",
        "- admin_merge_allowed: false",
        "- production_owner_switch_allowed: false",
        "- production_write_allowed: false",
        "- fallback_removal_allowed: false",
        "- real_external_call_allowed: false",
        "",
        "## Owner Decision Needed",
        "",
        "Codex autopilot detected a stop condition or blocked state. The next step requires explicit owner review before another implementation PR may be generated or merged.",
    ]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prompt_for_work_package(work_package: str, state: dict[str, Any], output_path: Path) -> None:
    docs = "\n".join(f"- {path}" for path in REQUIRED_PREFLIGHT_DOCS)
    prompt = f"""# AI-CRM Codex Autopilot Next Prompt

You are working in qianlan333/AI-CRM from latest main.

## Required preflight

Read and follow:
{docs}

## Selected post-closeout work package

- work_package: {work_package}
- current_phase: {state.get("current_phase")}
- last_merged_pr: {state.get("last_merged_pr")}
- recommended_next_pr: {state.get("recommended_next_pr")}

## Hard boundaries

- Only choose from docs/development/phase_execution_state.yaml next_cleanup_candidates.
- Business behavior must remain unchanged.
- The global deletion task is closed.
- Migrate fallback only as part of product-specific development with owner-approved replacement and rollback evidence.
- Do not write production.
- Do not remove high-risk fallback.
- Do not modify aicrm_next/main.py, business route implementations, schema/migrations, deploy/nginx/systemd, or deleted legacy package boundaries.
- Do not enable real external calls, payment, OAuth, WeCom callback, timer, automation execution, or outbound send.
- If any stop condition from docs/development/autonomous_stop_conditions.yaml appears, stop and generate an owner decision package only. Do not auto-merge.

## Required implementation behavior

- Advance only one product-specific package or stop with an owner decision package when the selected action touches high-risk runtime behavior.
- Do not start broad cleanup or global deletion waves.
- Update docs/development/phase_execution_state.yaml only when the active compact state changes.
- Keep Business value, Business continuity, Risk / rollback, and Next action in the PR body.
- Run:
  - python3 tools/check_autonomous_development_loop.py --output-md /tmp/autonomous_development_loop.md --output-json /tmp/autonomous_development_loop.json
  - python3 tools/check_automerge_eligibility.py --output-md /tmp/automerge_eligibility.md --output-json /tmp/automerge_eligibility.json
  - git diff --check

## Auto-merge boundary

Low-risk merge is allowed only when eligibility is true, GitHub required checks are green, no stop condition exists, and runtime behavior is unchanged. Owner decision packages must not auto-merge.
"""
    output_path.write_text(prompt, encoding="utf-8")


def build_tick_report(args: argparse.Namespace) -> dict[str, Any]:
    state = load_yaml(STATE)
    stop = load_yaml(STOP)
    terms = stop_terms(stop)
    details: dict[str, Any] = {
        "prompt_output": str(args.prompt_output),
        "owner_decision_output": str(args.owner_decision_output),
    }
    warnings: list[str] = []
    blockers: list[str] = []

    work_package: str | None = None
    try:
        work_package = choose_next_work_package(state, args.action)
    except ValueError as exc:
        blockers.append(str(exc))

    action_stop_hits: list[str] = []
    if work_package and work_package not in STOP_TERM_EXEMPT_WORK_PACKAGES:
        action_stop_hits = text_hits_stop_condition(str(work_package).replace("_", " "), terms)
    if action_stop_hits:
        blockers.append(f"selected work package touches stop condition: {action_stop_hits}")

    diff_stop_hits = diff_hits_stop_condition(changed_files(), terms)
    if diff_stop_hits:
        blockers.append(f"current diff touches stop condition: {diff_stop_hits}")

    open_prs, pr_warnings = fetch_open_autopilot_prs(args.skip_github)
    warnings.extend(pr_warnings)
    pr_classifications = [classify_open_pr(pr) for pr in open_prs]
    details["open_autopilot_prs"] = pr_classifications
    for pr in pr_classifications:
        if pr["owner_decision_label"]:
            blockers.append(f"open autopilot PR has owner-decision/automerge-blocked label: #{pr['number']}")
        elif pr["pending"]:
            blockers.append(f"open autopilot PR checks pending: #{pr['number']}")
        elif pr["failed"]:
            repair_marker = LOG_DIR_DEFAULT / f"repair-attempt-pr-{pr['number']}.json"
            if repair_marker.exists():
                blockers.append(f"open autopilot PR checks failed and bounded repair already attempted: #{pr['number']}")
            else:
                details["bounded_repair_allowed_for_pr"] = pr["number"]
                repair_marker.parent.mkdir(parents=True, exist_ok=True)
                repair_marker.write_text(json.dumps({"pr": pr["number"], "at": int(time.time())}) + "\n", encoding="utf-8")
                blockers.append(f"open autopilot PR checks failed; bounded repair prompt required before new action: #{pr['number']}")
        elif pr["checks_green"] and pr["autopilot_safe"]:
            merged, merge_detail = admin_merge_pr(pr["number"])
            details["admin_merge_attempt"] = {"pr": pr["number"], "merged": merged, "detail": merge_detail}
            if merged:
                return {
                    "ok": True,
                    "result_status": "open_autopilot_pr_admin_merged",
                    "prompt_generated": False,
                    "selected_work_package": work_package,
                    "selected_action": work_package,
                    "auto_merge_allowed": True,
                    "admin_merge_allowed": True,
                    "blockers": [],
                    "warnings": warnings,
                    "details": details,
                }
            blockers.append(f"open autopilot PR admin merge failed: #{pr['number']}: {merge_detail}")
        elif pr["checks_green"] and not pr["autopilot_safe"]:
            blockers.append(f"open autopilot PR checks passed but autopilot-safe label is missing: #{pr['number']}")
        else:
            blockers.append(f"open autopilot PR exists; wait for merge or owner decision: #{pr['number']}")

    if blockers:
        args.owner_decision_output.parent.mkdir(parents=True, exist_ok=True)
        owner_decision_package("; ".join(blockers), work_package, args.owner_decision_output)
        return {
            "ok": True,
            "result_status": "owner_decision_required",
            "prompt_generated": False,
            "owner_decision_package": str(args.owner_decision_output),
            "selected_work_package": work_package,
            "selected_action": work_package,
            "auto_merge_allowed": False,
            "admin_merge_allowed": False,
            "blockers": blockers,
            "warnings": warnings,
            "details": details,
        }

    args.prompt_output.parent.mkdir(parents=True, exist_ok=True)
    prompt_for_work_package(work_package or "", state, args.prompt_output)
    return {
        "ok": True,
        "result_status": "next_prompt_generated",
        "prompt_generated": True,
        "prompt_path": str(args.prompt_output),
        "selected_work_package": work_package,
        "selected_action": work_package,
        "auto_merge_allowed": False,
        "admin_merge_allowed": False,
        "blockers": [],
        "warnings": warnings,
        "details": details,
    }


def write_outputs(report: dict[str, Any], output_json: str | None, output_md: str | None) -> None:
    if output_json:
        Path(output_json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if output_md:
        lines = [
            "# Codex Autopilot Tick Report",
            "",
            f"- result_status: {report['result_status']}",
            f"- prompt_generated: {str(report.get('prompt_generated', False)).lower()}",
            f"- selected_work_package: {report.get('selected_work_package', report.get('selected_action'))}",
            f"- auto_merge_allowed: {str(report.get('auto_merge_allowed', False)).lower()}",
            "",
            "## Blockers",
            *(f"- {item}" for item in report.get("blockers", [])),
            "",
            "## Warnings",
            *(f"- {item}" for item in report.get("warnings", [])),
        ]
        Path(output_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action")
    parser.add_argument("--prompt-output", type=Path, default=PROMPT_DEFAULT)
    parser.add_argument("--owner-decision-output", type=Path, default=OWNER_DECISION_DEFAULT)
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument("--lock-file", type=Path, default=LOG_DIR_DEFAULT / "tick.lock")
    parser.add_argument("--skip-github", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    args.lock_file.parent.mkdir(parents=True, exist_ok=True)
    with args.lock_file.open("w", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            report = {
                "ok": True,
                "result_status": "already_running",
                "prompt_generated": False,
                "auto_merge_allowed": False,
                "admin_merge_allowed": False,
                "blockers": ["single-flight lock is held"],
                "warnings": [],
                "details": {"lock_file": str(args.lock_file)},
            }
            write_outputs(report, args.output_json, args.output_md)
            print(json.dumps(report, ensure_ascii=False))
            return 0
        report = build_tick_report(args)
        write_outputs(report, args.output_json, args.output_md)
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
