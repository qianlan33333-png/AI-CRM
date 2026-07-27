from __future__ import annotations

import ast
import json
from pathlib import Path

from scripts import run_external_push_worker


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts/run_external_push_worker.py"


def test_retired_worker_uses_count_only_reconciliation_without_legacy_sender_imports() -> None:
    source = WORKER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.append(node.module or "")

    assert all(not module.startswith("wecom_ability" + "_service") for module in imported_modules)
    assert "from " + "wecom_ability" + "_service" not in source
    assert "import " + "wecom_ability" + "_service" not in source
    assert "create_app" not in source
    assert "app_context" not in source
    assert "aicrm_next.external_push" not in source
    assert "aicrm_next.extensions.commerce.commerce.fulfillment_reconciliation" in source
    assert "run_due_external_push_events" not in source
    assert "run_due_external_push_retries" not in source


def test_retired_worker_default_is_count_only_and_never_sends(monkeypatch, capsys) -> None:
    calls: list[str] = []

    class Reconciliation:
        def diagnose(self):
            calls.append("diagnose")
            return {"ok": True, "mode": "count_only", "counts": {"legacy_domain_outbox_pending": 2}}

    monkeypatch.setattr(run_external_push_worker, "CommerceFulfillmentReconciliationService", Reconciliation)

    assert run_external_push_worker.main([]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert calls == ["diagnose"]
    assert payload["ok"] is True
    assert payload["legacy_worker_retired"] is True
    assert payload["replacement_owner"] == "payment.succeeded:webhook_order_paid_consumer"
    assert payload["database_mutation_performed"] is False
    assert payload["consumer_executed"] is False
    assert payload["real_external_call_executed"] is False


def test_legacy_flags_remain_parse_compatible_but_cannot_restore_sending(monkeypatch, capsys) -> None:
    calls: list[str] = []

    class Reconciliation:
        def diagnose(self):
            calls.append("diagnose")
            return {"ok": True, "mode": "count_only", "counts": {}}

    monkeypatch.setattr(run_external_push_worker, "CommerceFulfillmentReconciliationService", Reconciliation)

    assert run_external_push_worker.main(["--limit", "7", "--skip-events"]) == 0
    first_payload = json.loads(capsys.readouterr().out)
    assert calls == ["diagnose"]
    assert first_payload["mode"] == "count_only"

    calls.clear()
    assert run_external_push_worker.main(["--limit", "9", "--skip-retries"]) == 0
    second_payload = json.loads(capsys.readouterr().out)
    assert calls == ["diagnose"]
    assert second_payload["real_external_call_executed"] is False


def test_retired_worker_has_no_deployable_systemd_units() -> None:
    assert not (ROOT / "deploy/openclaw-external-push-worker.service").exists()
    assert not (ROOT / "deploy/openclaw-external-push-worker.timer").exists()
