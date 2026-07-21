from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .heartbeat import LeaseHeartbeat
from .lanes import QueueLane
from .listener import PostgresQueueWakeListener
from .repository import ExecutionRuntimeRepository, RuntimeClaim
from .worker_loop import CapacityBoundWorkerLoop, WorkAttempt


ClaimHandler = Callable[[RuntimeClaim], bool | Mapping[str, Any]]


@dataclass(frozen=True)
class QueueRuntimeServiceResult:
    ok: bool
    queue_kind: str
    lane_results: dict[str, dict[str, int | bool]]


class QueueRuntimeService:
    """Persistent service with a reserved executor pool for every lane."""

    def __init__(
        self,
        *,
        queue_kind: str,
        lanes: tuple[QueueLane, ...],
        generation: int,
        handler: ClaimHandler,
        repository: ExecutionRuntimeRepository | None = None,
        listener_factory: Callable[[], Any] = PostgresQueueWakeListener,
        service_name: str = "",
        worker_id: str = "",
        lease_seconds: int = 30,
        heartbeat_seconds: float = 10,
        fallback_seconds: float = 30,
        test_only: bool = False,
        claimless: bool = True,
    ) -> None:
        if queue_kind not in {
            "external_effect",
            "internal_event",
            "internal_outbox",
            "webhook_inbox",
        }:
            raise ValueError("unsupported queue kind")
        if not lanes:
            raise ValueError("at least one queue lane is required")
        self._queue_kind = queue_kind
        self._lanes = lanes
        self._generation = int(generation)
        self._handler = handler
        self._repo = repository or ExecutionRuntimeRepository()
        self._listener_factory = listener_factory
        self._service_name = service_name or f"aicrm-{queue_kind}-runtime"
        self._worker_id = worker_id or f"{self._service_name}-{uuid4().hex[:10]}"
        self._lease_seconds = max(10, min(int(lease_seconds or 30), 300))
        self._heartbeat_seconds = max(0.1, float(heartbeat_seconds))
        self._fallback_seconds = max(0.1, float(fallback_seconds))
        self._test_only = bool(test_only)
        self._claimless = bool(claimless)

    def run(self, *, stop_event: threading.Event) -> QueueRuntimeServiceResult:
        self._validate_external_execution_scope()
        lane_results: dict[str, dict[str, int | bool]] = {}
        lane_errors: list[str] = []
        lock = threading.Lock()
        threads: list[threading.Thread] = []

        def run_lane(lane: QueueLane) -> None:
            try:
                result = self._run_lane(lane=lane, stop_event=stop_event)
                with lock:
                    lane_results[lane.name] = result
            except Exception:
                with lock:
                    lane_errors.append(lane.name)
                stop_event.set()

        for lane in self._lanes:
            thread = threading.Thread(
                target=run_lane,
                args=(lane,),
                name=f"{self._service_name}-{lane.name}",
                daemon=True,
            )
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join()
        return QueueRuntimeServiceResult(
            ok=not lane_errors and all(bool(result.get("ok")) for result in lane_results.values()),
            queue_kind=self._queue_kind,
            lane_results=lane_results,
        )

    def _run_lane(
        self,
        *,
        lane: QueueLane,
        stop_event: threading.Event,
    ) -> dict[str, int | bool]:
        listener = self._listener_factory()
        lane_worker_id = f"{self._worker_id}:{lane.name}"
        heartbeat_stop = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            kwargs={
                "lane": lane,
                "worker_id": lane_worker_id,
                "listener": listener,
                "stop_event": heartbeat_stop,
            },
            name=f"{self._service_name}-{lane.name}-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()
        loop = CapacityBoundWorkerLoop(
            work_once=lambda: self._work_once(
                lane=lane,
                worker_id=lane_worker_id,
                listener=listener,
            ),
            next_due_at=lambda: (
                None
                if self._claimless
                else self._repo.next_due_at(
                    queue_kind=self._queue_kind,
                    lane=lane.name,
                    generation=self._generation,
                    test_only=self._test_only,
                )
            ),
            listener=listener,
            max_concurrency=lane.max_in_flight,
            fallback_seconds=self._fallback_seconds,
            wake_filter=lambda hint: (
                str(getattr(hint, "queue_kind", "all") or "all")
                in {"all", self._queue_kind}
                and str(getattr(hint, "lane", "") or "") in {"", lane.name}
            ),
        )
        try:
            return loop.run(stop_event=stop_event)
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=max(1.0, self._heartbeat_seconds * 2))

    def _work_once(self, *, lane: QueueLane, worker_id: str, listener: Any) -> WorkAttempt:
        claim = self._claim_one(lane=lane, worker_id=worker_id)
        if claim is None:
            self._heartbeat_worker(
                lane=lane,
                worker_id=worker_id,
                listener_connected=bool(getattr(listener, "connected", False)),
                drain_completed=True,
            )
            return WorkAttempt(claimed=False, ok=True)
        with LeaseHeartbeat(
            lambda: self._repo.renew_lease(
                queue_kind=claim.queue_kind,
                item_id=claim.item_id,
                lease_token=claim.lease_token,
                generation=claim.worker_generation,
                lease_seconds=self._lease_seconds,
            ),
            interval_seconds=self._heartbeat_seconds,
        ) as heartbeat:
            result = self._handler(claim)
        if isinstance(result, Mapping):
            ok = bool(result.get("ok"))
            error = str(result.get("error") or "")
        else:
            ok = bool(result)
            error = "" if ok else "handler_failed"
        if heartbeat.lost:
            return WorkAttempt(
                claimed=True,
                ok=False,
                item_id=str(claim.item_id),
                error="lost_lease",
            )
        return WorkAttempt(
            claimed=True,
            ok=ok,
            item_id=str(claim.item_id),
            error=error,
        )

    def _claim_one(self, *, lane: QueueLane, worker_id: str) -> RuntimeClaim | None:
        if self._claimless:
            return None
        kwargs = {
            "worker_id": worker_id,
            "generation": self._generation,
            "lease_seconds": self._lease_seconds,
        }
        if self._queue_kind == "external_effect":
            return self._repo.claim_external_effect_one(
                lane=lane.name,
                test_only=self._test_only,
                **kwargs,
            )
        if self._queue_kind == "internal_event":
            return self._repo.claim_internal_event_one(lane=lane.name, **kwargs)
        if self._queue_kind == "internal_outbox":
            return self._repo.claim_internal_outbox_one(lane=lane.name, **kwargs)
        return self._repo.claim_webhook_inbox_one(**kwargs)

    def _validate_external_execution_scope(self) -> None:
        if self._queue_kind != "external_effect" or self._claimless:
            return
        control = self._repo.read_control()
        scope = str(control.external_claim_scope or "blocked")
        if scope == "blocked":
            raise RuntimeError("external execution scope is blocked by database policy")
        if self._test_only and scope != "test_loopback":
            raise RuntimeError("external test-only worker does not match database claim scope")
        if not self._test_only and scope == "test_loopback":
            raise RuntimeError("database test-loopback scope requires a test-only worker")

    def _heartbeat_loop(
        self,
        *,
        lane: QueueLane,
        worker_id: str,
        listener: Any,
        stop_event: threading.Event,
    ) -> None:
        while not stop_event.is_set():
            try:
                self._heartbeat_worker(
                    lane=lane,
                    worker_id=worker_id,
                    listener_connected=bool(getattr(listener, "connected", False)),
                )
            except Exception:
                pass
            stop_event.wait(self._heartbeat_seconds)

    def _heartbeat_worker(
        self,
        *,
        lane: QueueLane,
        worker_id: str,
        listener_connected: bool,
        drain_completed: bool = False,
    ) -> None:
        self._repo.heartbeat_worker(
            service_name=self._service_name,
            worker_id=worker_id,
            queue_kind=self._queue_kind,
            generation=self._generation,
            rollout_mode=lane.rollout_mode,
            listener_connected=listener_connected,
            drain_completed=drain_completed,
        )


__all__ = ["QueueRuntimeService", "QueueRuntimeServiceResult"]
