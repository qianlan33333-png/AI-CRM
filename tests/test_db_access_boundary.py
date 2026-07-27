from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tools.check_db_access_boundary import check_db_access_boundary, load_config


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _config(*, allowlist: list[dict] | None = None) -> dict:
    return {
        "allowed_paths": ["aicrm_next/shared/db_session.py"],
        "allowed_globs": [
            "aicrm_next/**/repo.py",
            "aicrm_next/**/repository.py",
            "aicrm_next/**/repositories.py",
            "migrations/**",
            "scripts/**",
            "tests/**",
            "tools/**",
        ],
        "forbidden_layers": [
            "aicrm_next/*/api.py",
            "aicrm_next/*/routes.py",
            "aicrm_next/*/admin_pages.py",
            "aicrm_next/*/application.py",
            "aicrm_next/*/service.py",
            "aicrm_next/app/admin_console/**",
            "aicrm_next/*/frontend_compat/**",
        ],
        "temporary_allowlist": allowlist or [],
    }


def _write_config(path: Path, *, allowlist: list[dict] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(_config(allowlist=allowlist), sort_keys=False), encoding="utf-8")


def test_db_access_boundary_allows_approved_boundary_paths(tmp_path: Path) -> None:
    _write_config(tmp_path / "db_access_boundary.yml")
    _write(tmp_path / "aicrm_next" / "demo_context" / "repo.py", "def run(session):\n    session.execute('select 1')\n")
    _write(
        tmp_path / "aicrm_next" / "shared" / "db_session.py",
        "from sqlalchemy import create_engine\nfrom sqlalchemy.orm import sessionmaker\nengine = create_engine('sqlite://')\nfactory = sessionmaker(bind=engine)\n",
    )
    _write(tmp_path / "migrations" / "versions" / "demo.py", "def upgrade(op):\n    op.execute('select 1')\n")
    _write(tmp_path / "scripts" / "demo.py", "import psycopg\npsycopg.connect('postgresql://example')\n")

    violations = check_db_access_boundary(root=tmp_path, config_path=tmp_path / "db_access_boundary.yml")

    assert violations == []


@pytest.mark.parametrize(
    ("path", "source", "detected"),
    [
        (
            "aicrm_next/demo_context/api.py",
            "from sqlalchemy import create_engine\nengine = create_engine('sqlite://')\n",
            "sqlalchemy.create_engine",
        ),
        (
            "aicrm_next/demo_context/admin_pages.py",
            "import psycopg\nconn = psycopg.connect('postgresql://example')\n",
            "psycopg.connect",
        ),
        (
            "aicrm_next/demo_context/application.py",
            "from sqlalchemy.orm import sessionmaker\nfactory = sessionmaker()\n",
            "sqlalchemy.orm.sessionmaker",
        ),
    ],
)
def test_db_access_boundary_blocks_forbidden_layer_direct_db_access(
    tmp_path: Path, path: str, source: str, detected: str
) -> None:
    _write_config(tmp_path / "db_access_boundary.yml")
    _write(tmp_path / path, source)

    violations = check_db_access_boundary(root=tmp_path, config_path=tmp_path / "db_access_boundary.yml")

    assert len(violations) == 1
    violation = violations[0]
    assert violation.path.as_posix().endswith(path)
    assert violation.line == 2
    assert violation.rule == "db_access_boundary_violation"
    assert violation.detected_primitive == detected
    assert "repo.py/repository.py" in violation.suggestion
    assert "aicrm_next.shared.db_session" in violation.suggestion


def test_db_access_boundary_blocks_frontend_compat_raw_sql_execute(tmp_path: Path) -> None:
    _write_config(tmp_path / "db_access_boundary.yml")
    _write(tmp_path / "aicrm_next" / "app" / "admin_console" / "foo.py", "def run(conn):\n    conn.execute('SELECT 1')\n")

    violations = check_db_access_boundary(root=tmp_path, config_path=tmp_path / "db_access_boundary.yml")

    assert len(violations) == 1
    assert violations[0].path.as_posix().endswith("aicrm_next/app/admin_console/foo.py")
    assert violations[0].line == 2
    assert violations[0].rule == "db_access_boundary_violation"
    assert violations[0].detected_primitive == "db.execute"


def test_db_access_boundary_blocks_sqlalchemy_text_in_frontend_compat(tmp_path: Path) -> None:
    _write_config(tmp_path / "db_access_boundary.yml")
    _write(
        tmp_path / "aicrm_next" / "app" / "admin_console" / "foo.py",
        "from sqlalchemy import text\nquery = text('SELECT 1')\n",
    )

    violations = check_db_access_boundary(root=tmp_path, config_path=tmp_path / "db_access_boundary.yml")

    assert len(violations) == 1
    assert violations[0].detected_primitive == "sqlalchemy.text"


@pytest.mark.parametrize(
    ("source", "detected"),
    [
        ("import sqlalchemy as sa\nsa.create_engine('sqlite://')\n", "sqlalchemy.create_engine"),
        ("from sqlalchemy import create_engine as ce\nce('sqlite://')\n", "sqlalchemy.create_engine"),
        ("import sqlalchemy.orm as orm\norm.sessionmaker()\n", "sqlalchemy.orm.sessionmaker"),
        ("from sqlalchemy.orm import Session as SASession\nSASession()\n", "sqlalchemy.orm.Session"),
        ("import psycopg as pg\npg.connect('postgresql://example')\n", "psycopg.connect"),
        ("from psycopg import connect as pg_connect\npg_connect('postgresql://example')\n", "psycopg.connect"),
        ("import sqlite3 as sql\nsql.connect(':memory:')\n", "sqlite3.connect"),
    ],
)
def test_db_access_boundary_detects_aliases(tmp_path: Path, source: str, detected: str) -> None:
    _write_config(tmp_path / "db_access_boundary.yml")
    _write(tmp_path / "aicrm_next" / "demo_context" / "api.py", source)

    violations = check_db_access_boundary(root=tmp_path, config_path=tmp_path / "db_access_boundary.yml")

    assert len(violations) == 1
    assert violations[0].detected_primitive == detected


def test_db_access_boundary_allows_precise_temporary_allowlist(tmp_path: Path) -> None:
    allowlist = [
        {
            "path": "aicrm_next/demo_context/api.py",
            "rule": "db_access_boundary_violation",
            "owner": "demo_context",
            "reason": "Existing direct DB access predates checker.",
            "migration_target": "aicrm_next/demo_context/repo.py",
            "matches": ["engine = create_engine("],
        }
    ]
    _write_config(tmp_path / "db_access_boundary.yml", allowlist=allowlist)
    _write(
        tmp_path / "aicrm_next" / "demo_context" / "api.py",
        "from sqlalchemy import create_engine\nengine = create_engine(\n    'sqlite://'\n)\n",
    )

    violations = check_db_access_boundary(root=tmp_path, config_path=tmp_path / "db_access_boundary.yml")

    assert violations == []


@pytest.mark.parametrize(
    "bad_entry",
    [
        {
            "path": "aicrm_next/demo_context/api.py",
            "rule": "db_access_boundary_violation",
            "reason": "Existing direct DB access predates checker.",
            "migration_target": "aicrm_next/demo_context/repo.py",
            "matches": ["engine = create_engine("],
        },
        {
            "path": "aicrm_next/demo_context/api.py",
            "rule": "db_access_boundary_violation",
            "owner": "demo_context",
            "reason": "",
            "migration_target": "aicrm_next/demo_context/repo.py",
            "matches": ["engine = create_engine("],
        },
        {
            "path": "aicrm_next/demo_context/api.py",
            "rule": "db_access_boundary_violation",
            "owner": "demo_context",
            "reason": "Existing direct DB access predates checker.",
            "matches": ["engine = create_engine("],
        },
        {
            "path": "aicrm_next/demo_context/api.py",
            "rule": "db_access_boundary_violation",
            "owner": "demo_context",
            "reason": "Existing direct DB access predates checker.",
            "migration_target": "aicrm_next/demo_context/api.py",
            "matches": ["engine = create_engine("],
        },
        {
            "path": "aicrm_next/demo_context/**",
            "rule": "db_access_boundary_violation",
            "owner": "demo_context",
            "reason": "Existing direct DB access predates checker.",
            "migration_target": "aicrm_next/demo_context/repo.py",
            "matches": ["engine = create_engine("],
        },
        {
            "path": "aicrm_next/demo_context/api.py",
            "rule": "db_access_boundary_violation",
            "owner": "demo_context",
            "reason": "Existing direct DB access predates checker.",
            "migration_target": "aicrm_next/demo_context/repo.py",
            "matches": ["create_engine"],
        },
    ],
)
def test_db_access_boundary_rejects_imprecise_allowlist(tmp_path: Path, bad_entry: dict) -> None:
    _write_config(tmp_path / "db_access_boundary.yml", allowlist=[bad_entry])

    with pytest.raises(ValueError):
        load_config(tmp_path / "db_access_boundary.yml")


def test_db_access_boundary_blocks_unmatched_call_in_allowlisted_file(tmp_path: Path) -> None:
    allowlist = [
        {
            "path": "aicrm_next/demo_context/api.py",
            "rule": "db_access_boundary_violation",
            "owner": "demo_context",
            "reason": "Existing direct DB access predates checker.",
            "migration_target": "aicrm_next/demo_context/repo.py",
            "matches": ["engine = create_engine("],
        }
    ]
    _write_config(tmp_path / "db_access_boundary.yml", allowlist=allowlist)
    _write(
        tmp_path / "aicrm_next" / "demo_context" / "api.py",
        "from sqlalchemy import create_engine\nimport psycopg\nengine = create_engine(\n    'sqlite://'\n)\npsycopg.connect('postgresql://example')\n",
    )

    violations = check_db_access_boundary(root=tmp_path, config_path=tmp_path / "db_access_boundary.yml")

    assert len(violations) == 1
    assert violations[0].detected_primitive == "psycopg.connect"


def test_db_access_boundary_current_repository_passes() -> None:
    violations = check_db_access_boundary()

    assert violations == []


def test_db_access_boundary_no_longer_allowlists_commerce_admin_db_helpers() -> None:
    config = load_config(Path("docs/architecture/db_access_boundary.yml"))

    allowlisted_paths = {entry["path"] for entry in config["temporary_allowlist"]}

    assert "aicrm_next/extensions/commerce/commerce/admin_refunds.py" not in allowlisted_paths
    assert "aicrm_next/extensions/commerce/commerce/admin_transaction_detail.py" not in allowlisted_paths
    assert "aicrm_next/extensions/commerce/commerce/admin_transactions.py" not in allowlisted_paths
    assert "aicrm_next/extensions/commerce/commerce/admin_webhooks.py" not in allowlisted_paths
    assert "aicrm_next/extensions/commerce/commerce/external_push_admin.py" not in allowlisted_paths
    assert "aicrm_next/extensions/commerce/commerce/wechat_shop_service.py" not in allowlisted_paths


def test_db_access_boundary_no_longer_allowlists_public_product_h5_wechat_pay() -> None:
    config = load_config(Path("docs/architecture/db_access_boundary.yml"))

    allowlisted_paths = {entry["path"] for entry in config["temporary_allowlist"]}
    source = Path("aicrm_next/extensions/commerce/public_product/h5_wechat_pay.py").read_text(encoding="utf-8")

    assert "aicrm_next/extensions/commerce/public_product/h5_wechat_pay.py" not in allowlisted_paths
    assert "psycopg.connect" not in source


def test_db_access_boundary_no_longer_allowlists_background_jobs_or_payment_events() -> None:
    config = load_config(Path("docs/architecture/db_access_boundary.yml"))

    allowlisted_paths = {entry["path"] for entry in config["temporary_allowlist"]}
    background_db_source = Path("aicrm_next/background_jobs/db.py").read_text(encoding="utf-8")
    payment_source = Path("aicrm_next/platform_foundation/internal_events/payment.py").read_text(encoding="utf-8")

    assert "aicrm_next/background_jobs/db.py" not in allowlisted_paths
    assert "aicrm_next/platform_foundation/internal_events/payment.py" not in allowlisted_paths
    assert "psycopg.connect" not in background_db_source
    assert "psycopg.connect" not in payment_source


def test_db_access_boundary_no_longer_allowlists_channel_archive_or_operation_members() -> None:
    config = load_config(Path("docs/architecture/db_access_boundary.yml"))

    allowlisted_paths = {entry["path"] for entry in config["temporary_allowlist"]}
    channels_source = Path("aicrm_next/automation_engine/channels_api.py").read_text(encoding="utf-8")
    archive_source = Path("aicrm_next/extensions/archive/message_archive/sync_service.py").read_text(encoding="utf-8")
    operation_members_source = Path("aicrm_next/common_operation_members.py").read_text(encoding="utf-8")

    assert "aicrm_next/automation_engine/channels_api.py" not in allowlisted_paths
    assert "aicrm_next/extensions/archive/message_archive/sync_service.py" not in allowlisted_paths
    assert "aicrm_next/common_operation_members.py" not in allowlisted_paths
    assert "psycopg.connect" not in channels_source
    assert "psycopg.connect" not in archive_source
    assert "psycopg.connect" not in operation_members_source


def test_db_access_boundary_temporary_allowlist_is_empty() -> None:
    config = load_config(Path("docs/architecture/db_access_boundary.yml"))

    assert config["temporary_allowlist"] == []
    for path in (
        "aicrm_next/extensions/growth/cloud_orchestrator/campaigns_read.py",
        "aicrm_next/extensions/hxc/hxc_dashboard/postgres_repo.py",
        "aicrm_next/media_library/postgres_repo.py",
        "aicrm_next/shared/postgres_connection.py",
    ):
        source = Path(path).read_text(encoding="utf-8")
        assert "psycopg.connect" not in source
