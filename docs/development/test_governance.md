# Current AI-CRM Test Governance

## Scope

The test system is derived only from the current AI-CRM Next source tree,
current route ownership manifest, current migration graph, and current delivery
workflows. Historical pull requests, retired SHAs, deleted files, and migration
implementation strings are not test requirements.

`docs/ci/current_behavior_inventory.json` is the behavior-to-test index. Every
entry identifies its current owner, source paths, route groups, execution layers,
and allowed side effects. Route details remain authoritative in
`docs/architecture/route_ownership_manifest.yml`; architecture rules remain in
their existing checker inputs.

## Layers

- `tests/unit`: pure rules and application decisions; no app boot or database.
- `tests/contracts`: current route, ownership, auth, manifest, selector, and
  architecture contracts.
- `tests/postgres`: PostgreSQL 16 migrations, schema, repositories,
  transactions, and concurrency.
- `tests/high_risk`: authentication, payment, WeCom callbacks, external effects,
  idempotency, and recovery. Network access is blocked and providers are fake.
- `tests/release`: startup, migration head, runtime owner, exact SHA, health,
  deployment lock, and rollback contracts.
- `tests/frontend`: Node tests for current shared JavaScript behavior and page
  wiring.

The suite may contain at most 160 Python test files and 35,000 Python test lines.
Parameterized cases are not limited. The root `tests/conftest.py` must remain
lightweight and may not import the application or define autouse fixtures.

## Developer workflow

Run `make bootstrap-test` once to create `.venv` with
`/usr/local/bin/python3.10` and the hashed lock file. Run `make preflight` before
opening a pull request. Preflight selects unit, contract, and frontend tests from
`origin/main...working-tree`, runs syntax and architecture checks, validates the
manifest, performs `git diff --check`, and fails if any tracked or untracked
source file changes while tests execute.

Local preflight never starts PostgreSQL and never runs `postgres`, `high_risk`,
or `release`. Its output is development evidence only, not production evidence.

## Cloud workflow

`CI Fast` owns one required job named `ci-fast-result`. Pull requests are
classified as `fast` or `high_risk`; unknown runtime paths and deleted files fail
closed to `high_risk`. Main pushes run only the release gate. Full regression is
available only through `workflow_dispatch` or `workflow_call` and has no
schedule. The deployment promotion workflow still consumes the successful
`CI Fast` main-push SHA and passes that exact SHA to the existing production
deployment workflow.

Production data and real provider calls are forbidden in every test layer.
PostgreSQL tests accept only a local host and a database name containing `test`.

## Rollback

Revert the complete test-system replacement commit/PR. Git history restores the
previous tests and CI assets; no in-tree archive is maintained. Production
runtime rollback remains the existing previous-release transaction in the
deployment workflow.
