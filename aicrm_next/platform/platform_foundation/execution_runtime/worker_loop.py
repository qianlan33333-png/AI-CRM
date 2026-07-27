from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable


@dataclass(frozen=True)
class WorkAttempt:
    claimed: bool
    ok: bool
    item_id: str = ""
    error: str = ""


class CapacityBoundWorkerLoop:
    """Run one claim per real executor slot and wake on DB hints or deadlines."""

    def __init__(
        self,
        *,
        work_once: Callable[[], WorkAttempt],
        next_due_at: Callable[[], datetime | None],
        listener: Any,
        max_concurrency: int,
        fallback_seconds: float = 30,
        reconnect_seconds: float = 1,
        wake_filter: Callable[[Any], bool] | None = None,
    ) -> None:
        self._work_once = work_once
        self._next_due_at = next_due_at
        self._listener = listener
        self._max_concurrency = max(1, int(max_concurrency))
        self._fallback_seconds = max(0.1, float(fallback_seconds))
        self._reconnect_seconds = max(0.05, float(reconnect_seconds))
        self._wake_filter = wake_filter or (lambda _hint: True)

    def run(self, *, stop_event: threading.Event) -> dict[str, int | bool]:
        wake = threading.Event()
        dirty = threading.Event()
        listener_ready = threading.Event()
        counters = {"attempted": 0, "claimed": 0, "failed": 0}
        listener_thread = threading.Thread(
            target=self._listen,
            args=(stop_event, wake, dirty, listener_ready),
            name="aicrm-queue-listener",
            daemon=True,
        )
        listener_thread.start()
        while not listener_ready.wait(timeout=0.1):
            if stop_event.is_set():
                self._listener.close()
                listener_thread.join(timeout=2)
                return {"ok": False, "attempted": 0, "claimed": 0, "failed": 1}
        futures: set[Future[WorkAttempt]] = set()
        should_fill = True
        try:
            with ThreadPoolExecutor(
                max_workers=self._max_concurrency,
                thread_name_prefix="aicrm-queue-slot",
            ) as executor:
                while not stop_event.is_set():
                    completed = {future for future in futures if future.done()}
                    for future in completed:
                        futures.remove(future)
                        counters["attempted"] += 1
                        try:
                            result = future.result()
                        except Exception:
                            counters["failed"] += 1
                            continue
                        if result.claimed:
                            counters["claimed"] += 1
                            should_fill = True
                        if not result.ok:
                            counters["failed"] += 1

                    if should_fill:
                        while len(futures) < self._max_concurrency and not stop_event.is_set():
                            future = executor.submit(self._work_once)
                            future.add_done_callback(lambda _future: wake.set())
                            futures.add(future)
                        should_fill = False

                    if stop_event.is_set():
                        break

                    timeout = self._deadline_timeout()
                    wait_result = self._wait_for_wake_or_stop(
                        wake=wake,
                        stop_event=stop_event,
                        timeout_seconds=timeout,
                    )
                    if wait_result == "stop":
                        break
                    if wait_result == "wake":
                        wake.clear()
                        if dirty.is_set():
                            dirty.clear()
                            should_fill = True
                    else:
                        should_fill = True
        finally:
            stop_event.set()
            wake.set()
            self._listener.close()
            listener_thread.join(timeout=2)
        return {"ok": counters["failed"] == 0, **counters}

    @staticmethod
    def _wait_for_wake_or_stop(
        *,
        wake: threading.Event,
        stop_event: threading.Event,
        timeout_seconds: float,
    ) -> str:
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while not stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "timeout"
            if wake.wait(timeout=min(remaining, 0.25)):
                return "wake"
        return "stop"

    def _listen(
        self,
        stop_event: threading.Event,
        wake: threading.Event,
        dirty: threading.Event,
        listener_ready: threading.Event,
    ) -> None:
        initial_listener_ready = False
        while not stop_event.is_set():
            try:
                if not bool(getattr(self._listener, "connected", False)):
                    self._listener.connect()
                    if initial_listener_ready:
                        dirty.set()
                        wake.set()
                    else:
                        initial_listener_ready = True
                        listener_ready.set()
                hint = self._listener.wait(timeout_seconds=self._fallback_seconds)
                if hint is None or self._wake_filter(hint):
                    dirty.set()
                    wake.set()
            except Exception:
                self._listener.close()
                if stop_event.wait(self._reconnect_seconds):
                    return

    def _deadline_timeout(self) -> float:
        due_at = self._next_due_at()
        if due_at is None:
            return self._fallback_seconds
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)
        seconds = (due_at.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
        return min(self._fallback_seconds, max(0.25, seconds))
