from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from threading import Condition, Lock
from time import monotonic
from typing import Any, Iterator

from .runtime_settings import runtime_setting


RUNTIME_SETTING_KEYS = frozenset(
    {
        "AICRM_MEDIA_READ_MAX_CONCURRENCY",
        "AICRM_MEDIA_READ_QUEUE_LIMIT",
        "AICRM_MEDIA_READ_RETRY_AFTER_SECONDS",
        "AICRM_MEDIA_READ_WAIT_TIMEOUT_MS",
        "AICRM_MEDIA_ADMISSION_ENABLED",
        "AICRM_MEDIA_ADMISSION_ROLLOUT_PERCENT",
    }
)


@dataclass(frozen=True)
class ResourcePolicy:
    resource_class: str
    priority: str
    max_in_flight: int
    max_queued: int
    wait_timeout_ms: int
    retry_after_seconds: int = 1

    def normalized(self) -> "ResourcePolicy":
        return ResourcePolicy(
            resource_class=str(self.resource_class or "shared").strip() or "shared",
            priority=str(self.priority or "P2").strip().upper() or "P2",
            max_in_flight=max(1, int(self.max_in_flight or 1)),
            max_queued=max(0, int(self.max_queued or 0)),
            wait_timeout_ms=max(0, int(self.wait_timeout_ms or 0)),
            retry_after_seconds=max(1, int(self.retry_after_seconds or 1)),
        )


class ResourceCapacityExhausted(RuntimeError):
    error_code = "resource_capacity_exhausted"

    def __init__(self, policy: ResourcePolicy, *, reason: str) -> None:
        self.policy = policy
        self.reason = str(reason or "capacity_exhausted")
        super().__init__(f"{policy.resource_class} capacity exhausted")


@dataclass(frozen=True)
class ResourceLease:
    resource_class: str
    priority: str
    waited_ms: int


class ResourceAdmissionController:
    """Bound low-priority work without making critical routes share its queue."""

    def __init__(self, policy: ResourcePolicy) -> None:
        self.policy = policy.normalized()
        self._condition = Condition()
        self._in_flight = 0
        self._queued = 0
        self._attempted = 0
        self._completed = 0
        self._rejected = 0
        self._timed_out = 0
        self._max_in_flight_observed = 0
        self._max_queued_observed = 0
        self._wait_samples_ms: deque[int] = deque(maxlen=512)
        self._duration_samples_ms: deque[int] = deque(maxlen=512)

    @contextmanager
    def admit(self) -> Iterator[ResourceLease]:
        started = monotonic()
        queued = False
        with self._condition:
            self._attempted += 1
            if self._in_flight >= self.policy.max_in_flight:
                if self._queued >= self.policy.max_queued:
                    self._rejected += 1
                    raise ResourceCapacityExhausted(self.policy, reason="queue_full")
                self._queued += 1
                queued = True
                self._max_queued_observed = max(self._max_queued_observed, self._queued)
                deadline = started + (self.policy.wait_timeout_ms / 1000)
                while self._in_flight >= self.policy.max_in_flight:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        self._queued -= 1
                        self._rejected += 1
                        self._timed_out += 1
                        raise ResourceCapacityExhausted(self.policy, reason="queue_timeout")
                    self._condition.wait(timeout=remaining)
            if queued:
                self._queued -= 1
            waited_ms = max(0, int((monotonic() - started) * 1000))
            self._wait_samples_ms.append(waited_ms)
            self._in_flight += 1
            self._max_in_flight_observed = max(self._max_in_flight_observed, self._in_flight)

        lease = ResourceLease(
            resource_class=self.policy.resource_class,
            priority=self.policy.priority,
            waited_ms=waited_ms,
        )
        try:
            yield lease
        finally:
            duration_ms = max(0, int((monotonic() - started) * 1000))
            with self._condition:
                self._in_flight = max(0, self._in_flight - 1)
                self._completed += 1
                self._duration_samples_ms.append(duration_ms)
                self._condition.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            waits = list(self._wait_samples_ms)
            durations = list(self._duration_samples_ms)
            return {
                "resource_class": self.policy.resource_class,
                "priority": self.policy.priority,
                "limits": {
                    "max_in_flight": self.policy.max_in_flight,
                    "max_queued": self.policy.max_queued,
                    "wait_timeout_ms": self.policy.wait_timeout_ms,
                    "retry_after_seconds": self.policy.retry_after_seconds,
                },
                "current": {"in_flight": self._in_flight, "queued": self._queued},
                "totals": {
                    "attempted": self._attempted,
                    "completed": self._completed,
                    "rejected": self._rejected,
                    "timed_out": self._timed_out,
                },
                "observed": {
                    "max_in_flight": self._max_in_flight_observed,
                    "max_queued": self._max_queued_observed,
                    "wait_ms": _percentiles(waits),
                    "duration_ms": _percentiles(durations),
                },
            }


class RequestPriorityMetrics:
    """Low-cardinality in-process telemetry for P0/P1/P2 HTTP traffic."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._current = {priority: 0 for priority in ("P0", "P1", "P2")}
        self._max_current = {priority: 0 for priority in ("P0", "P1", "P2")}
        self._totals = {
            priority: {"requests": 0, "errors": 0, "timeouts": 0, "rejected": 0}
            for priority in ("P0", "P1", "P2")
        }
        self._duration_samples_ms: dict[str, deque[int]] = {
            priority: deque(maxlen=1024) for priority in ("P0", "P1", "P2")
        }

    def begin(self, priority: str) -> None:
        normalized = _priority(priority)
        with self._lock:
            self._current[normalized] += 1
            self._max_current[normalized] = max(self._max_current[normalized], self._current[normalized])
            self._totals[normalized]["requests"] += 1

    def complete(self, priority: str, *, duration_ms: float, status_code: int) -> None:
        normalized = _priority(priority)
        status = int(status_code or 0)
        with self._lock:
            self._current[normalized] = max(0, self._current[normalized] - 1)
            self._duration_samples_ms[normalized].append(max(0, int(duration_ms)))
            if status >= 500:
                self._totals[normalized]["errors"] += 1
            if status in {408, 504}:
                self._totals[normalized]["timeouts"] += 1
            if status == 429:
                self._totals[normalized]["rejected"] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                priority: {
                    "current": self._current[priority],
                    "max_current": self._max_current[priority],
                    "totals": dict(self._totals[priority]),
                    "duration_ms": _percentiles(list(self._duration_samples_ms[priority])),
                }
                for priority in ("P0", "P1", "P2")
            }


def _percentiles(values: list[int]) -> dict[str, int]:
    if not values:
        return {"p50": 0, "p95": 0, "p99": 0}
    ordered = sorted(values)

    def at(percentile: float) -> int:
        index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile)))
        return int(ordered[index])

    return {"p50": at(0.50), "p95": at(0.95), "p99": at(0.99)}


_MEDIA_BINARY_CONTROLLER: ResourceAdmissionController | None = None
_REQUEST_PRIORITY_METRICS = RequestPriorityMetrics()
_ROLLOUT_LOCK = Lock()
_ROLLOUT_BYPASSED = 0


def media_binary_admission_controller() -> ResourceAdmissionController:
    global _MEDIA_BINARY_CONTROLLER
    if _MEDIA_BINARY_CONTROLLER is None:
        _MEDIA_BINARY_CONTROLLER = ResourceAdmissionController(
            ResourcePolicy(
                resource_class="media_binary",
                priority="P2",
                max_in_flight=_setting_int("AICRM_MEDIA_READ_MAX_CONCURRENCY", 2),
                max_queued=_setting_int("AICRM_MEDIA_READ_QUEUE_LIMIT", 10),
                wait_timeout_ms=_setting_int("AICRM_MEDIA_READ_WAIT_TIMEOUT_MS", 800),
                retry_after_seconds=_setting_int("AICRM_MEDIA_READ_RETRY_AFTER_SECONDS", 1),
            )
        )
    return _MEDIA_BINARY_CONTROLLER


@contextmanager
def media_binary_admission(*, rollout_key: str) -> Iterator[ResourceLease]:
    """Apply P2 admission only to the currently enabled deterministic cohort."""

    global _ROLLOUT_BYPASSED
    enabled = _setting_bool("AICRM_MEDIA_ADMISSION_ENABLED", False)
    rollout_percent = _setting_percent("AICRM_MEDIA_ADMISSION_ROLLOUT_PERCENT", 0)
    key = str(rollout_key or "").strip()
    participates = enabled and rollout_percent > 0 and _rollout_bucket(key) < rollout_percent
    if participates:
        with media_binary_admission_controller().admit() as lease:
            yield lease
        return
    with _ROLLOUT_LOCK:
        _ROLLOUT_BYPASSED += 1
    yield ResourceLease(resource_class="media_binary", priority="P2", waited_ms=0)


def resource_admission_snapshot() -> dict[str, Any]:
    with _ROLLOUT_LOCK:
        bypassed = _ROLLOUT_BYPASSED
    return {
        "request_priorities": _REQUEST_PRIORITY_METRICS.snapshot(),
        "media_binary": media_binary_admission_controller().snapshot(),
        "media_rollout": {
            "enabled": _setting_bool("AICRM_MEDIA_ADMISSION_ENABLED", False),
            "percent": _setting_percent("AICRM_MEDIA_ADMISSION_ROLLOUT_PERCENT", 0),
            "bypassed": bypassed,
        },
    }


def request_priority_metrics() -> RequestPriorityMetrics:
    return _REQUEST_PRIORITY_METRICS


def request_priority_for_path(path: str) -> str:
    normalized = str(path or "").lower()
    if any(marker in normalized for marker in ("/thumbnail", "/variants/", "/export", "/batch")):
        return "P2"
    if normalized == "/sidebar/bind-mobile" or normalized.startswith("/api/sidebar/"):
        return "P0"
    return "P1"


def reset_resource_admission_controllers() -> None:
    global _MEDIA_BINARY_CONTROLLER, _REQUEST_PRIORITY_METRICS, _ROLLOUT_BYPASSED
    _MEDIA_BINARY_CONTROLLER = None
    _REQUEST_PRIORITY_METRICS = RequestPriorityMetrics()
    with _ROLLOUT_LOCK:
        _ROLLOUT_BYPASSED = 0


def _setting_int(name: str, default: int) -> int:
    try:
        return int(runtime_setting(name, str(default)) or default)
    except (TypeError, ValueError):
        return int(default)


def _setting_bool(name: str, default: bool) -> bool:
    value = str(runtime_setting(name, "true" if default else "false") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _setting_percent(name: str, default: int) -> int:
    return max(0, min(100, _setting_int(name, default)))


def _rollout_bucket(value: str) -> int:
    digest = sha256((value or "unkeyed").encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % 100


def _priority(value: str) -> str:
    normalized = str(value or "P1").strip().upper()
    return normalized if normalized in {"P0", "P1", "P2"} else "P1"
