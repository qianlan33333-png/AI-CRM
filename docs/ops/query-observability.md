# Query observability

AI-CRM Next emits one structured `aicrm_request_query_summary` log event for
every request matched to a governed route. The event is intended to connect a
route and capability to its database cost without exposing request or customer
data.

## Event fields

- `method`, `route`, and `route_name`: the checked-in FastAPI route template,
  never the requested URL or path parameter values.
- `capability`: the governed route capability.
- `status_code` and `request_duration_ms`: the response status and total
  application duration.
- `query_count`, `failed_query_count`, and `query_duration_ms`: SQL calls seen
  by SQLAlchemy and the shared psycopg connection wrappers, plus driver execute
  time. Fetch and response serialization time remain part of request duration.
- `query_fingerprints`: at most 32 per-request SHA-256 prefixes of normalized
  statement shapes with their call counts. String, numeric, dollar-quoted, and
  comment literals are discarded before hashing. `query_fingerprint_overflow_count`
  records additional unique shapes.

The observer hashes SQL statement shapes in memory and discards the statement.
It never receives or logs binding values. Logs must not add raw SQL, query
parameters, URL parameters, headers, cookies, request bodies, phone numbers,
external_userid, unionid, or job payloads.

## Coverage and interpretation

The request summary covers SQLAlchemy engines and the shared psycopg wrappers
used by online AI-CRM Next routes. One-off maintenance code and direct database
connections outside those wrappers are not attributed to a request. PostgreSQL
`pg_stat_statements`, once separately enabled in an approved maintenance
window, remains the authority for database-wide calls, total execution time,
I/O, and row statistics.

For the seven-day baseline, aggregate application events by route, capability,
and fingerprint. Rank by total request time, p95 request time, call count,
query count, and failed query count. Join this operational ranking to
`pg_stat_statements` using reviewed source queries and plans; do not publish or
persist SQL text in application telemetry.

## Runtime cost and rollback

The request-local observer performs no database writes and uses no global
request registry. It stores at most 32 short fingerprints per request. Rolling
back the application removes the middleware and engine listeners; no database
or log migration is required.
