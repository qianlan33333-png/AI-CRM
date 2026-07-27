from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.ops_enrollment import application, repo as repo_module
from aicrm_next.ops_enrollment.repo import InMemoryUserOpsRepository, SqlAlchemyUserOpsRepository
from aicrm_next.platform.shared.repository_provider import evaluate_repository

ROOT = Path(__file__).resolve().parents[1]
USER_OPS_PROD_TABLES_MIGRATION = ROOT / "migrations" / "versions" / "0029_user_ops_prod_tables.py"


class FakeSession:
    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


class ProductionUserOpsRepository:
    def list_rows(self) -> list[dict]:
        return [
            {
                "id": 1,
                "unionid": "union_ops_001",
                "mobile": "13800138000",
                "external_userid": "wx_ext_001",
                "customer_name": "张小蓝",
                "owner_userid": "owner-a",
                "owner_display_name": "顾问甲",
                "class_term_no": "2026-05-A",
                "class_term_label": "2026 五月 A 班",
                "source_type": "lead_pool",
                "created_at": "2026-05-01T09:00:00+08:00",
                "updated_at": "2026-05-18T10:00:00+08:00",
                "activation_bucket": "activated",
                "tags": ["黄小璨"],
                "is_added_wecom": True,
                "is_mobile_bound": True,
                "do_not_disturb": False,
                "do_not_disturb_reasons": [],
            }
        ]

    def close(self) -> None:
        pass


def _production_postgres_env(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://probe:probe@127.0.0.1:1/aicrm_probe")
    monkeypatch.delenv("USER_OPS_REPO_BACKEND", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ALLOW_FIXTURE_REPO_IN_PROD", raising=False)


def test_postgres_mode_defaults_to_sqlalchemy_repo_without_backend_env(monkeypatch) -> None:
    _production_postgres_env(monkeypatch)
    session = FakeSession()
    monkeypatch.setattr(repo_module, "get_session_factory", lambda *, settings: lambda: session)

    repository = repo_module.build_user_ops_repository()
    decision = evaluate_repository(repository, capability_owner="ops_enrollment")

    assert isinstance(repository, SqlAlchemyUserOpsRepository)
    assert not isinstance(repository, InMemoryUserOpsRepository)
    assert repository._session is session
    assert decision.ok is True
    assert decision.repository_kind == "production"


def test_fixture_mode_still_defaults_to_in_memory_repo(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("USER_OPS_REPO_BACKEND", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)

    repository = repo_module.build_user_ops_repository()

    assert isinstance(repository, InMemoryUserOpsRepository)


def test_user_ops_overview_does_not_hit_fixture_blocker_in_postgres_mode(monkeypatch) -> None:
    _production_postgres_env(monkeypatch)
    application._REPO = None
    monkeypatch.setattr(application, "build_user_ops_repository", lambda: ProductionUserOpsRepository())
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/api/admin/user-ops/overview")
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert "fixture_repository_blocked_in_production" not in response.text


def test_sqlalchemy_user_ops_tables_are_present_in_mainline_migrations() -> None:
    migration = USER_OPS_PROD_TABLES_MIGRATION.read_text(encoding="utf-8")

    for table_name in (
        "user_ops_pool_current_next",
        "user_ops_do_not_disturb_next",
        "user_ops_send_records_next",
    ):
        assert table_name in migration
    assert "INSERT INTO" not in migration
