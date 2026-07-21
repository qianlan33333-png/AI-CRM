from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from aicrm_next.platform_foundation.execution_runtime.worker_loop import (
    CapacityBoundWorkerLoop,
    WorkAttempt,
)


class FakeListener:
    def __init__(self, *, on_wait=None) -> None:
        self.waits: list[float] = []
        self.closed = False
        self._on_wait = on_wait
        self._closed = threading.Event()

    def connect(self) -> None:
        return None

    def wait(self, *, timeout_seconds: float):
        self.waits.append(timeout_seconds)
        if self._on_wait is not None:
            self._on_wait()
        else:
            self._closed.wait(max(timeout_seconds, 0))
        return None

    def close(self) -> None:
        self.closed = True
        self._closed.set()


def test_worker_claims_only_for_real_free_slots() -> None:
    release = threading.Event()
    stop = threading.Event()
    lock = threading.Lock()
    running = 0
    peak = 0
    started = 0

    def work_once() -> WorkAttempt:
        nonlocal running, peak, started
        with lock:
            running += 1
            started += 1
            peak = max(peak, running)
        release.wait(timeout=2)
        with lock:
            running -= 1
        return WorkAttempt(claimed=True, ok=True)

    loop = CapacityBoundWorkerLoop(
        work_once=work_once,
        next_due_at=lambda: None,
        listener=FakeListener(),
        max_concurrency=2,
        fallback_seconds=30,
    )
    thread = threading.Thread(target=loop.run, kwargs={"stop_event": stop}, daemon=True)
    thread.start()

    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        with lock:
            if started >= 2:
                break
        time.sleep(0.01)

    with lock:
        assert started == 2
        assert running == 2
        assert peak == 2
    time.sleep(0.05)
    with lock:
        assert started == 2, "no third claim may happen while both slots are occupied"

    stop.set()
    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_slot_completion_immediately_attempts_next_job() -> None:
    stop = threading.Event()
    completed_three = threading.Event()
    starts: list[float] = []
    lock = threading.Lock()

    def work_once() -> WorkAttempt:
        with lock:
            starts.append(time.monotonic())
            position = len(starts)
        if position >= 3:
            completed_three.set()
            stop.set()
        return WorkAttempt(claimed=True, ok=True)

    loop = CapacityBoundWorkerLoop(
        work_once=work_once,
        next_due_at=lambda: None,
        listener=FakeListener(),
        max_concurrency=1,
        fallback_seconds=30,
    )
    thread = threading.Thread(target=loop.run, kwargs={"stop_event": stop}, daemon=True)
    thread.start()

    assert completed_three.wait(timeout=1)
    thread.join(timeout=2)
    assert starts[1] - starts[0] < 0.2
    assert starts[2] - starts[1] < 0.2


def test_idle_wait_is_bounded_by_nearest_deadline_and_fallback() -> None:
    stop = threading.Event()
    listener = FakeListener()
    due_at = datetime.now(timezone.utc) + timedelta(seconds=0.05)
    attempts = 0

    def no_work() -> WorkAttempt:
        nonlocal attempts
        attempts += 1
        if attempts >= 2:
            stop.set()
        return WorkAttempt(claimed=False, ok=True)

    loop = CapacityBoundWorkerLoop(
        work_once=no_work,
        next_due_at=lambda: due_at,
        listener=listener,
        max_concurrency=1,
        fallback_seconds=30,
    )
    started_at = time.monotonic()
    loop.run(stop_event=stop)

    assert listener.waits
    assert listener.waits[0] == 30
    assert attempts == 2
    assert time.monotonic() - started_at < 0.5
    assert listener.closed is True


def test_empty_claim_does_not_spin_until_listener_or_deadline_wakes() -> None:
    stop = threading.Event()
    attempts = 0

    def no_work() -> WorkAttempt:
        nonlocal attempts
        attempts += 1
        return WorkAttempt(claimed=False, ok=True)

    loop = CapacityBoundWorkerLoop(
        work_once=no_work,
        next_due_at=lambda: None,
        listener=FakeListener(),
        max_concurrency=2,
        fallback_seconds=30,
    )
    thread = threading.Thread(target=loop.run, kwargs={"stop_event": stop}, daemon=True)
    thread.start()

    deadline = time.monotonic() + 0.5
    while attempts < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert attempts == 2

    time.sleep(0.1)
    assert attempts == 2, "empty claim attempts must sleep instead of busy-looping"

    stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_listener_is_ready_before_the_initial_drain() -> None:
    connect_release = threading.Event()
    stop = threading.Event()
    called = threading.Event()

    class DelayedListener(FakeListener):
        def __init__(self) -> None:
            super().__init__()
            self.connected = False

        def connect(self) -> None:
            connect_release.wait(timeout=1)
            self.connected = True

        def close(self) -> None:
            self.connected = False
            super().close()

    def no_work() -> WorkAttempt:
        called.set()
        stop.set()
        return WorkAttempt(claimed=False, ok=True)

    loop = CapacityBoundWorkerLoop(
        work_once=no_work,
        next_due_at=lambda: None,
        listener=DelayedListener(),
        max_concurrency=1,
        fallback_seconds=30,
    )
    thread = threading.Thread(target=loop.run, kwargs={"stop_event": stop}, daemon=True)
    thread.start()
    assert called.wait(timeout=0.1) is False
    connect_release.set()
    assert called.wait(timeout=1)
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_claimed_failure_marks_the_worker_loop_failed() -> None:
    stop = threading.Event()
    attempts = 0

    def work_once() -> WorkAttempt:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return WorkAttempt(claimed=True, ok=False, item_id="failed", error="handler_failed")
        stop.set()
        return WorkAttempt(claimed=False, ok=True)

    result = CapacityBoundWorkerLoop(
        work_once=work_once,
        next_due_at=lambda: datetime.now(timezone.utc),
        listener=FakeListener(),
        max_concurrency=1,
        fallback_seconds=0.1,
    ).run(stop_event=stop)

    assert result["claimed"] == 1
    assert result["failed"] == 1
    assert result["ok"] is False
