from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from threading import Condition
from time import monotonic
from typing import Callable, Protocol

from aicrm_next.platform.shared.runtime import database_mode

from .repo import FixtureIdentityRepository, PostgresIdentityRepository


class SidebarFollowRelationRepository(Protocol):
    def has_active_follow_relation(self, *, corp_id: str, user_id: str, external_userid: str) -> bool: ...


@dataclass(frozen=True)
class _CacheEntry:
    allowed: bool
    expires_at: float


class SidebarAuthorizationService:
    """Verify and briefly cache the authoritative active WeCom follow relation."""

    def __init__(
        self,
        repository: SidebarFollowRelationRepository,
        *,
        positive_ttl_seconds: float = 300,
        negative_ttl_seconds: float = 30,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._repository = repository
        self._positive_ttl_seconds = max(0.0, float(positive_ttl_seconds))
        self._negative_ttl_seconds = max(0.0, float(negative_ttl_seconds))
        self._clock = clock
        self._condition = Condition()
        self._cache: dict[tuple[str, str, str], _CacheEntry] = {}
        self._inflight: set[tuple[str, str, str]] = set()
        self._metrics: Counter[str] = Counter()

    def authorize(self, *, corp_id: str, user_id: str, external_userid: str) -> bool:
        key = self._key(corp_id=corp_id, user_id=user_id, external_userid=external_userid)
        if not all(key):
            with self._condition:
                self._metrics["invalid_input"] += 1
            return False

        owner = False
        with self._condition:
            while True:
                now = self._clock()
                cached = self._cache.get(key)
                if cached and cached.expires_at > now:
                    self._metrics["cache_hit_allowed" if cached.allowed else "cache_hit_denied"] += 1
                    return cached.allowed
                if cached:
                    self._cache.pop(key, None)
                if key not in self._inflight:
                    self._inflight.add(key)
                    self._metrics["cache_miss"] += 1
                    owner = True
                    break
                self._metrics["singleflight_wait"] += 1
                self._condition.wait(timeout=5.0)

        if not owner:
            return False
        allowed = False
        failed = False
        try:
            allowed = bool(
                self._repository.has_active_follow_relation(
                    corp_id=key[0],
                    user_id=key[1],
                    external_userid=key[2],
                )
            )
        except Exception:
            failed = True
            allowed = False
        finally:
            with self._condition:
                ttl = self._positive_ttl_seconds if allowed else self._negative_ttl_seconds
                self._cache[key] = _CacheEntry(allowed=allowed, expires_at=self._clock() + ttl)
                self._inflight.discard(key)
                self._metrics["repository_error" if failed else ("relation_allowed" if allowed else "relation_denied")] += 1
                self._condition.notify_all()
        return allowed

    def invalidate(self, *, corp_id: str, user_id: str, external_userid: str) -> None:
        key = self._key(corp_id=corp_id, user_id=user_id, external_userid=external_userid)
        with self._condition:
            self._cache.pop(key, None)
            self._metrics["invalidated"] += 1

    def record_oauth_callback(self, *, repeated: bool) -> None:
        with self._condition:
            self._metrics["oauth_callbacks"] += 1
            if repeated:
                self._metrics["oauth_repeat_callbacks"] += 1

    def snapshot(self) -> dict[str, int]:
        with self._condition:
            now = self._clock()
            active_entries = sum(1 for entry in self._cache.values() if entry.expires_at > now)
            return {
                "active_entries": active_entries,
                "inflight": len(self._inflight),
                **{key: int(value) for key, value in sorted(self._metrics.items())},
            }

    @staticmethod
    def _key(*, corp_id: str, user_id: str, external_userid: str) -> tuple[str, str, str]:
        return tuple(str(value or "").strip() for value in (corp_id, user_id, external_userid))  # type: ignore[return-value]


_DEFAULT_SERVICE: SidebarAuthorizationService | None = None


def build_sidebar_authorization_service() -> SidebarAuthorizationService:
    global _DEFAULT_SERVICE
    if _DEFAULT_SERVICE is None:
        repository: SidebarFollowRelationRepository
        repository = PostgresIdentityRepository() if database_mode() == "postgres" else FixtureIdentityRepository()
        _DEFAULT_SERVICE = SidebarAuthorizationService(repository)
    return _DEFAULT_SERVICE


def sidebar_authorization_snapshot() -> dict[str, int]:
    return build_sidebar_authorization_service().snapshot()


__all__ = [
    "SidebarAuthorizationService",
    "build_sidebar_authorization_service",
    "sidebar_authorization_snapshot",
]
