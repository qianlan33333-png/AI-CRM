from __future__ import annotations

from pathlib import Path

import pytest

from aicrm_next.channels.integration_gateway.mcp import MCP_TOOLS


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "image-library-curator"
SKILL_MD = SKILL_DIR / "SKILL.md"
README_MD = SKILL_DIR / "README.md"
REFERENCES_DIR = SKILL_DIR / "references"


@pytest.fixture(scope="module")
def skill_source() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def readme_source() -> str:
    return README_MD.read_text(encoding="utf-8")


def test_skill_docs_are_historical_media_tool_docs_not_current_mcp_surface(skill_source: str) -> None:
    current_tools = {tool["name"] for tool in MCP_TOOLS}

    assert "image_library_list" in skill_source
    assert "image_library_list" not in current_tools
    assert current_tools == {"resolve_customer", "get_customer_context", "get_recent_messages"}


def test_skill_docs_still_point_to_local_mcp_endpoint(readme_source: str) -> None:
    assert ".mcp.json" in readme_source
    assert "MCP_BEARER_TOKEN" in readme_source
    assert "/mcp" in readme_source


def test_image_library_skill_references_remain_complete() -> None:
    expected = {
        "system-prompt.md",
        "workflow-a-batch-annotate.md",
        "workflow-b-upload-annotate.md",
        "workflow-c-recommend.md",
    }
    assert expected <= {path.name for path in REFERENCES_DIR.glob("*.md")}


def test_system_prompt_and_workflows_preserve_media_curation_contract() -> None:
    system_prompt = (REFERENCES_DIR / "system-prompt.md").read_text(encoding="utf-8")
    workflow_a = (REFERENCES_DIR / "workflow-a-batch-annotate.md").read_text(encoding="utf-8")
    workflow_b = (REFERENCES_DIR / "workflow-b-upload-annotate.md").read_text(encoding="utf-8")
    workflow_c = (REFERENCES_DIR / "workflow-c-recommend.md").read_text(encoding="utf-8")

    for field in ("description", "tags", "category", "ai_metadata"):
        assert field in system_prompt
    assert "facets" in system_prompt.lower()
    assert "overwrite: false" in workflow_a or "overwrite=false" in workflow_a
    assert "image_library_upload" in workflow_b
    assert "image_library_list" in workflow_c
