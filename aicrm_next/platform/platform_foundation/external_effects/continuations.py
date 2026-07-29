from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from .models import ExternalEffectDispatchResult, ExternalEffectJob

ContinuationPredicate = Callable[[ExternalEffectJob, ExternalEffectDispatchResult], bool]
ContinuationHandler = Callable[[ExternalEffectJob, ExternalEffectDispatchResult], Mapping[str, Any]]


@dataclass(frozen=True)
class ExternalEffectContinuation:
    name: str
    matches: ContinuationPredicate
    run: ContinuationHandler
    requires_provider_result: bool = False


@dataclass(frozen=True)
class ExternalEffectContinuationConsumer:
    """Bind one continuation to one durable Internal Event consumer run."""

    consumer_name: str
    continuation: ExternalEffectContinuation
    max_attempts: int = 5

    def __post_init__(self) -> None:
        consumer_name = str(self.consumer_name or "").strip()
        if not consumer_name:
            raise ValueError("external effect continuation consumer name is required")
        if not isinstance(self.continuation, ExternalEffectContinuation):
            raise ValueError("external effect continuation consumer requires a continuation")
        object.__setattr__(self, "consumer_name", consumer_name)
        object.__setattr__(self, "max_attempts", max(1, int(self.max_attempts or 5)))


def run_external_effect_continuation(
    continuation: ExternalEffectContinuation,
    job: ExternalEffectJob,
    dispatch_result: ExternalEffectDispatchResult,
    *,
    provider_result_loader: Callable[[], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute exactly one continuation without selecting a sibling handler."""

    try:
        matches = continuation.matches(job, dispatch_result)
    except Exception as exc:
        return {
            "applicable": True,
            "ok": False,
            "continuation": continuation.name,
            "error": f"continuation predicate failed: {exc.__class__.__name__}",
        }
    if not matches:
        return {
            "applicable": False,
            "ok": True,
            "continuation": continuation.name,
            "reason": "continuation_not_applicable",
        }
    if continuation.requires_provider_result and not dict(dispatch_result.provider_result or {}):
        try:
            provider_result = dict(provider_result_loader() or {}) if provider_result_loader is not None else {}
        except Exception as exc:
            return {
                "applicable": True,
                "ok": False,
                "continuation": continuation.name,
                "error": f"continuation provider result load failed: {exc.__class__.__name__}",
            }
        dispatch_result = replace(dispatch_result, provider_result=provider_result)
    try:
        result = dict(continuation.run(job, dispatch_result))
    except Exception as exc:
        return {
            "applicable": True,
            "ok": False,
            "continuation": continuation.name,
            "error": f"continuation handler failed: {exc.__class__.__name__}",
        }
    return {**result, "applicable": True, "continuation": continuation.name}


class ExternalEffectContinuationRegistry:
    def __init__(self, continuations: Iterable[ExternalEffectContinuation] = ()) -> None:
        registered = tuple(continuations)
        names = tuple(item.name for item in registered)
        if any(not name for name in names):
            raise ValueError("external effect continuation name is required")
        if len(set(names)) != len(names):
            raise ValueError("external effect continuation names must be unique")
        self._continuations = registered

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self._continuations)

    def run(
        self,
        job: ExternalEffectJob,
        dispatch_result: ExternalEffectDispatchResult,
        *,
        provider_result_loader: Callable[[], Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Run the historical first-match chain.

        New ``external_effect.completed`` events must bind each continuation to
        an independent Internal Event consumer. This method remains only for
        legacy consumer-run aliases created before that fan-out contract.
        """

        for continuation in self._continuations:
            result = run_external_effect_continuation(
                continuation,
                job,
                dispatch_result,
                provider_result_loader=provider_result_loader,
            )
            if not result.get("applicable"):
                continue
            return result
        return {"applicable": False, "reason": "no_registered_continuation"}


EMPTY_EXTERNAL_EFFECT_CONTINUATION_REGISTRY = ExternalEffectContinuationRegistry()
