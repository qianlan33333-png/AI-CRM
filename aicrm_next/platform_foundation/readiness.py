from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from aicrm_next.shared.wecom_runtime import load_wecom_execution_config
from aicrm_next.shared.release import current_release_sha
from aicrm_next.shared.runtime import production_environment, raw_database_url, runtime_setting
from aicrm_next.platform_foundation.internal_events.config import (
    allowed_consumers,
    allowed_event_consumer_pairs,
    allowed_event_types,
)

from .repository import ConnectionFactory, RuntimeReadinessRepository


ROOT = Path(__file__).resolve().parents[2]
FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _component(status: str, *, critical: bool, **details: Any) -> dict[str, Any]:
    return {"status": status, "critical": critical, **details}


def _safe_error(exc: Exception) -> str:
    return exc.__class__.__name__


def _threshold(name: str, default: int) -> int:
    try:
        return max(0, int(runtime_setting(name, str(default)) or default))
    except (TypeError, ValueError):
        return default


def _expected_migration_heads() -> tuple[str, ...]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return tuple(sorted(ScriptDirectory.from_config(config).get_heads()))


def _probe_database(repository: RuntimeReadinessRepository) -> dict[str, Any]:
    if not repository.ping():
        raise RuntimeError("database ping returned an invalid result")
    return _component("ok", critical=True, ping=True, driver="psycopg")


def _probe_migration(repository: RuntimeReadinessRepository, expected_heads: tuple[str, ...]) -> dict[str, Any]:
    current = repository.migration_revisions()
    matches = current == tuple(sorted(expected_heads))
    return _component(
        "ok" if matches else "failed",
        critical=True,
        current_revisions=list(current),
        expected_heads=list(sorted(expected_heads)),
        matches_head=matches,
    )


def _probe_queues(repository: RuntimeReadinessRepository) -> dict[str, Any]:
    metrics = repository.queue_metrics(
        allowed_pairs=tuple(allowed_event_consumer_pairs()),
        allowed_event_types=tuple(allowed_event_types()),
        allowed_consumers=tuple(allowed_consumers()),
    )
    actionable_internal_age = metrics.get(
        "internal_event_actionable_oldest_pending_age_seconds",
        metrics.get("internal_event_oldest_pending_age_seconds", 0),
    )
    max_age = max(
        metrics.get("webhook_eligible_oldest_pending_age_seconds", metrics.get("webhook_oldest_pending_age_seconds", 0)),
        actionable_internal_age,
        metrics.get("external_effect_eligible_oldest_pending_age_seconds", metrics.get("external_effect_oldest_pending_age_seconds", 0)),
    )
    terminal_count = (
        metrics.get("webhook_dead_letter_count", 0)
        + metrics.get("internal_event_actionable_terminal_count", metrics.get("internal_event_terminal_count", 0))
        + metrics.get("external_effect_terminal_count", 0)
    )
    max_age_threshold = _threshold("AICRM_READINESS_MAX_QUEUE_AGE_SECONDS", 3600)
    terminal_threshold = _threshold("AICRM_READINESS_MAX_TERMINAL_COUNT", 0)
    warnings: list[str] = []
    if max_age > max_age_threshold:
        warnings.append("oldest_pending_age_exceeded")
    if terminal_count > terminal_threshold:
        warnings.append("terminal_or_dead_letter_count_exceeded")
    return _component(
        "warning" if warnings else "ok",
        critical=True,
        metrics=metrics,
        thresholds={
            "max_queue_age_seconds": max_age_threshold,
            "max_terminal_count": terminal_threshold,
        },
        warnings=warnings,
    )


def _probe_wecom(diagnostics: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(diagnostics or load_wecom_execution_config().diagnostics())
    conflict = bool(payload.get("conflict"))
    return _component(
        "failed" if conflict else "ok",
        critical=True,
        execution_mode=str(payload.get("execution_mode") or "disabled"),
        execution_mode_source=str(payload.get("execution_mode_source") or "unknown"),
        real_calls_enabled=bool(payload.get("enabled")),
        conflict=conflict,
        blocking_reasons=list(payload.get("blocking_reasons") or []),
    )


def _probe_release(release_sha: str, *, production: bool) -> dict[str, Any]:
    exact = bool(FULL_SHA_PATTERN.fullmatch(release_sha))
    return _component(
        "ok" if exact else ("failed" if production else "warning"),
        critical=True,
        release_sha=release_sha,
        exact_sha=exact,
    )


def runtime_readiness_payload(
    *,
    database_url: str | None = None,
    connection_factory: ConnectionFactory | None = None,
    expected_heads: tuple[str, ...] | None = None,
    wecom_diagnostics: dict[str, Any] | None = None,
    release_sha: str | None = None,
    production: bool | None = None,
) -> dict[str, Any]:
    url = raw_database_url() if database_url is None else str(database_url or "").strip()
    is_production = production_environment() if production is None else bool(production)
    components: dict[str, dict[str, Any]] = {
        "wecom": _probe_wecom(wecom_diagnostics),
        "release": _probe_release(str(release_sha or current_release_sha()), production=is_production),
        "runtime_units": _component(
            "external_gate",
            critical=False,
            source="scripts/ops/manage_production_runtime_units.py --phase verify --execute",
            heartbeat_source="systemd_unit_verification",
        ),
    }
    if not url:
        missing_status = "failed" if is_production else "fixture"
        components["database"] = _component(missing_status, critical=True, configured=False, ping=False)
        components["migration"] = _component(missing_status, critical=True, matches_head=False)
        components["queues"] = _component(missing_status, critical=True, metrics={}, warnings=[])
    else:
        try:
            with RuntimeReadinessRepository(url, connection_factory=connection_factory) as repository:
                try:
                    components["database"] = _probe_database(repository)
                except Exception as exc:
                    components["database"] = _component("failed", critical=True, ping=False, error_class=_safe_error(exc))
                try:
                    heads = tuple(expected_heads) if expected_heads is not None else _expected_migration_heads()
                    components["migration"] = _probe_migration(repository, heads)
                except Exception as exc:
                    components["migration"] = _component("failed", critical=True, matches_head=False, error_class=_safe_error(exc))
                try:
                    components["queues"] = _probe_queues(repository)
                except Exception as exc:
                    components["queues"] = _component("failed", critical=True, metrics={}, warnings=[], error_class=_safe_error(exc))
        except Exception as exc:
            failure = _component("failed", critical=True, error_class=_safe_error(exc))
            components["database"] = dict(failure, ping=False)
            components["migration"] = dict(failure, matches_head=False)
            components["queues"] = dict(failure, metrics={}, warnings=[])

    failed_components = sorted(
        name
        for name, component in components.items()
        if component.get("critical") is True and component.get("status") == "failed"
    )
    warning_components = sorted(name for name, component in components.items() if component.get("status") == "warning")
    ok = not failed_components
    return {
        "ok": ok,
        "status": "ready" if ok else "not_ready",
        "http_status": 200 if ok else 503,
        "failed_components": failed_components,
        "warning_components": warning_components,
        "components": components,
        "pii_in_output": False,
        "secrets_in_output": False,
    }


__all__ = ["runtime_readiness_payload"]
