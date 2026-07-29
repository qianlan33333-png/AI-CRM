from __future__ import annotations

import ast
from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


ADMIN_READ_HANDLER_NAMES = {
    "list_questionnaires",
    "get_questionnaire",
    "get_questionnaire_questions",
    "get_questionnaire_results",
    "get_questionnaire_submissions",
}


def _function_source(path: Path, names: set[str]) -> str:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    snippets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            snippets.append(ast.get_source_segment(text, node) or "")
    return "\n".join(snippets)


def test_questionnaire_admin_read_handlers_do_not_forward_or_call_external_services() -> None:
    source = _function_source(Path("aicrm_next/extensions/forms/questionnaire/api.py"), ADMIN_READ_HANDLER_NAMES)

    forbidden = [
        "forward_to_legacy_flask",
        "list_questionnaires_from_legacy",
        "get_questionnaire_detail_from_legacy",
        "requests.post(",
        "httpx.post(",
        "emit_external_push",
        "apply_tags",
        "create_questionnaire_in_legacy",
        "update_questionnaire_in_legacy",
        "delete_questionnaire_in_legacy",
    ]
    assert [marker for marker in forbidden if marker in source] == []


def test_questionnaire_admin_read_responses_have_no_real_external_call_markers() -> None:
    client = TestClient(create_app())
    responses = [
        client.get("/api/admin/questionnaires"),
        client.get("/api/admin/questionnaires/1"),
        client.get("/api/admin/questionnaires/1/questions"),
        client.get("/api/admin/questionnaires/1/results"),
        client.get("/api/admin/questionnaires/1/submissions"),
    ]

    for response in responses:
        assert response.status_code == 200
        payload = response.json()
        assert payload["fallback_used"] is False
        assert payload["route_owner"] == "ai_crm_next"
        assert "real_external_call_executed" not in payload
        assert "compatibility_facade" not in payload
