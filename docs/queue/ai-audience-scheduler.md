# AI Audience Scheduler

`scripts/run_ai_audience_scheduler.py` is the clock-intent writer for AI audience packages.

It follows the queue boundaries:

- writes one idempotent daily durable intent per active package at `Asia/Shanghai 02:00`;
- never scans incremental packages on a three-minute interval;
- never claims, relays, or executes an internal-event consumer;
- source events advance a package's monotonic dirty generation and coalesce behind its single open intent;
- never sends webhook or WeCom messages directly.

External side effects remain in `external_effect_job` and are executed only by the External Effect worker.

## Production Timer

Production uses the consolidated versioned scheduler alongside the internal and external workers:

```bash
sudo cp deploy/aicrm-job-catalog-scheduler.service /etc/systemd/system/
sudo cp deploy/aicrm-job-catalog-scheduler.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aicrm-job-catalog-scheduler.timer
```

The catalog evaluates `ai_audience.refresh` every three minutes while preserving the
daily 02:00 Asia/Shanghai intent rule inside the handler:

```text
schedule="*/3 * * * *"
```

`scripts/ops/check_ai_audience_refresh_owner.py` remains a fail-closed code and
legacy-unit guard. It verifies that the consolidated scheduler is the declared
successor and that the intermediate dedicated timer is retired. The PostgreSQL
internal runtime owns `ai_audience.refresh.requested`; provider continuations remain
separate external effects. The scheduler has no relay, consumer, or provider ownership.
