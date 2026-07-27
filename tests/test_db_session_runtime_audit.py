from __future__ import annotations

import re
from pathlib import Path


RUNTIME_SQLALCHEMY_FACTORY_ALLOWLIST = {
    Path("aicrm_next/platform/shared/db_session.py"): "single process-level Engine and SessionFactory owner",
}


def test_runtime_create_engine_and_sessionmaker_are_centralized() -> None:
    root = Path("aicrm_next")
    pattern = re.compile(r"\b(create_engine|sessionmaker)\s*\(")
    hits: list[str] = []
    for path in root.rglob("*.py"):
        relative_path = path.relative_to(Path("."))
        if "migrations" in relative_path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line) and relative_path not in RUNTIME_SQLALCHEMY_FACTORY_ALLOWLIST:
                hits.append(f"{relative_path}:{line_no}: {line.strip()}")

    assert hits == []


def test_sql_repository_close_does_not_dispose_global_engine() -> None:
    checked_files = [
        Path("aicrm_next/crm/customer_read_model/repo.py"),
        Path("aicrm_next/crm/customer_read_model/repo_live_source.py"),
        Path("aicrm_next/ops_enrollment/repo.py"),
        Path("aicrm_next/crm/customer_read_model/sidebar_v2.py"),
    ]

    for path in checked_files:
        source = path.read_text(encoding="utf-8")
        assert "create_engine(" not in source or path == Path("aicrm_next/platform/shared/db_session.py")
        assert "sessionmaker(" not in source
        assert ".dispose(" not in source
