from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CALLBACK_MODULES = [
    ROOT / "aicrm_next" / "channel_entry" / "api.py",
    ROOT / "aicrm_next" / "channel_entry" / "callback_ingress.py",
    ROOT / "aicrm_next" / "channel_entry" / "callback_processor.py",
    ROOT / "aicrm_next" / "channel_entry" / "callback_worker.py",
    ROOT / "aicrm_next" / "channel_entry" / "inbox.py",
    ROOT / "aicrm_next" / "channel_entry" / "ingress_app.py",
]
APPLICATION = ROOT / "aicrm_next" / "channel_entry" / "application.py"
WELCOME_EFFECT_REPOSITORY = ROOT / "aicrm_next" / "channel_entry" / "welcome_media_effects_repository.py"
REALTIME = ROOT / "aicrm_next" / "platform_foundation" / "external_effects" / "realtime.py"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = "." * int(node.level or 0) + str(node.module or "")
            modules.add(module)
    return modules


def _function_calls(path: Path, function_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) or node.name != function_name:
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                if isinstance(child.func, ast.Name):
                    calls.add(child.func.id)
                elif isinstance(child.func, ast.Attribute):
                    calls.add(child.func.attr)
    return calls


def test_callback_ingress_modules_do_not_import_real_external_adapters_or_network_clients() -> None:
    forbidden_exact = {
        "requests",
        "httpx",
        "urllib",
        "urllib.request",
        ".wecom_adapter",
        "aicrm_next.integration_gateway",
        "aicrm_next.platform_foundation.external_effects.adapters",
    }
    for module_path in CALLBACK_MODULES:
        imports = _imports(module_path)
        assert not (imports & forbidden_exact), f"{module_path} imports forbidden callback-path dependency: {imports & forbidden_exact}"
        assert not any(item.startswith("aicrm_next.integration_gateway.") for item in imports)


def test_callback_http_handler_only_ingests_and_never_runs_business_processor_inline() -> None:
    calls = _function_calls(ROOT / "aicrm_next" / "channel_entry" / "api.py", "_handle_callback")

    assert "ingest_wecom_external_contact_callback" in calls
    assert "process_wecom_external_contact_event" not in calls
    assert "process_channel_entry" not in calls
    assert "ExternalEffectService" not in calls


def test_channel_entry_real_wecom_actions_are_planned_as_external_effect_jobs() -> None:
    application_source = APPLICATION.read_text(encoding="utf-8")
    welcome_repository_source = WELCOME_EFFECT_REPOSITORY.read_text(encoding="utf-8")
    source = application_source + welcome_repository_source

    assert "build_welcome_effect_graph_repository().plan" in application_source
    assert "ExternalEffectService" in welcome_repository_source
    assert 'adapter_name="wecom_welcome_message"' in welcome_repository_source
    assert 'adapter_name="wecom_tag"' in application_source
    assert '"real_external_call_executed": False' in application_source
    assert "send_welcome_msg(" not in source
    assert "mark_external_contact_tags(" not in source
    assert "update_external_contact_remark(" not in source


def test_external_effect_realtime_wakeup_is_signal_only() -> None:
    source = REALTIME.read_text(encoding="utf-8")
    application_source = APPLICATION.read_text(encoding="utf-8")

    assert "REALTIME_ENABLED_KEY" in source
    assert "REALTIME_ALLOWED_TYPES_KEY" in source
    assert "ThreadPoolExecutor" not in source
    assert "_EXECUTOR.submit" not in source
    assert "ExternalEffectWorker(" not in source
    assert "dispatch_one(" not in source
    assert '"provider_dispatch_allowed": False' in source
    assert '"signal_transport": "transactional_queue_trigger"' in source
    assert "_wake_welcome_external_effect_job" not in application_source
    assert "wake_external_effect_job" in application_source
