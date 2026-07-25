from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_IDENTITY_QUERY_FILES = (
    "aicrm_next/identity_contact/repo.py",
    "aicrm_next/questionnaire/repo.py",
    "aicrm_next/automation_agents/repository.py",
)


def test_runtime_identity_alias_queries_use_gin_indexable_containment() -> None:
    for relative_path in RUNTIME_IDENTITY_QUERY_FILES:
        source = (ROOT / relative_path).read_text(encoding="utf-8")

        assert "jsonb_exists(external_userids_json" not in source
        assert "jsonb_exists(identity.external_userids_json" not in source
        assert "jsonb_array_elements(external_userids_json)" not in source
        assert "jsonb_array_elements(identity.external_userids_json)" not in source

    combined = "\n".join((ROOT / relative_path).read_text(encoding="utf-8") for relative_path in RUNTIME_IDENTITY_QUERY_FILES)
    assert "external_userids_json @> jsonb_build_array" in combined
    assert "jsonb_build_object('external_userid'" in combined

    questionnaire_source = (ROOT / "aicrm_next/questionnaire/repo.py").read_text(encoding="utf-8")
    assert "clauses.append(\"qs.unionid = %s AND qs.unionid <> ''\")" in questionnaire_source
    assert "resolved_unionid" in questionnaire_source

    automation_source = (ROOT / "aicrm_next/automation_agents/repository.py").read_text(encoding="utf-8")
    assert "identity_clause = \"AND s.unionid = :unionid AND s.unionid <> ''\"" in automation_source
