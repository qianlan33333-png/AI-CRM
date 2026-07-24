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

## Seven-day aggregate

`scripts/ops/summarize_query_observability.py` consumes journal JSON from stdin
and emits one aggregate JSON document. It never echoes source log lines or
unknown fields. Route, route name, method, and capability must exactly match a
checked-in entry in `docs/architecture/route_ownership_manifest.yml`; this
prevents an accidental requested path or customer identifier from becoming an
aggregate key. Fingerprints must be 16 lowercase hexadecimal characters and
their call counts must reconcile to the request query count.

Collect slightly more than seven days so the first and last accepted events can
span the required 168 hours:

```bash
sudo journalctl \
  -u openclaw-wecom-postgres.service \
  --since "8 days ago" \
  --output=json \
  --no-pager \
| /home/ubuntu/venvs/openclaw/bin/python \
    scripts/ops/summarize_query_observability.py \
    --require-window-hours 168 \
    --require-active-days 7 \
    --fingerprint-route-limit 20 \
    --top 200 \
> /operator/selected/aicrm-query-baseline.json
```

Use an operator-selected protected path for the aggregate output. Do not save
the raw journal stream. The command exits with status 2 when there are no valid
events, any accepted event lacks a journal timestamp, or the observed window is
shorter than requested. `--require-active-days 7` additionally requires valid
events on at least seven distinct UTC dates. Rejected event counts are reported
without rejected values or reasons.

The route ranking is ordered by total request duration, then p95, then request
count. The fingerprint ranking contains only call count, request count, and
checked-in route constants; request telemetry cannot safely attribute database
execution time to one fingerprint. Each fingerprint reports its total route
count but includes at most the configured number of route constants, plus an
overflow count, so common infrastructure queries cannot make the report grow
without bound. After `pg_stat_statements` is separately enabled, use its
database-wide execution and row statistics as the authority for that part of
the ranking.

## Runtime cost and rollback

The request-local observer performs no database writes and uses no global
request registry. It stores at most 32 short fingerprints per request. Rolling
back the application removes the middleware and engine listeners; no database
or log migration is required.
