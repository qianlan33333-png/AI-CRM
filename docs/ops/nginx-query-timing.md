# Nginx query timing observability

AI-CRM uses a dedicated Nginx JSON log format to measure request total time and
upstream connection/response time. Application telemetry remains responsible
for governed route templates, capabilities, SQL counts, and query fingerprints.

## Privacy boundary

The timing event contains only timestamp, request ID, HTTP method/status, total
request time, and upstream timing/status. It must not contain the requested URL
or path, query arguments, client IP, user identity, headers, cookies, request
body, phone number, external_userid, unionid, raw SQL, or binding values.

The checked-in template deliberately omits `access_log`. The private operations
handoff selects the destination and retention policy, then activates the format
exactly once in the reviewed production server block. This avoids publishing
production paths and prevents an unreviewed checkout from changing live logs.

## Maintenance-window gate

Activation requires a separately approved low-traffic window. Before applying
it, the operator snapshots the effective Nginx configuration and records the
rollback source. The candidate configuration must pass Nginx syntax validation
and the repository's read-only checker. After a reload, run health and signed
read-only sidebar probes, confirm the timing log is parseable, and observe 499,
5xx, connection count, and upstream latency for 20 minutes.

If validation or observation fails, restore the configuration snapshot and
reload Nginx. This slice does not change PostgreSQL settings, connection pool
sizes, application timeouts, data, events, or external effects.

## Read-only checker

`scripts/ops/check_nginx_query_timing_config.py` accepts a reviewed config or
effective dump. It reports whether the named format exists, all required timing
variables are present, identifying request variables are absent, and an active
`access_log` references the format. It never prints config contents or values.

For the seven-day baseline, aggregate these events by HTTP status and upstream
latency, then correlate the time window with application route/capability events.
Do not copy raw access logs into issues, pull requests, or application telemetry.
