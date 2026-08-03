from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "aicrm_next/extensions/forms/questionnaire/templates/admin_questionnaires.html"
STYLESHEET = ROOT / "aicrm_next/extensions/forms/questionnaire/static/admin_questionnaire_editor.css"
SCRIPT = ROOT / "aicrm_next/extensions/forms/questionnaire/static/admin_questionnaire_editor.js"


def test_questionnaire_editor_template_is_thin_and_assets_are_jinja_free() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")
    stylesheet = STYLESHEET.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")

    assert len(template.splitlines()) < 3000
    assert "<style>" not in template
    assert '<script id="questionnaire-editor-config" type="application/json">' in template
    assert '<script src="/static/questionnaire/admin_questionnaire_editor.js?v=20260715-operations-only"></script>' in template
    assert '<link rel="stylesheet" href="/static/questionnaire/admin_questionnaire_editor.css?v=lark-template-20260803">' in template
    assert "{{" not in stylesheet and "{%" not in stylesheet
    assert "{{" not in script and "{%" not in script
    assert "questionnaire-editor-config" in script
    assert "JSON.parse(editorConfigElement.textContent" in script


def test_questionnaire_editor_page_serializes_config_and_serves_assets() -> None:
    client = TestClient(create_app())
    page = client.get("/admin/questionnaires/new")
    stylesheet = client.get("/static/questionnaire/admin_questionnaire_editor.css")
    script = client.get("/static/questionnaire/admin_questionnaire_editor.js")

    assert page.status_code == 200
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    match = re.search(
        r'<script id="questionnaire-editor-config" type="application/json">\s*(.*?)\s*</script>',
        page.text,
        flags=re.S,
    )
    assert match is not None
    config = json.loads(match.group(1))
    assert config["mode"] == "new"
    assert config["initialQuestionnaire"] is None
    assert config["initialQuestionnaireId"] is None


def test_questionnaire_editor_keeps_key_dom_and_behavior_contracts() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")

    for element_id in (
        "back-link",
        "reset-btn",
        "save-btn",
        "add-single",
        "add-multi",
        "add-textarea",
        "add-mobile",
        "open-assessment-settings",
        "add-rule",
        "preview-head",
        "preview-questions",
        "inspector-body",
        "drawer-overlay",
        "toast",
    ):
        assert f'id="{element_id}"' in template
    for contract in (
        "/api/admin/questionnaires",
        "loadAvailableTags",
        "validateOtherOptionsBeforeSave",
        "renderWorkspace",
        "resetDraft",
    ):
        assert contract in script


def test_questionnaire_editor_matches_the_supplied_three_column_template() -> None:
    stylesheet = STYLESHEET.read_text(encoding="utf-8")

    for visual_contract in (
        "grid-template-columns: 270px minmax(420px, 1fr) 340px",
        "width: 372px",
        "border-radius: 24px",
        "background: #f5f6f7",
        "body:not(.assessment-template-mode) .tool-card .tool-mark",
    ):
        assert visual_contract in stylesheet
