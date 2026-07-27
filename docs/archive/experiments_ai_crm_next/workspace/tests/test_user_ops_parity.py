from __future__ import annotations

import json
import re
from pathlib import Path

from conftest import make_client

from aicrm_next.automation.ops_enrollment.parity_spec import (
    OVERVIEW_CARD_LABELS,
    compare_card_labels,
    compare_required_keys,
    validate_payload,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OLD_FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "old_user_ops"


def _fixture_payload(name: str) -> dict:
    return json.loads((OLD_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))["payload"]


def test_parity_spec_contains_8_overview_labels() -> None:
    assert OVERVIEW_CARD_LABELS == [
        "引流品总数",
        "已加微",
        "未加微",
        "已绑手机号",
        "未绑手机号",
        "黄小璨已激活",
        "黄小璨未激活",
        "激活待录入",
    ]


def test_old_user_ops_fixtures_conform_to_parity_spec() -> None:
    for name in [
        "overview.default",
        "list.default",
        "list.wecom_added",
        "list.not_added",
        "preview.default",
        "send_records.default",
    ]:
        assert validate_payload(name, _fixture_payload(name)) == []


def test_next_user_ops_overview_conforms_to_parity_spec() -> None:
    payload = make_client().get("/api/admin/user-ops/overview").json()
    assert validate_payload("overview.default", payload) == []


def test_next_user_ops_list_conforms_to_parity_spec() -> None:
    payload = make_client().get("/api/admin/user-ops/list").json()
    assert validate_payload("list.default", payload) == []


def test_next_user_ops_preview_conforms_to_parity_spec() -> None:
    payload = make_client().post(
        "/api/admin/user-ops/batch-send/preview",
        json={"selection_mode": "all_filtered", "content": "parity dry run"},
    ).json()
    assert validate_payload("preview.default", payload) == []


def test_next_user_ops_execute_conforms_to_parity_spec() -> None:
    payload = make_client().post(
        "/api/admin/user-ops/batch-send/execute",
        json={"selection_mode": "manual", "selected_ids": [1], "content": "parity dry run", "confirm": True},
    ).json()
    assert validate_payload("execute.default", payload) == []
    assert payload["execution_summary"]["dispatch_adapter"] == "fake_wecom"


def test_next_user_ops_send_records_conforms_to_parity_spec() -> None:
    payload = make_client().get("/api/admin/user-ops/send-records").json()
    assert validate_payload("send_records.default", payload) == []


def test_comparison_detects_missing_required_key() -> None:
    issues = compare_required_keys({"ok": True}, ["ok", "items"])
    assert issues == [{"rule": "required_key", "location": "$", "key": "items", "severity": "fail"}]


def test_comparison_detects_missing_card_label() -> None:
    payload = {"cards": [{"label": label} for label in OVERVIEW_CARD_LABELS if label != "黄小璨未激活"]}
    issues = compare_card_labels(payload)
    assert any(issue.get("label") == "黄小璨未激活" for issue in issues)


def test_old_user_ops_fixtures_use_masked_values() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in OLD_FIXTURE_DIR.glob("*.json"))
    assert "mobile_masked_001" in combined
    assert "external_user_masked_001" in combined
    assert "customer_masked_001" in combined
    assert re.search(r"1[3-9]\d{9}", combined) is None
    assert "old_ext_" not in combined
