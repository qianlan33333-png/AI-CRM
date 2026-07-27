#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.crm.customer_read_model.backfill import (  # noqa: E402
    CustomerReadModelBackfillService,
    FixtureCustomerReadModelSource,
    JsonFileCustomerReadModelSource,
)
from aicrm_next.crm.customer_read_model.models import metadata as customer_read_model_metadata  # noqa: E402
from aicrm_next.crm.customer_read_model.repo import SqlAlchemyCustomerReadModelRepository  # noqa: E402


def _normalize_sqlalchemy_url(database_url: str) -> str:
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url[len("postgres://") :]
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url[len("postgresql://") :]
    return database_url


def _safe_execute_database_url(database_url: str) -> tuple[bool, str]:
    parsed = urlparse(database_url)
    scheme = parsed.scheme.lower()
    if scheme == "sqlite":
        if parsed.path in {"", "/:memory:"} or database_url.endswith(":memory:"):
            return True, ""
        sqlite_path = parsed.path
        if sqlite_path.startswith("//tmp/"):
            sqlite_path = sqlite_path[1:]
        if Path(sqlite_path).is_absolute() and str(Path(sqlite_path)).startswith("/tmp/"):
            return True, ""
        return False, "sqlite execute database must be an in-memory database or an absolute /tmp path"
    if scheme in {"postgres", "postgresql", "postgresql+psycopg"}:
        host = (parsed.hostname or "").lower()
        db_name = Path(parsed.path or "").name.lower()
        if host in {"localhost", "127.0.0.1", "::1"} and any(marker in db_name for marker in ("test", "tmp")):
            return True, ""
        return False, "postgres execute database must be local and named as a test/tmp database"
    return False, f"unsupported execute database scheme: {parsed.scheme or '<empty>'}"


def _build_sqlalchemy_target_repo(database_url: str) -> SqlAlchemyCustomerReadModelRepository:
    engine = create_engine(_normalize_sqlalchemy_url(database_url), future=True)
    if urlparse(database_url).scheme.lower() == "sqlite":
        customer_read_model_metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, future=True)
    return SqlAlchemyCustomerReadModelRepository(session_factory())


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill the Next-native customer read model.")
    parser.add_argument("--execute", action="store_true", help="Write to the configured read model repository. Defaults to dry-run.")
    parser.add_argument("--allow-execute", action="store_true", help="Required with --execute to acknowledge writes to an explicit test database.")
    parser.add_argument("--database-url", default="", help="Explicit test database URL for --execute. Dry-run does not require this.")
    parser.add_argument("--repo-backend", choices=["sqlalchemy"], default="sqlalchemy")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--external-userid", action="append", default=[])
    parser.add_argument("--source", choices=["fixture", "file-json"], default="fixture")
    parser.add_argument("--json-file", default="", help="Input JSON file when --source=file-json.")
    args = parser.parse_args()

    if args.execute:
        if not args.allow_execute:
            print(json.dumps({"ok": False, "error": "--execute requires --allow-execute"}, ensure_ascii=False), file=sys.stderr)
            return 2
        if not str(args.database_url or "").strip():
            print(json.dumps({"ok": False, "error": "--execute requires explicit --database-url"}, ensure_ascii=False), file=sys.stderr)
            return 2
        ok, reason = _safe_execute_database_url(args.database_url)
        if not ok:
            print(json.dumps({"ok": False, "error": reason}, ensure_ascii=False), file=sys.stderr)
            return 2

    if args.source == "file-json":
        if not args.json_file:
            print(json.dumps({"ok": False, "error": "--source=file-json requires --json-file"}, ensure_ascii=False), file=sys.stderr)
            return 2
        source = JsonFileCustomerReadModelSource(args.json_file)
    else:
        source = FixtureCustomerReadModelSource()

    target_repo = _build_sqlalchemy_target_repo(args.database_url) if args.execute else None
    result = CustomerReadModelBackfillService(source=source, target_repo=target_repo).run(
        dry_run=not bool(args.execute),
        limit=args.limit,
        external_userids=args.external_userid,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
