# Automation Ops Scheduler

`scripts/run_automation_ops_scheduler.py` is now the production scheduler entry
for group_ops only. `aicrm-next-group-ops-planning.timer` is its sole automatic
owner after cutover. It creates due `external_effect_job` rows with
`effect_type=wecom.message.group.send`; historical `broadcast_jobs` remain
read-only compatibility records. Real WeCom group delivery is handled by the
External Effect worker and the `wecom_group_message` adapter guard.

## group_ops due_at

Only standard group operation plans are scanned:

- `plan_type = standard`
- plan `status = active`
- node `status = active`
- at least one active bound group
- node content can be normalized into a WeCom customer-group payload

For every active plan node and every active bound group:

1. Read `scheduled_time`; if missing, derive `HH:MM` from `trigger_time_label`.
2. Interpret `scheduled_time` as business wall-clock time in `Asia/Shanghai` by default. Override only with `AICRM_GROUP_OPS_TIMEZONE`.
3. Use `automation_group_ops_plan_groups.created_at` as the group start time.
4. If a binding has no `created_at`, use `automation_group_ops_plans.created_at`.
5. Convert the start anchor to the business timezone, take that local date, then compute `due_at = start_date + (day_index - 1) days + scheduled_time`.
6. Store `scheduled_for` as business-timezone ISO, for example `2026-05-29T13:00:00+08:00`.
7. Compare due-ness by converting both `due_at` and scheduler `now` to UTC.
8. If `due_at <= now`, plan a `wecom.message.group.send` External Effect job with `scheduled_at=due_at`.

Groups with the same `plan_id`, `node_id`, `due_at` minute, owner, and content hash are merged into one job. Their `content_payload.chat_ids` contains all due `chat_id` values. Groups with different `due_at` values are not merged.

The External Effect job keeps the current group_ops delivery contract in `payload_json`: `chat_ids`, `owner_userid`, `webhook_key`, `content_payload.channel=wecom_customer_group`, and the normalized message payload. `scheduled_at` is the computed `due_at`, not scheduler runtime.

## Idempotency

The scheduler uses a revision-aware source/idempotency shape that includes:

- `plan_id`
- `node_id`
- `due_at` minute
- plan and node `updated_at`
- normalized message content hash
- owner and schedule fields
- sorted active chat bindings and their lifecycle timestamps
- sorted `chat_ids` hash

The revision fingerprint is also the effect graph `version_fingerprint`. Rerunning the
same revision does not duplicate work. Editing plan/node content, owner, schedule, or
active group membership creates a new graph and transactionally cancels every old
pre-provider child before the new revision becomes eligible. Disable, archive, delete,
and group removal invalidate the old graph without materializing a replacement. A child
that has crossed the provider boundary is never overwritten or replayed; its graph is
reported as terminal/superseded with the boundary evidence preserved.

The `external_effect_job` idempotency guard is the primary duplicate protection, with a
historical `broadcast_jobs` lookup only for older rows.

## Retired legacy components

The old `operation_task` scheduler path, HXC dashboard refresh hook, and Feishu hourly report hook have been removed from this runner. They no longer appear as skipped components and must not be used as compatibility placeholders for old automation program/task orchestration.

## Responsibility Boundary

- Automation ops scheduler: compute due group_ops work and plan External Effect jobs.
- External Effect worker: claim due group-send effects and dispatch the guarded `wecom_group_message` adapter.
- Broadcast queue worker: remains for historical/non-group_ops broadcast rows.
- WeCom adapter: decide whether fake, blocked, or production side effects may run.

The scheduler must not call WeCom directly.

## group_ops Real E2E Notes

`scheduled_time` keeps the product rule: `08:00-23:30`, in 30-minute steps, interpreted as the `Asia/Shanghai` business timezone unless `AICRM_GROUP_OPS_TIMEZONE` is explicitly set.

For real group-send acceptance, choose the current `Asia/Shanghai` time and round down to the nearest 30-minute slot:

- `13:07` -> `13:00`
- `13:35` -> `13:30`

If the current business time is outside `08:00-23:30`, do not run real group-send E2E; run unit tests only.

Real group_ops acceptance must use only:

```bash
python scripts/run_automation_ops_scheduler.py
python scripts/run_external_effect_queue_worker.py --execute --effect-type wecom.message.group.send
```

Do not use group_ops run-due or direct queue writes to stand in for automatic scheduling.

## WeCom Modes

- `AICRM_WECOM_GROUP_ADAPTER_MODE=fake`: worker can mark group jobs sent, while the adapter records `side_effect_executed=false`.
- `AICRM_WECOM_GROUP_ADAPTER_MODE=disabled` or `staging`: group sending is blocked and jobs fail or remain blocked through the adapter path.
- `AICRM_WECOM_GROUP_ADAPTER_MODE=production`: real group messages still require `AICRM_ENABLE_REAL_WECOM_GROUP_MESSAGE=true`; otherwise the production guard fails.

## systemd

The deployment manifest installs and enables the reviewed Next successor after
the generation cutover is committed:

```bash
sudo cp deploy/aicrm-next-group-ops-planning.service /etc/systemd/system/
sudo cp deploy/aicrm-next-group-ops-planning.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aicrm-next-group-ops-planning.timer
sudo systemctl status aicrm-next-group-ops-planning.timer
```

The timer runs every minute. Idempotency makes this safe even when no tasks are due.
`openclaw-automation-ops-scheduler.timer` remains disabled as a retired legacy
owner and is never a rollback path.
