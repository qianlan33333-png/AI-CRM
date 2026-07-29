from __future__ import annotations

from aicrm_next.platform.platform_foundation.execution_runtime.start_rate import (
    SharedStartRateLimiter,
)
from aicrm_next.platform.platform_foundation.execution_runtime.handlers import (
    external_effect_handler,
)
from types import SimpleNamespace


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


def test_shared_start_rate_limits_two_lanes_in_one_scope() -> None:
    clock = FakeClock()
    limiter = SharedStartRateLimiter(
        target_rate_per_second=2.0,
        burst=2,
        now=clock.now,
        sleep=clock.sleep,
    )

    limiter.acquire("wecom:corp:app:send")
    limiter.acquire("wecom:corp:app:send")
    limiter.acquire("wecom:corp:app:send")

    assert clock.sleeps == [0.5]
    assert limiter.snapshot("wecom:corp:app:send")["current_rate_per_second"] == 2.0


def test_shared_start_rate_halves_on_throttle_and_recovers_after_five_minutes() -> None:
    clock = FakeClock()
    limiter = SharedStartRateLimiter(
        target_rate_per_second=2.0,
        burst=2,
        now=clock.now,
        sleep=clock.sleep,
    )

    limiter.record_outcome("scope", error_code="rate_limited")
    assert limiter.snapshot("scope")["current_rate_per_second"] == 1.0
    limiter.record_outcome("scope", provider_errcode=45009)
    assert limiter.snapshot("scope")["current_rate_per_second"] == 0.5

    clock.value += 299
    limiter.record_outcome("scope")
    assert limiter.snapshot("scope")["current_rate_per_second"] == 0.5
    clock.value += 1
    limiter.record_outcome("scope")
    assert limiter.snapshot("scope")["current_rate_per_second"] == 1.0
    clock.value += 300
    limiter.record_outcome("scope")
    assert limiter.snapshot("scope")["current_rate_per_second"] == 2.0


def test_shared_start_rate_isolated_by_scope() -> None:
    clock = FakeClock()
    limiter = SharedStartRateLimiter(
        target_rate_per_second=2.0,
        burst=2,
        now=clock.now,
        sleep=clock.sleep,
    )

    limiter.record_outcome("corp-a", provider_errcode=45011)

    assert limiter.snapshot("corp-a")["current_rate_per_second"] == 1.0
    assert limiter.snapshot("corp-b")["current_rate_per_second"] == 2.0


def test_external_handler_applies_one_limiter_to_regular_and_ai_bulk_lanes() -> None:
    calls: list[tuple[int, str]] = []

    class Worker:
        def dispatch_claimed(self, item_id: int, *, lease_token: str):
            calls.append((item_id, lease_token))
            return {"ok": True, "job": {"last_error_code": ""}}

    class Limiter:
        def __init__(self) -> None:
            self.acquired: list[str] = []
            self.outcomes: list[str] = []

        def acquire(self, scope_key: str) -> None:
            self.acquired.append(scope_key)

        def record_outcome(self, scope_key: str, **_kwargs) -> None:
            self.outcomes.append(scope_key)

    limiter = Limiter()
    handler = external_effect_handler(Worker(), start_rate_limiter=limiter)
    for item_id, lane in enumerate(("wecom_bulk", "wecom_ai_assistant_bulk"), start=1):
        handler(
            SimpleNamespace(
                item_id=item_id,
                lease_token=f"lease-{item_id}",
                lane=lane,
                payload={"rate_scope_key": "wecom:corp:app:send_private_message"},
            )
        )

    assert limiter.acquired == [
        "wecom:corp:app:send_private_message",
        "wecom:corp:app:send_private_message",
    ]
    assert limiter.outcomes == limiter.acquired
    assert calls == [(1, "lease-1"), (2, "lease-2")]
