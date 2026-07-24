# PostgreSQL Connection Pool Runbook

## Incident Background

Production hit PostgreSQL `max_connections` pressure. `pg_stat_activity` showed `openclaw` / `openclaw_wecom` sessions in `idle` and `idle in transaction`, and connections dropped after application restart.

This failure mode is an application connection lifecycle and pool governance issue. Increasing database capacity alone does not fix sessions that are not closed, long implicit transactions, or unbounded per-request engine creation.

## Current Application Mechanism

`aicrm_next/shared/db_session.py` is the single SQLAlchemy Engine and SessionFactory owner for `aicrm_next` runtime code.

- `get_engine()` returns a process-level shared Engine.
- `get_session_factory()` reuses the shared Engine.
- `get_db()` is the FastAPI request-scope dependency and rolls back/closes the Session after the request.
- `session_scope()` rolls back/closes its Session in `finally`.
- Repository `close()` rolls back/closes the owned Session and must not dispose the global Engine.
- Engine initialization logs safe pool settings once per Engine cache miss and never logs the full DSN or password.

Supported environment variables:

- `DB_POOL_SIZE`
- `DB_MAX_OVERFLOW`
- `DB_POOL_TIMEOUT`
- `DB_POOL_RECYCLE`
- `DB_APPLICATION_NAME`
- `PGAPPNAME`

Recommended production defaults:

```bash
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=0
DB_POOL_TIMEOUT=5
DB_POOL_RECYCLE=1800
DB_APPLICATION_NAME=aicrm-next-web
PGAPPNAME=aicrm-next-web
```

`DB_APPLICATION_NAME` labels SQLAlchemy engines. `PGAPPNAME` is the matching
libpq fallback for direct psycopg connections inherited by the same systemd
service. Production runtime units must declare both values identically. The
checked-in runtime manifest owns one unique label for Web, callback ingress,
each persistent queue consumer, and each active scheduled job. Queue listener
connections append `-listener` to their service label, so they remain
attributable without recording queue payloads, customer identifiers, SQL text,
or binding values.

The deployment validator rejects missing, duplicate, mismatched, or unsafe
labels before installing a unit. Application names are operational constants;
never derive them from a request, external_userid, unionid, phone number, job
payload, or another business value.

Connection budget:

```text
web_worker_count * (DB_POOL_SIZE + DB_MAX_OVERFLOW)
```

Example with 4 workers:

```text
4 * (5 + 0) = 20
```

Keep this below the PostgreSQL ordinary business connection budget, leaving room for admin sessions, workers, and maintenance tasks.

## Role-Level Guardrails

Do not put the following SQL in an automatic migration. Run it manually only after confirming the business has no legitimate long transaction or long query path for the role.

```sql
ALTER ROLE openclaw SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE openclaw SET statement_timeout = '30s';
```

`idle_in_transaction_session_timeout` terminates abnormal sessions stuck idle inside a transaction. `statement_timeout` limits unexpectedly slow queries. These are guardrails, not substitutes for correct Session close.

## Diagnostic SQL

Use `docs/ops/sql/postgres_connection_diagnostics.sql` from `psql`. It includes:

- Connection count grouped by `application_name`, `usename`, `datname`, and `state`.
- `idle in transaction` sessions older than 60 seconds.
- Longest active transactions.
- Current database connection count with `max_connections`.
- Connection source aggregation by `client_addr` and `application_name`.
- Idle sessions older than 5 minutes for diagnosis only.

## Alerting Suggestions

- Business connection count over 70% of budget: warning.
- Business connection count over 85% of budget: critical.
- `idle in transaction > 0` lasting 60 seconds: warning.
- Any `too many clients already`: critical.
- Any SQLAlchemy pool timeout in application logs: warning.

Useful log keywords:

```text
too many clients already
QueuePool limit
PoolTimeout
timeout waiting for connection
```

## PgBouncer / RDS Proxy Guidance

PgBouncer or cloud RDS Proxy is not the first required fix for this incident. The first fixes are:

1. Shared process-level Engine and SessionFactory.
2. Request-scope Session close.
3. Repository compatibility close paths.
4. SQL pagination for high-frequency list APIs.

If worker count grows or connection spikes remain noisy, add PgBouncer transaction pooling or the cloud provider's RDS Proxy. These tools smooth bursts and protect PostgreSQL, but they cannot replace Session close and cannot fix `idle in transaction` caused by application ownership bugs.

## Manual Load Check

Run a 20-minute steady request loop against:

- `/api/customers`
- `/api/sidebar/customer-context`
- `/api/sidebar/v2/questionnaires`
- `/api/sidebar/v2/orders`

In parallel, run `docs/ops/sql/postgres_connection_diagnostics.sql` every 1-2 minutes.

Acceptance criteria:

- `openclaw` connections do not grow monotonically with request count.
- `idle in transaction` older than 60 seconds remains 0.
- After stopping the load, connections return to the stable idle pool size.
- No `too many clients already` appears.
- Application logs do not show SQLAlchemy pool timeout.

Example loop:

```bash
BASE_URL="https://your-aicrm-host"
EXTERNAL_USERID="replace-with-real-external-userid"

end=$((SECONDS + 1200))
while [ "$SECONDS" -lt "$end" ]; do
  curl -fsS "$BASE_URL/api/customers?limit=50&offset=0" >/dev/null
  curl -fsS "$BASE_URL/api/sidebar/customer-context?external_userid=$EXTERNAL_USERID" >/dev/null
  curl -fsS "$BASE_URL/api/sidebar/v2/questionnaires?external_userid=$EXTERNAL_USERID" >/dev/null
  curl -fsS "$BASE_URL/api/sidebar/v2/orders?external_userid=$EXTERNAL_USERID" >/dev/null
  sleep 1
done
```

## Optional Index Review

Do not add production indexes blindly during incident response. After collecting `EXPLAIN (ANALYZE, BUFFERS)` for the customer list and sidebar v2 queries, consider indexes on heavily filtered columns such as:

- `customer_list_index_next(owner_userid, id)`
- `customer_list_index_next(updated_at)`
- source tables used by live fallback: `contacts(external_userid)`, `archived_messages(external_userid, send_time)`, and binding/identity external user id columns.
