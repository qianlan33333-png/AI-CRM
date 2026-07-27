# DB / Session Access Boundary

`tools/check_db_access_boundary.py` is a static architecture guardrail for
AI-CRM Next. It prevents API, admin page, frontend compatibility, application,
service, and other business modules from adding direct database/session
primitives outside repository or shared DB session boundaries.

This checker does not change runtime database behavior, does not run migrations,
and does not approve production database access. Runtime safety remains governed
by environment configuration, deployment approval, migrations policy, and
existing DB/session providers.

## Allowed Boundaries

Direct DB/session primitives are allowed only in:

- `aicrm_next/platform/shared/db_session.py`
- `aicrm_next/**/repo.py`
- `aicrm_next/**/repository.py`
- `aicrm_next/**/repositories.py`
- `migrations/**`
- `scripts/**`
- `tests/**`
- `tools/**`

API, route, admin page, frontend compatibility, application, service, and other
business context files must move DB access behind a repository or
`aicrm_next.platform.shared.db_session`.

## Temporary Allowlist

`docs/architecture/db_access_boundary.yml` contains the temporary allowlist for
historical direct DB usage, if any. Each allowlist entry must be exact:
`path`, `rule`, `owner`, `reason`, `migration_target`, and `matches` are
required. Directory-level entries and broad matches such as `execute`,
`connect`, `create_engine`, or `SELECT` are forbidden.

Allowlist entries are migration debt, not a permanent approval. New DB/session
usage in business modules must be moved into repository/shared DB session
boundaries instead of broadening the allowlist.

## Rollback

The checker is not used by runtime code. If it blocks an urgent fix, rollback by
removing it from `scripts/ci/run_architecture_gates.sh` or reverting the checker
PR. Do not run production migrations, change deploy/systemd/nginx/env, or add
runtime DB fallback behavior as part of checker rollback.
