# Monitoring frontend retirement

AI-CRM does not expose standalone frontend pages for queue, event, webhook,
data-health, lineage, or orchestration monitoring. Production operations query
the authenticated JSON APIs and canonical PostgreSQL data directly.

## Retired frontend surfaces

- Group invite library, Jobs, Broadcast Jobs, Push Center, Internal Events, and
  Webhook Inbox pages.
- Data Health, Data Quality, Delivery Lineage, and Growth Orchestration pages.
- Dashboard statistics, pending-item cards, and the synthetic shell-health
  polling endpoint.
- The unused P1 status-card JavaScript implementation and shared execution-page
  assets.

These routes and assets are physically absent. They must not be reintroduced as
hidden navigation, compatibility redirects, or alternate monitoring pages.

## Preserved backend contracts

The retirement does not remove the durable data or action boundary:

- `/api/admin/push-center/*` reads canonical external-effect jobs and attempts;
  retry and cancel remain authenticated durable commands.
- `/api/admin/internal-events/*` reads internal events and consumer runs; manual
  run, retry, and skip remain authenticated durable commands.
- `/api/admin/webhook-inbox/*` reads durable callback inbox rows; preview,
  dispatch, retry, skip, and run-due remain authenticated command boundaries.
- `/api/admin/jobs/*` and `/api/admin/broadcast-jobs/*` retain real job data and
  required approval, cancellation, notification, archive-sync, and batch actions.
- `/api/admin/data-health/*` retains database-backed health checks.
- `/api/admin/delivery-lineage/*` retains delivery lineage and reconciliation
  reads.
- `/api/admin/group-invite-library/*` retains group-link compatibility storage
  and CRUD for authorized clients.

The removed `/api/admin/data-quality/*` registry was not database monitoring: it
only returned static metadata and a snapshot contract that explicitly reported
no database probe and no persistence. Growth Orchestration was also removed
because it was a duplicate aggregation of existing campaign, audience, group-op,
and cloud-plan projections without an owned durable model.

## Product boundary

Direct-use pages for configuration, customer work, campaigns, commerce, media,
questionnaires, and other business actions remain. New operational visibility
should extend the canonical APIs, database projections, logs, or AI operations
tooling instead of adding a frontend monitoring page.
