# External Effects Boundary

`tools/check_external_effects_boundary.py` is a static architecture guardrail for
AI-CRM Next. It blocks business contexts from adding direct `requests` or `httpx`
network calls unless those calls sit behind the external effects platform or an
integration gateway adapter.

This checker does not approve real external calls. Runtime approval is still
controlled by feature flags, adapter mode, test-only gates, fake/staging-disabled
configuration, and explicit business approval.

## Allowed Boundaries

Direct HTTP client usage is allowed only in:

- `aicrm_next/platform/platform_foundation/external_effects/**`
- `aicrm_next/channels/integration_gateway/**`
- `historical removed reference (http_client.py)`, if present
- `tests/**`, `tools/**`, and `scripts/**`

Business context files such as `api.py`, `application.py`, `service.py`,
`admin_pages.py`, `routes.py`, `repo.py`, and other context modules must move
external effects behind `aicrm_next.channels.integration_gateway` or
`aicrm_next.platform.platform_foundation.external_effects`.

## Temporary Allowlist

`docs/architecture/external_effects_registry.yml` contains the temporary
allowlist for historical direct clients. Each allowlist entry must be exact:
`path`, `rule`, `owner`, `effect_key`, `reason`, `migration_target`, and
`matches` are required. Directory-level and broad `requests` / `httpx` matches
are forbidden.

The current historical entries are not a long-term approval:

- `aicrm_next/channels/channel_entry/wecom_adapter.py`
- `aicrm_next/extensions/commerce/commerce/wechat_pay_client.py`
- `aicrm_next/extensions/commerce/commerce/wechat_shop_client.py`

The next migration step is to move these clients behind integration gateway or
external effects boundaries without changing route behavior in this checker PR.

## Rollback

The checker is not used by runtime code. If it blocks an urgent fix, rollback by
removing it from `scripts/ci/run_architecture_gates.sh` or reverting the checker
PR. Do not restore production compatibility fallbacks or enable real external
calls as part of checker rollback.
