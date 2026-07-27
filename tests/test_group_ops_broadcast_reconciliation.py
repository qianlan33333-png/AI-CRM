from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from sqlalchemy import text

from aicrm_next.background_jobs import broadcast_reconciliation
from aicrm_next.background_jobs.broadcast_reconciliation import (
    GroupOpsBroadcastReconciliationService,
)
from aicrm_next.shared.db_session import get_session_factory


EXPECTED_COUNTS = {
    "stale_dispatching",
    "unknown_after_dispatch",
    "job_recipient_projection_mismatch",
    "job_message_projection_mismatch",
    "sent_missing_delivery_evidence",
    "sent_missing_outbound_task",
    "duplicate_idempotency_key",
    "p1_runtime_artifact",
    "p1_active_ownership_declaration",
}


class _Result:
    def fetchone(self):
        return {"anomaly_count": 1}


class _Connection:
    def __init__(self) -> None:
        self.row_factory = None
        self.statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement, params=()):
        del params
        self.statements.append(str(statement))
        return _Result()


def _retired_manifest(root: Path) -> None:
    path = root / "docs/architecture/data_table_lifecycle_manifest.yml"
    path.parent.mkdir(parents=True)
    table_lines = []
    for name in broadcast_reconciliation._P1_TABLES:
        table_lines.extend(
            [
                f"  {name}:",
                "    lifecycle: retired",
                '    write_owner: ""',
                "    runtime_entrypoints: []",
            ]
        )
    path.write_text("tables:\n" + "\n".join(table_lines) + "\n", encoding="utf-8")


def test_count_only_reconciliation_has_pii_free_read_only_aggregate_counts(monkeypatch, tmp_path) -> None:
    connection = _Connection()
    _retired_manifest(tmp_path)
    monkeypatch.setattr(broadcast_reconciliation, "connect_raw_postgres", lambda url: connection)
    monkeypatch.setattr(
        "aicrm_next.channels.integration_gateway.wecom_private_adapter.build_wecom_private_message_adapter",
        lambda: (_ for _ in ()).throw(AssertionError("provider must not be built")),
    )

    result = GroupOpsBroadcastReconciliationService(
        database_url="postgresql://test/test",
        repo_root=tmp_path,
    ).diagnose()

    assert result["ok"] is True
    assert result["mode"] == "count_only"
    assert result["repair_supported"] is False
    assert set(result["counts"]) == EXPECTED_COUNTS
    assert set(result["counts"].values()) == {0, 1}
    assert result["database_mutation_performed"] is False
    assert result["consumer_executed"] is False
    assert result["provider_executed"] is False
    assert result["real_external_call_executed"] is False
    assert result["pii_in_output"] is False
    statements = "\n".join(connection.statements).upper()
    assert "INSERT INTO" not in statements
    assert "UPDATE " not in statements
    assert "DELETE FROM" not in statements
    for name in (
        "stale_dispatching",
        "unknown_after_dispatch",
        "job_recipient_projection_mismatch",
        "job_message_projection_mismatch",
        "sent_missing_delivery_evidence",
        "sent_missing_outbound_task",
    ):
        assert "TIMESTAMPTZ '2026-07-13 05:42:30+00'" in broadcast_reconciliation._ANOMALY_QUERIES[name]
    assert "TIMESTAMPTZ '2026-07-13 05:42:30+00'" not in broadcast_reconciliation._ANOMALY_QUERIES["duplicate_idempotency_key"]


def test_postgres_reconciliation_is_repeatable_and_does_not_emit_seeded_pii(next_pg_schema, capsys) -> None:
    del next_pg_schema
    pii = "R10-PRIVATE-NAME-DO-NOT-PRINT"
    with get_session_factory()() as session:
        session.execute(
            text(
                """
                INSERT INTO broadcast_jobs (
                    source_type, source_id, source_table, scheduled_for, status,
                    business_domain, idempotency_key, channel, target_kind,
                    target_unionids_json, target_count, content_type, content_payload
                ) VALUES (
                    'manual', 'r10-pii', 'test', CURRENT_TIMESTAMP, 'sent',
                    'test', 'r10-pii-key', 'wecom_private', 'unionid',
                    CAST(:targets AS jsonb), 1, 'text', CAST(:payload AS jsonb)
                )
                """
            ),
            {
                "targets": json.dumps([pii]),
                "payload": json.dumps({"display_name": pii, "text": pii}),
            },
        )
        session.execute(
            text(
                """
                INSERT INTO broadcast_jobs (
                    source_type, source_id, source_table, scheduled_for, status,
                    business_domain, idempotency_key, channel, target_kind,
                    target_unionids_json, target_count, content_type, content_payload,
                    created_at, updated_at
                ) VALUES (
                    'manual', 'r10-historical', 'test', TIMESTAMPTZ '2026-07-13 05:42:29+00', 'sent',
                    'test', 'r10-historical-key', 'wecom_private', 'unionid',
                    CAST(:targets AS jsonb), 1, 'text', CAST(:payload AS jsonb),
                    TIMESTAMPTZ '2026-07-13 05:42:29+00', TIMESTAMPTZ '2026-07-13 05:42:29+00'
                )
                """
            ),
            {
                "targets": json.dumps([pii]),
                "payload": json.dumps({"display_name": pii, "text": pii}),
            },
        )
        session.commit()

    service = GroupOpsBroadcastReconciliationService()
    first = service.diagnose()
    second = service.diagnose()
    captured = capsys.readouterr()

    assert first == second
    assert first["counts"]["sent_missing_delivery_evidence"] == 1
    assert first["counts"]["sent_missing_outbound_task"] == 1
    assert pii not in json.dumps(first, ensure_ascii=False)
    assert pii not in captured.out
    assert pii not in captured.err


def test_reconciliation_cli_exposes_no_repair_mode() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/ops/reconcile_group_ops_broadcast.py", "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "count" in completed.stdout.lower()
    assert "--repair" not in completed.stdout
