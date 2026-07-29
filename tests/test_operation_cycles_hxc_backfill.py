from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from aicrm_next.extensions.hxc.operation_cycles.dto import OperationCycleSnapshotV1


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_hxc_monday_operation_cycle_snapshot.py"
FIXTURE = ROOT / "fixtures" / "operation_cycles" / "hxc_monday_20260713_snapshot.json"
FORBIDDEN_OUTPUT_KEYS = {
    "phone",
    "mobile",
    "unionid",
    "external_userid",
    "openid",
    "nickname",
    "display_name",
    "recipient",
    "raw_message",
    "content_text",
    "credential",
    "access_token",
    "secret",
    "password",
}
PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hxc_operation_cycle_backfill", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _walk_keys(value: object) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            keys.append(str(key).lower().replace("-", "_"))
            keys.extend(_walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.extend(_walk_keys(child))
    return keys


def _assert_safe_snapshot(payload: dict) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert not PHONE_PATTERN.search(serialized)
    assert "/Users/" not in serialized
    assert "file://" not in serialized
    for key in _walk_keys(payload):
        assert not any(part in key for part in FORBIDDEN_OUTPUT_KEYS), key


def test_committed_fixture_is_safe_and_preserves_the_known_aggregate_funnel() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    validated = OperationCycleSnapshotV1.model_validate(payload)

    assert payload["schema_version"] == "operation_cycle_snapshot.v1"
    assert validated.report_id == payload["report_id"]
    assert payload["external_effects"] == "none"
    assert payload["run"]["run_key"] == "hxc_monday_full_activation_20260713"
    assert payload["run"]["plan_version"] == ""
    assert payload["run"]["plan_status"] == "draft"
    assert payload["run"]["plan_source"] == "cloud_broadcast_plans.production_readonly_aggregate"
    assert {key: item["value"] for key, item in payload["funnel"].items()} == {
        "audited_count": 895,
        "candidate_count": 1275,
        "effective_sent_count": 845,
        "failed_count": 3,
        "planned_target_count": 848,
        "recommended_send_count": 848,
    }
    assert payload["delivery_status"] == "partial"
    assert payload["data_status"] == "attribution_gap"
    assert payload["review_status"] == "approved"
    assert payload["funnel"]["effective_sent_count"]["data_source"] == "broadcast_jobs.production_readonly_aggregate"
    assert payload["funnel"]["failed_count"]["data_source"] == "broadcast_jobs.production_readonly_aggregate"
    assert payload["funnel"]["failed_count"]["classification"] == "failed_retryable"
    assert all(metric["is_causal"] is False for metric in payload["metrics"])
    assert all(metric["value_status"] == "unknown" for metric in payload["metrics"] if metric["observation_window"] in {"T+2h", "T+24h", "T+48h", "T+72h"})
    assert any("draft" in conflict for conflict in payload["retrospective"]["data_conflicts"])
    assert set(payload["documents"]) == {
        "broadcast_details",
        "retrospective_details",
        "execution_strategy",
    }
    assert payload["documents"]["broadcast_details"]["markdown"].startswith("# 2026-07-13 本周发送数据")
    assert payload["documents"]["retrospective_details"]["markdown"].startswith("# 2026-07-13 本周复盘明细")
    assert payload["documents"]["execution_strategy"]["markdown"].startswith("# 2026-07-20 下周执行策略")
    delivery_reference = next(item for item in payload["references"] if item["reference_key"].endswith("delivery-metrics"))
    assert delivery_reference["data_status"] == "observed"
    assert delivery_reference["source_system"] == "ai_crm_production_readonly"
    assert delivery_reference["source_id"] == "hxc-monday-abcd-20260713-1600-final-848-weekly-cover-v1"
    assert delivery_reference["href"] == ""
    assert len(delivery_reference["evidence_hash"]) == 64
    _assert_safe_snapshot(payload)


def test_builder_extracts_only_allowlisted_aggregates_from_sensitive_source_files(tmp_path: Path) -> None:
    module = _load_script()
    raw_phone = "13800138000"
    raw_external_id = "wm_sensitive_external_contact"
    raw_message = "这是一条不应进入快照的原始消息"
    send_summary = _write_json(
        tmp_path / "send_summary.json",
        {
            "generated_at": "2026-07-13T15:34:00",
            "recipient_count": 895,
            "sendable_count": 848,
            "decision_counts": {"建议发送": 848},
            "debug_people": [{"phone": raw_phone}],
        },
    )
    campaign_input = _write_json(
        tmp_path / "campaign_input.json",
        {
            "candidate_count": 1275,
            "recipient_count": 848,
            "scheduled_for": "2026-07-13 16:00",
            "recipients": [{"external_userid": raw_external_id, "content_text": raw_message}],
        },
    )
    create_response = _write_json(
        tmp_path / "create_response.json",
        {
            "recipient_count": 848,
            "scheduled_for": "2026-07-13 16:00",
            "review_status": "pending_review",
            "run_status": "draft",
            "plan_id": "internal-plan-not-copied",
        },
    )
    delivery_metrics = _write_json(
        tmp_path / "delivery_metrics.json",
        {
            "delivery": {
                "status_counts": {"sent": 844, "failed_retryable": 4},
                "first_sent_at": "2026-07-13T16:00:05+08:00",
                "last_sent_at": "2026-07-13T16:24:10+08:00",
            },
            "observation_windows": {
                "T+2h": {"active_message_count": 14, "target_behavior_count": 3},
            },
            "raw_message": raw_message,
        },
    )

    snapshot = module.build_snapshot(
        send_summary_path=send_summary,
        campaign_input_path=campaign_input,
        create_response_path=create_response,
        delivery_metrics_path=delivery_metrics,
        reported_at="2026-07-14T00:00:00+08:00",
    )
    serialized = json.dumps(snapshot, ensure_ascii=False)

    assert raw_phone not in serialized
    assert raw_external_id not in serialized
    assert raw_message not in serialized
    assert str(tmp_path) not in serialized
    assert snapshot["funnel"]["effective_sent_count"]["value"] == 844
    assert snapshot["funnel"]["failed_count"]["value"] == 4
    assert snapshot["run"]["first_sent_at"] == "2026-07-13T16:00:05+08:00"
    assert snapshot["run"]["last_sent_at"] == "2026-07-13T16:24:10+08:00"
    t2_metrics = [metric for metric in snapshot["metrics"] if metric["observation_window"] == "T+2h"]
    assert {(metric["metric_key"], metric["numerator"]) for metric in t2_metrics} == {
        ("active_message_count_t2h", 14),
        ("target_behavior_count_t2h", 3),
    }
    assert all(item["data_status"] == "observed" for item in snapshot["references"])
    assert all(len(item["evidence_hash"]) == 64 for item in snapshot["references"])
    _assert_safe_snapshot(snapshot)


def test_builder_marks_unavailable_sources_without_fabricating_behavior_results() -> None:
    module = _load_script()
    snapshot = module.build_snapshot()

    assert all(item["data_status"] == "unknown" for item in snapshot["references"])
    assert snapshot["artifact_status"] == "source_missing"
    assert snapshot["retrospective"]["data_conflicts"] == []
    assert all(item["status"] == "unknown" and item["value"] is None for item in snapshot["funnel"].values())
    assert snapshot["run"]["first_sent_at"] is None
    assert snapshot["run"]["last_sent_at"] is None
    assert snapshot["attempts"][1]["parent_attempt_key"] == snapshot["attempts"][0]["attempt_key"]
    assert next(stage for stage in snapshot["stages"] if stage["stage"] == "observing")["blocked_reason"] == "instrumentation_missing"
    behavior_metrics = [metric for metric in snapshot["metrics"] if metric["observation_window"].startswith("T+")]
    assert behavior_metrics
    assert all(metric["numerator"] is None and metric["value"] is None for metric in behavior_metrics)
    assert all(metric["data_quality"] == "source_missing" for metric in behavior_metrics)
    _assert_safe_snapshot(snapshot)


@pytest.mark.parametrize(
    "payload",
    [
        {"union_id": "private"},
        {"external_user_id": "private"},
        {"raw_msg": "private"},
        {"note": "sk-proj-abcdefghijklmnopqrstuv123456"},
    ],
)
def test_builder_output_guard_rejects_private_key_variants_and_credentials(payload: dict) -> None:
    module = _load_script()
    with pytest.raises(ValueError):
        module.assert_snapshot_safe(payload)


def test_builder_rejects_an_inconsistent_aggregate_funnel(tmp_path: Path) -> None:
    module = _load_script()
    send_summary = _write_json(tmp_path / "summary.json", {"recipient_count": 895, "sendable_count": 848})
    campaign_input = _write_json(tmp_path / "campaign.json", {"candidate_count": 800, "recipient_count": 848})

    with pytest.raises(ValueError, match="aggregate funnel is inconsistent"):
        module.build_snapshot(send_summary_path=send_summary, campaign_input_path=campaign_input)


def test_cli_receipt_does_not_echo_absolute_input_or_output_paths(tmp_path: Path) -> None:
    output = tmp_path / "snapshot.json"
    send_summary = _write_json(tmp_path / "summary.json", {"recipient_count": 895, "sendable_count": 848})
    campaign_input = _write_json(
        tmp_path / "campaign.json",
        {"candidate_count": 1275, "recipient_count": 848, "scheduled_for": "2026-07-13 16:00"},
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--send-summary",
            str(send_summary),
            "--campaign-input",
            str(campaign_input),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["ok"] is True
    assert len(receipt["snapshot_hash"]) == 64
    assert str(tmp_path) not in result.stdout
    snapshot = json.loads(output.read_text(encoding="utf-8"))
    assert str(tmp_path) not in json.dumps(snapshot, ensure_ascii=False)
    _assert_safe_snapshot(snapshot)
