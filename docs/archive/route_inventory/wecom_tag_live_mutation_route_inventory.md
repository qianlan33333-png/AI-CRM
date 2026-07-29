# WeCom Live Tag Mutation Route Inventory

Scope: Legacy Exit group 14 closeout locks live gate/mark/unmark and tag mutation callers to Next CommandBus handling. R09 supersedes the former questionnaire exception: H5 persists a durable `questionnaire.submitted` continuation, `questionnaire_tag_consumer` plans the External Effect, and only the worker may call WeCom and project local CRM tags after success. Tag catalog CRUD/sync stays deletion_locked from group 13.

Closeout status: live gate, live mark, and live unmark are `deletion_locked` / `locked` in both the route registry and production route ownership manifest. Their `legacy_fallback_allowed` flags are false, and production_compat must not register these routes.

## Caller ↔ API ↔ CommandBus ↔ SideEffectPlan Matrix

| Caller | Caller file | Current API | Payload | Target external_userid | tag_ids | CommandBus command | SideEffectPlan effect_type | Real WeCom | Smoke / test |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Live gate smoke | curl / admin diagnostics | `GET /api/admin/wecom/tags/live/gate` | none | none | none | none | none | no submit-time call; `adapter_mode=local_projection_and_external_effect`, `route_owner=ai_crm_next`, `fallback_used=false` | registry/manifest locked; smoke + `tests/test_wecom_tag_live_mutation_commands.py` |
| Admin live mark | API client / smoke | `POST /api/admin/wecom/tags/live/mark` | `external_userid`, `tag_ids`, `operator`, `Idempotency-Key` | `external_userid` | request `tag_ids` | `PlanWeComTagMarkCommand` | `wecom.tag.mark` | no; `real_external_call_executed=false`, `wecom_api_called=false` | registry/manifest locked; smoke + `tests/test_wecom_tag_live_mutation_commands.py` |
| Admin live unmark | API client / smoke | `POST /api/admin/wecom/tags/live/unmark` | `external_userid`, `tag_ids`, `operator`, `Idempotency-Key` | `external_userid` | request `tag_ids` | `PlanWeComTagUnmarkCommand` | `wecom.tag.unmark` | no; `real_external_call_executed=false`, `wecom_api_called=false` | registry/manifest locked; smoke + `tests/test_wecom_tag_live_mutation_commands.py` |
| Sidebar signup tag marker | `aicrm_next/sidebar_write/api.py`, `aicrm_next/sidebar_write/application.py` | `POST /api/sidebar/signup-tags/mark` | `external_userid`, `tag_id` or `tag_name`, `marked` | `external_userid` | `tag_id` when present | `MarkSignupTagCommand` | `wecom.tag.update` | no; existing plan-only sidebar CommandBus | locked sidebar route; smoke + `tests/test_wecom_tag_live_mutation_callers_contract.py` |
| Questionnaire H5 submit tag apply | `aicrm_next/questionnaire/h5_write.py` | `POST /api/h5/questionnaires/{slug}/submit` | answers + identity; final tags derived by scoring | authoritative identity reloaded by the consumer | `final_tags` | `questionnaire.h5.submit` | `questionnaire.tag.apply` | no request-path call; `adapter_mode=durable_internal_event`, then `questionnaire_tag_consumer` plans one External Effect and the worker performs post-success projection | locked H5 submit route; smoke + `tests/test_questionnaire_h5_final_tags_real_wecom.py` |
| Questionnaire admin write tag selector | `aicrm_next/questionnaire/templates/admin_questionnaires.html` | `GET /api/admin/wecom/tags` selector only | none for mutation | none | none | none | none | no mutation route; read CRUD remains deletion_locked | `tests/test_wecom_tag_read_selectors.py` |
| Customer profile tag read/assignment boundary | `aicrm_next/customer_read_model/api.py`, `aicrm_next/customer_tags/live_mutation.py` | `GET /api/admin/customers/profile/tags`; command-only assignment boundary | `external_userid`, `tag_ids` for assignment command | `external_userid` | command `tag_ids` | `PlanCustomerTagAssignmentCommand` | `wecom.tag.assignment.apply` | no; command-only plan boundary until a write API is approved | no approved write API in this group; `tests/test_wecom_tag_live_mutation_callers_contract.py` |
| User ops tag filter | `aicrm_next/automation/ops_enrollment/api.py` | customer/user ops reads and previews | filters only | none | none | none | none | no mutation caller found | explicitly no mutation API in this group; `tests/test_wecom_tag_live_mutation_inventory.py` |
| Automation questionnaire result | `QuestionnaireSubmitSideEffectGateway.emit_automation_questionnaire_result` | internal gateway call from submit | questionnaire/submission/final_tags | submission `external_userid` | `final_tags` as automation context | none in this group | none in this group | automation runtime remains explicitly out of scope | explicitly no automation runtime mutation in this group |

## Inventory

1. `aicrm_next/customer_tags/api.py` owns `live/gate`, `live/mark`, and `live/unmark`.
2. `aicrm_next/customer_tags/mutation_commands.py` defines `PlanWeComTagMarkCommand`, `PlanWeComTagUnmarkCommand`, `PlanCustomerTagAssignmentCommand`, and the retired questionnaire planning command kept for non-submit compatibility.
3. `aicrm_next/customer_tags/live_mutation.py` owns CommandBus dispatch, idempotency reuse, AuditLedger writes, and SideEffectPlan creation.
4. `aicrm_next/sidebar_write/application.py` already records sidebar signup tag mutation as `wecom.tag.update` SideEffectPlan only.
5. `aicrm_next/questionnaire/h5_write.py` reports only durable continuation state. `aicrm_next/questionnaire/event_consumers.py` is the sole questionnaire tag planner, and the External Effect worker owns provider execution plus post-success projection.
6. Customer profile tags are read-only today; `PlanCustomerTagAssignmentCommand` is the plan-only boundary for future assignment mutation.
7. No user ops tag mutation route was found; tag usage there is read/filter/preview only.

## CommandBus

| Command | Source | SideEffectPlan | Adapter | Status |
| --- | --- | --- | --- | --- |
| `PlanWeComTagMarkCommand` | `POST /api/admin/wecom/tags/live/mark` | `wecom.tag.mark` | `wecom_tag`, `queued_external_effect` | queued external effect |
| `PlanWeComTagUnmarkCommand` | `POST /api/admin/wecom/tags/live/unmark` | `wecom.tag.unmark` | `wecom_tag`, `queued_external_effect` | queued external effect |
| `PlanCustomerTagAssignmentCommand` | customer profile/tag assignment boundary | `wecom.tag.assignment.apply` | `wecom_tag`, `queued_external_effect` | queued external effect |
| questionnaire H5 submit tag apply | questionnaire H5 submit scoring | `questionnaire.tag.apply` | `durable_internal_event` | no provider in H5; worker executes queued effect and then performs post-success local projection |

## SideEffectPlan And External Effect Queue

Admin/customer mutation plans include `adapter_name=wecom_tag`, `adapter_mode=queued_external_effect`, `requires_approval=false`, `real_external_call_executed=false`, `wecom_api_called=false`, `external_userid`, `tag_ids`, and a redacted `payload_summary`. Questionnaire H5 submit returns `tag_apply.status=queued` only after its transactional outbox exists, with `real_external_call_executed=false` and no External Effect job claimed. Canonical-identity gaps remain retryable in the consumer; provider failures live on the External Effect job/attempt and never update the local mirror.

## Explicitly Unchanged

- Real WeCom execution for questionnaire `final_tags` is performed only by the External Effect worker.
- Real WeCom token exchange is deferred to the External Effect worker and gated adapter.
- Real external push is not changed by this group.
- Payment, storage, OpenClaw, and automation runtime are not changed.
- Tag catalog CRUD/sync remains group 13 `deletion_locked`.
