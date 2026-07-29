from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable


_RATE_LIMIT_ERROR_CODES = frozenset(
    {
        "rate_limited",
        "http_429",
        "wecom_error_45009",
        "wecom_error_45011",
    }
)
_RATE_LIMIT_PROVIDER_CODES = frozenset({45009, 45011})


@dataclass
class _ScopeState:
    tokens: float
    last_refill_at: float
    current_rate: float
    last_limited_at: float | None = None
    last_recovery_at: float | None = None
    throttle_count: int = 0


class SharedStartRateLimiter:
    """Process-wide token bucket shared by ordinary and AI WeCom bulk lanes.

    The durable provider cooldown remains authoritative across processes. This
    limiter coordinates starts inside one runtime process and intentionally
    never claims to be a distributed rate limiter.
    """

    def __init__(
        self,
        *,
        target_rate_per_second: float = 2.0,
        burst: int = 2,
        minimum_rate_per_second: float = 0.25,
        recovery_seconds: float = 300.0,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._target_rate = max(0.01, float(target_rate_per_second))
        self._burst = max(1, int(burst))
        self._minimum_rate = min(
            self._target_rate,
            max(0.01, float(minimum_rate_per_second)),
        )
        self._recovery_seconds = max(1.0, float(recovery_seconds))
        self._now = now
        self._sleep = sleep
        self._lock = threading.Lock()
        self._scopes: dict[str, _ScopeState] = {}

    def _state(self, scope_key: str, now: float) -> _ScopeState:
        key = str(scope_key or "").strip() or "wecom:default"
        return self._scopes.setdefault(
            key,
            _ScopeState(
                tokens=float(self._burst),
                last_refill_at=now,
                current_rate=self._target_rate,
            ),
        )

    def acquire(self, scope_key: str) -> float:
        waited = 0.0
        while True:
            with self._lock:
                now = self._now()
                state = self._state(scope_key, now)
                elapsed = max(0.0, now - state.last_refill_at)
                state.tokens = min(
                    float(self._burst),
                    state.tokens + elapsed * state.current_rate,
                )
                state.last_refill_at = now
                if state.tokens >= 1.0:
                    state.tokens -= 1.0
                    return waited
                delay = max(0.001, (1.0 - state.tokens) / state.current_rate)
            self._sleep(delay)
            waited += delay

    def record_outcome(
        self,
        scope_key: str,
        *,
        error_code: str = "",
        provider_errcode: int = 0,
    ) -> None:
        with self._lock:
            now = self._now()
            state = self._state(scope_key, now)
            limited = (
                str(error_code or "").strip().lower() in _RATE_LIMIT_ERROR_CODES
                or int(provider_errcode or 0) in _RATE_LIMIT_PROVIDER_CODES
            )
            if limited:
                state.current_rate = max(self._minimum_rate, state.current_rate / 2.0)
                state.tokens = min(state.tokens, 1.0)
                state.last_limited_at = now
                state.last_recovery_at = now
                state.throttle_count += 1
                return
            recovery_anchor = state.last_recovery_at
            if (
                state.last_limited_at is not None
                and recovery_anchor is not None
                and now - state.last_limited_at >= self._recovery_seconds
                and now - recovery_anchor >= self._recovery_seconds
                and state.current_rate < self._target_rate
            ):
                state.current_rate = min(self._target_rate, state.current_rate * 2.0)
                state.last_recovery_at = now

    def snapshot(self, scope_key: str) -> dict[str, float | int | None]:
        with self._lock:
            now = self._now()
            state = self._state(scope_key, now)
            return {
                "target_rate_per_second": self._target_rate,
                "current_rate_per_second": state.current_rate,
                "burst": self._burst,
                "throttle_count": state.throttle_count,
                "last_limited_at_monotonic": state.last_limited_at,
            }

    def aggregate_snapshot(self) -> dict[str, float | int]:
        with self._lock:
            rates = [state.current_rate for state in self._scopes.values()]
            return {
                "scope_count": len(self._scopes),
                "target_rate_per_second": self._target_rate,
                "minimum_current_rate_per_second": min(rates, default=self._target_rate),
                "throttle_count": sum(state.throttle_count for state in self._scopes.values()),
                "burst": self._burst,
            }
