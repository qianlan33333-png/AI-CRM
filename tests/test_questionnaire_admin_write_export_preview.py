from __future__ import annotations

import csv
from io import StringIO

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from wechat_identity_test_support import authorize_wechat_client


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    client = TestClient(create_app())
    authorize_wechat_client(client, {"openid": "openid_001", "unionid": "unionid_001", "external_userid": "wx_ext_001"})
    return client


def test_questionnaire_export_preview_returns_masked_sample_and_plan_only(client: TestClient) -> None:
    response = client.post(
        "/api/admin/questionnaires/1/export/preview",
        json={"fields": ["external_userid", "answers", "created_at"]},
        headers={"Idempotency-Key": "questionnaire-export-preview"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["command_name"] == "questionnaire.admin.export_preview"
    assert body["source_status"] == "next_command"
    assert body["write_model_status"] == "export_preview_planned"
    assert body["real_external_call_executed"] is False
    assert body["export_preview"]["file_created"] is False
    assert body["export_preview"]["estimated_count"] >= 1
    assert body["export_preview"]["masked_sample"][0]["external_userid"] == "masked"
    assert body["side_effect_plan"]["effect_type"] == "questionnaire.export.preview"
    assert body["side_effect_plan"]["adapter_mode"] == "real_blocked"


def test_existing_get_export_route_downloads_csv_without_storage_file(client: TestClient) -> None:
    submit = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={
            "answers": {
                "q_activation": "activated",
                "q_interest": ["ai_tools"],
                "q_note": "希望补充运营建议",
            },
            "identity": {
                "unionid": "unionid_001",
                "external_userid": "wx_ext_001",
                "mobile": "13800138000",
            },
        },
    )
    assert submit.status_code == 200

    response = client.get("/api/admin/questionnaires/1/export", headers={"Idempotency-Key": "questionnaire-export-get"})

    assert response.status_code == 200
    assert response.headers["Content-Disposition"].startswith('attachment; filename="questionnaire-hxc-activation-v1-submissions.csv"')
    assert response.headers["X-AICRM-Source-Status"] == "next_command"
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    csv_text = response.content.decode("utf-8-sig")
    reader = csv.DictReader(StringIO(csv_text))
    assert reader.fieldnames == [
        "submission_id",
        "submitted_at",
        "external_userid",
        "unionid",
        "mobile",
        "score",
        "final_tags",
        "黄小璨是否已激活？",
        "你关注哪些能力？",
        "还有什么想补充？",
    ]
    assert "matched_by" not in (reader.fieldnames or [])
    assert "answers" not in (reader.fieldnames or [])
    rows = list(reader)
    fixture_row = next(row for row in rows if row["submission_id"] == "sub_fixture_001")
    submitted_row = next(row for row in rows if row["unionid"] == "unionid_001")
    assert fixture_row["submitted_at"] == "2026-05-20 18:10:00"
    assert "T" not in fixture_row["submitted_at"]
    assert "+" not in fixture_row["submitted_at"]
    assert fixture_row["黄小璨是否已激活？"] == "已激活"
    assert submitted_row["黄小璨是否已激活？"] == "已激活"
    assert submitted_row["你关注哪些能力？"] == "AI 工具"
    assert submitted_row["还有什么想补充？"] == "希望补充运营建议"
