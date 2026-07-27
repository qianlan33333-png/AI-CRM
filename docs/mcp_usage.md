# CRM MCP Usage

This is the current minimal MCP contract for OpenClaw or another MCP-capable
assistant. It exposes stable CRM actions through `GET /mcp` and `POST /mcp`
using a registered MCP client and short-lived JWT.

## Boundary

- MCP is a CRM capability facade, not a scheduler.
- It may read customer context and create CRM tasks.
- It must not bypass CRM approval for real outbound sends.
- OpenClaw integration must go through `aicrm_next.channels.integration_gateway`.
- Deleted historical OpenClaw source paths must not be reintroduced.

## Authentication

```http
Authorization: Bearer <short-lived-client-credentials-jwt>
```

The caller must use the dedicated `mcp` client with `audience=external_integration`; `GET` requires `read`/`mcp_read`, while `POST` requires `write`/`mcp_execute`. Token exchange and rotation follow [`auth_client_credentials.md`](auth_client_credentials.md). Missing, invalid, or expired JWTs return `401`; scope or capability violations return `403`. Shared Bearer and fallback credentials are not accepted.

## Customer Reference Rules

Customer tools accept `customer_ref` or `external_userid`.

- `external_userid` wins when supplied.
- A phone-like `customer_ref` is resolved through existing CRM identity logic.
- Failed resolution returns an explicit error instead of a partial success.

Common errors:

- `customer_ref or external_userid is required`
- `customer not found for mobile: <mobile>`
- `customer not found`

## Core Read Methods

- `resolve_customer`: returns the canonical customer object.
- `get_customer_context`: returns customer, recent messages, timeline events,
  `source_status`, `degraded`, and warnings.
- `get_contact`: returns the contact view for one customer.
- `get_recent_messages`: returns recent messages for one customer.
- `get_owner_recent_chat_dump`: groups recent private and group messages by
  owner and time window; OpenClaw decides priority outside CRM.

Stable legacy read routes for OpenClaw are:

- `GET /api/customers`
- `GET /api/customers/<external_userid>`
- `GET /api/customers/<external_userid>/timeline`
- `GET /api/messages/<external_userid>/recent`

Callers should rely on identifiers, message content, timestamps, tags, owner
ids, timeline item metadata, and `source_status`; optional display fields are
best effort.

## Write-Like Task Methods

- `update_customer_tags`: add or remove tags for one customer.
- `create_private_message_task`: create a private-message task.
- `create_group_message_task`: create a group-message task.
- `create_moment_task`: create a moment task.

These create CRM-side records or requests. They do not authorize an assistant to
perform unrestricted live external sends.

## Compatibility Methods

The MCP still exposes older message/search/tag/batch helpers for compatibility.
New OpenClaw flows should prefer:

1. `resolve_customer`
2. `get_customer_context`
3. `get_owner_recent_chat_dump`
4. `update_customer_tags`
5. task-creation methods when CRM follow-up is needed

## Recommended Flow

For a phone-number request:

1. Resolve customer.
2. Read customer context.
3. Decide outside CRM.
4. Update tags or create a CRM task.

For hourly owner review:

1. Call `get_owner_recent_chat_dump(owner_userid, lookback_minutes=60)`.
2. Let OpenClaw rank candidates.
3. Fetch context for selected customers.
4. Create follow-up tasks only when needed.
