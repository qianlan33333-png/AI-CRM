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

Install the dedicated timer alongside the existing internal-event and external-effect workers:

```bash
sudo cp deploy/openclaw-ai-audience-scheduler.service /etc/systemd/system/
sudo cp deploy/openclaw-ai-audience-scheduler.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now openclaw-ai-audience-scheduler.timer
```

The timer writes clock intents once per day:

```text
OnCalendar=*-*-* 02:00:00 Asia/Shanghai
```

`scripts/ops/check_ai_audience_refresh_owner.py` is a fail-closed precondition for the timer unit. The PostgreSQL internal runtime owns `ai_audience.refresh.requested`; provider continuations remain separate external effects. The timer has no relay or consumer ownership.
