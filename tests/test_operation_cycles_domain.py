from __future__ import annotations

import pytest
from pydantic import ValidationError

from aicrm_next.extensions.hxc.operation_cycles.domain import compute_snapshot_hash
from aicrm_next.extensions.hxc.operation_cycles.dto import OperationCycleSnapshotV1


def snapshot_payload() -> dict:
    return {
        "schema_version": "operation_cycle_snapshot.v1",
        "external_effects": "none",
        "report_id": "monday-20260713-r1",
        "snapshot_revision": 1,
        "strategy": {
            "strategy_key": "hxc.monday.activation",
            "title": "每周一全量用户激活",
            "version": 1,
            "definition": {"audience": "all_active_customers"},
        },
        "run": {
            "run_key": "hxc.monday.activation.20260713",
            "label": "2026-07-13 周一激活",
            "plan_version": "review-v2",
            "plan_status": "draft",
            "plan_source": "cloud_broadcast_plans",
        },
        "execution_stage": "observing",
        "review_status": "approved",
        "delivery_status": "completed",
        "data_status": "early",
        "optimization_status": "draft",
        "artifact_status": "complete",
        "attempts": [
            {
                "attempt_key": "preflight-blocked",
                "status": "blocked",
                "blocked_reason": "source unavailable",
                "summary": {},
            },
            {"attempt_key": "resumed", "parent_attempt_key": "preflight-blocked", "status": "completed"},
        ],
        "stages": [
            {"stage_key": "preflight-1", "attempt_key": "preflight-blocked", "stage": "preflight", "status": "blocked"},
            {"stage_key": "delivery-1", "attempt_key": "resumed", "stage": "delivery", "status": "completed"},
        ],
        "funnel": {
            "candidate_count": {"status": "observed", "value": 1275, "data_source": "crm"},
            "audited_count": {"status": "observed", "value": 895, "data_source": "audit"},
            "recommended_send_count": {"status": "observed", "value": 848, "data_source": "review"},
            "planned_target_count": {"status": "observed", "value": 848, "data_source": "plan"},
            "effective_sent_count": {"status": "observed", "value": 845, "data_source": "broadcast_jobs"},
            "failed_count": {"status": "observed", "value": 3, "data_source": "broadcast_jobs"},
        },
        "metrics": [
            {
                "metric_key": "active_message_12h",
                "label": "12 小时主动消息",
                "numerator": 14,
                "denominator": 845,
                "value": 14,
                "unit": "people",
                "observation_window": "T+12h",
                "data_source": "message_archive_lower_bound",
                "data_quality": "partial_lower_bound",
                "limitations": ["仅为观察信号"],
                "is_causal": False,
                "value_status": "partial_lower_bound",
            }
        ],
        "retrospective": {
            "conclusion": "实际发送已发生，但计划投影仍为草稿。",
            "data_conflicts": ["plan=draft while broadcast delivery completed"],
        },
        "next_iteration": {"summary": "补齐 cardTrack 埋点", "status": "draft"},
        "references": [
            {
                "reference_key": "broadcast-summary",
                "reference_type": "broadcast_job",
                "label": "发送事实",
                "source_system": "broadcast_jobs",
                "source_id": "20260713-monday",
                "href": "/admin/broadcast-jobs",
                "evidence_hash": "a" * 64,
                "data_status": "observed",
            }
        ],
    }


def test_complete_snapshot_validates_and_hash_is_canonical() -> None:
    left = OperationCycleSnapshotV1.model_validate(snapshot_payload())
    right_payload = snapshot_payload()
    right_payload["strategy"] = dict(reversed(list(right_payload["strategy"].items())))
    right = OperationCycleSnapshotV1.model_validate(right_payload)

    assert compute_snapshot_hash(left) == compute_snapshot_hash(right)
    assert left.external_effects == "none"
    assert left.funnel.failed_count.value == 3
    assert left.documents.broadcast_details.markdown == ""
    assert left.documents.retrospective_details.markdown == ""
    assert left.documents.execution_strategy.markdown == ""


def test_snapshot_accepts_three_opaque_markdown_documents() -> None:
    payload = snapshot_payload()
    payload["documents"] = {
        "broadcast_details": {
            "markdown": '# 群发数据\n\n```chart\n{"type":"bar"}\n```',
            "generated_at": "2026-07-14T09:00:00+08:00",
        },
        "retrospective_details": {
            "markdown": "# 本周复盘\n\n记录结论、证据边界与未解决问题。",
            "generated_at": "2026-07-14T09:05:00+08:00",
        },
        "execution_strategy": {
            "markdown": "# 下周执行策略\n\n只保存 Markdown，不拆业务字段。",
            "generated_at": "2026-07-14T09:10:00+08:00",
        },
    }

    snapshot = OperationCycleSnapshotV1.model_validate(payload)

    assert snapshot.documents.broadcast_details.markdown.startswith("# 群发数据")
    assert snapshot.documents.retrospective_details.markdown.startswith("# 本周复盘")
    assert snapshot.documents.execution_strategy.generated_at is not None


def test_markdown_documents_use_existing_recursive_privacy_guard() -> None:
    payload = snapshot_payload()
    payload["documents"] = {
        "broadcast_details": {"markdown": "请联系 13800138000"},
        "retrospective_details": {"markdown": ""},
        "execution_strategy": {"markdown": ""},
    }

    with pytest.raises(ValidationError, match="phone number is forbidden"):
        OperationCycleSnapshotV1.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("strategy", "definition", "external_userid"), "wm_private"),
        (("strategy", "definition", "external_user_id"), "wm_private"),
        (("strategy", "definition", "union_id"), "private-union"),
        (("attempts", 0, "summary", "original_message"), "private content"),
        (("attempts", 0, "summary", "raw_msg"), "private content"),
        (("attempts", 0, "summary", "messages"), ["private content"]),
        (("attempts", 0, "summary", "nested"), {"message_content": "private content"}),
        (("attempts", 0, "summary", "note"), "sk-proj-abcdefghijklmnopqrstuv123456"),
        (("retrospective", "observations"), ["请联系 13800138000"]),
        (("retrospective", "observations"), ["请联系 +86 138-0013-8000"]),
        (("attempts", 0, "summary", "people"), [{"value": "anonymous"}]),
        (("references", 0, "href"), "file:///Users/example/private.json"),
    ],
)
def test_snapshot_recursively_rejects_private_data(path: tuple, value) -> None:
    payload = snapshot_payload()
    cursor = payload
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value

    with pytest.raises(ValidationError):
        OperationCycleSnapshotV1.model_validate(payload)


@pytest.mark.parametrize("missing_field", ["numerator", "denominator", "limitations"])
def test_observed_metric_requires_complete_definition(missing_field: str) -> None:
    payload = snapshot_payload()
    metric = payload["metrics"][0]
    if missing_field == "limitations":
        metric[missing_field] = []
    else:
        metric.pop(missing_field)

    with pytest.raises(ValidationError):
        OperationCycleSnapshotV1.model_validate(payload)


def test_attempt_parent_cycle_is_rejected() -> None:
    payload = snapshot_payload()
    payload["attempts"] = [
        {"attempt_key": "a", "parent_attempt_key": "b", "status": "blocked"},
        {"attempt_key": "b", "parent_attempt_key": "a", "status": "blocked"},
    ]
    payload["stages"] = []

    with pytest.raises(ValidationError, match="parent cycle"):
        OperationCycleSnapshotV1.model_validate(payload)


def test_stage_must_reference_an_attempt() -> None:
    payload = snapshot_payload()
    payload["stages"][0]["attempt_key"] = "missing"

    with pytest.raises(ValidationError, match="stage attempt does not exist"):
        OperationCycleSnapshotV1.model_validate(payload)


def test_observed_reference_requires_sha256_evidence_hash() -> None:
    payload = snapshot_payload()
    payload["references"][0]["evidence_hash"] = ""

    with pytest.raises(ValidationError, match="evidence_hash is required"):
        OperationCycleSnapshotV1.model_validate(payload)


def test_non_observed_zero_is_rejected_but_observed_zero_is_preserved() -> None:
    valid = snapshot_payload()
    valid["funnel"]["failed_count"] = {"status": "observed", "value": 0}
    assert OperationCycleSnapshotV1.model_validate(valid).funnel.failed_count.value == 0

    invalid = snapshot_payload()
    invalid["funnel"]["failed_count"] = {"status": "not_due", "value": 0}
    with pytest.raises(ValidationError, match="must be null"):
        OperationCycleSnapshotV1.model_validate(invalid)


def test_causal_metric_claim_is_rejected() -> None:
    payload = snapshot_payload()
    payload["metrics"][0]["is_causal"] = True

    with pytest.raises(ValidationError):
        OperationCycleSnapshotV1.model_validate(payload)
