from __future__ import annotations

import time

from aicrm_next.platform_foundation.execution_runtime.heartbeat import LeaseHeartbeat


def test_heartbeat_renews_until_owner_is_lost() -> None:
    calls = 0

    def renew() -> bool:
        nonlocal calls
        calls += 1
        return calls < 3

    with LeaseHeartbeat(renew, interval_seconds=0.01) as heartbeat:
        deadline = time.monotonic() + 0.5
        while not heartbeat.lost and time.monotonic() < deadline:
            time.sleep(0.005)

    assert calls == 3
    assert heartbeat.lost is True


def test_heartbeat_exception_is_treated_as_lease_loss() -> None:
    def renew() -> bool:
        raise RuntimeError("database unavailable")

    with LeaseHeartbeat(renew, interval_seconds=0.01) as heartbeat:
        deadline = time.monotonic() + 0.5
        while not heartbeat.lost and time.monotonic() < deadline:
            time.sleep(0.005)

    assert heartbeat.lost is True
