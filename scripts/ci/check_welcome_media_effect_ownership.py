#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _class_node(path: Path, name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise RuntimeError(f"{path}: class {name} not found")


def main() -> int:
    violations: list[str] = []
    adapter_path = ROOT / "aicrm_next/platform/platform_foundation/external_effects/adapters.py"
    adapter = _class_node(adapter_path, "WeComWelcomeMessageAdapter")
    calls = [node for node in ast.walk(adapter) if isinstance(node, ast.Call)]
    send_calls = [
        node
        for node in calls
        if isinstance(node.func, ast.Attribute) and node.func.attr == "send_welcome_msg"
    ]
    forbidden_calls = [
        node
        for node in calls
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"ensure_ready", "upload_media", "resolve_content_package_materials"}
        )
        or (isinstance(node.func, ast.Name) and "resolver" in node.func.id.lower())
    ]
    if len(send_calls) != 1:
        violations.append("WeComWelcomeMessageAdapter must contain exactly one send_welcome_msg provider call")
    if forbidden_calls:
        violations.append("WeComWelcomeMessageAdapter contains an inline material resolver/uploader call")
    adapter_source = ast.get_source_segment(adapter_path.read_text(encoding="utf-8"), adapter) or ""
    if "unresolved_material_dependency" not in adapter_source:
        violations.append("WeComWelcomeMessageAdapter must fail closed on unresolved media dependencies")

    scheduler_source = (ROOT / "aicrm_next/automation/background_jobs/automation_ops_scheduler.py").read_text(encoding="utf-8")
    if "enqueue_due_media_refreshes" in scheduler_source or "_run_media_refresh" in scheduler_source:
        violations.append("automation_ops_scheduler still owns periodic media refresh planning")
    if "retired_manual_repair_only" not in scheduler_source:
        violations.append("automation_ops_scheduler must advertise the retired media timer")

    repair_source = (ROOT / "aicrm_next/wecom_media_jobs.py").read_text(encoding="utf-8")
    if "repair_authorized" not in repair_source or "manual_repair:wecom_media_lease_backfill" not in repair_source:
        violations.append("due-media scanner is not guarded as an explicit manual repair path")

    application_source = (ROOT / "aicrm_next/channels/channel_entry/application.py").read_text(encoding="utf-8")
    if "WelcomeEffectGraphRequest" not in application_source:
        violations.append("channel-entry welcome callback does not plan a durable media dependency graph")
    for token in ("callback_received_at", "provider_deadline_at", "welcome_provider_deadline"):
        if token not in application_source:
            violations.append(f"channel-entry welcome callback is missing hard deadline token: {token}")

    graph_source = (ROOT / "aicrm_next/channels/channel_entry/welcome_media_effects_repository.py").read_text(encoding="utf-8")
    for token in ("WECOM_WELCOME_EFFECT_LANE", "max_attempts=1", "PROVIDER_DEADLINE_FIELD"):
        if token not in graph_source:
            violations.append(f"welcome effect graph is missing realtime execution token: {token}")

    inbox_source = (ROOT / "aicrm_next/channels/channel_entry/inbox.py").read_text(encoding="utf-8")
    if "welcome_ingress_lane" not in inbox_source:
        violations.append("welcome callback ingress does not select the reserved lane")

    runtime_source = (ROOT / "scripts/run_execution_runtime.py").read_text(encoding="utf-8")
    for token in ("wecom_welcome_ingress", "wecom_welcome", "critical_post_commit_hook"):
        if token not in runtime_source:
            violations.append(f"queue runtime is missing welcome realtime ownership token: {token}")

    worker_source = (ROOT / "aicrm_next/platform/platform_foundation/external_effects/worker.py").read_text(encoding="utf-8")
    if "expire_provider_deadline" not in worker_source:
        violations.append("external effect worker does not fail closed on elapsed provider deadlines")
    if worker_source.find("complete_dispatch(") > worker_source.find("_run_post_success_continuations(updated"):
        violations.append("critical welcome continuation must run only after dispatch completion is durable")

    claim_source = (ROOT / "aicrm_next/platform/platform_foundation/execution_runtime/repository.py").read_text(encoding="utf-8")
    for token in ("WECOM_WELCOME_RESERVED_LANES", "ordinary_capacity", "_active_reserved_capacity"):
        if token not in claim_source:
            violations.append(f"queue admission does not reserve welcome capacity: {token}")

    if violations:
        for violation in violations:
            print(f"VIOLATION: {violation}")
        return 1
    print("welcome media effect ownership check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
