#!/usr/bin/env python3
"""Aggregate privacy-safe AI-CRM request/query telemetry from stdin."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from array import array
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, TextIO

from aicrm_next.shared.route_ownership import load_route_manifest


_EVENT = "aicrm_request_query_summary"
_LOG_MARKER = "aicrm request query summary"
_FINGERPRINT = re.compile(r"[0-9a-f]{16}")
_ROUTE = re.compile(r"/[A-Za-z0-9_./{}:-]{0,255}")
_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,160}")
_METHODS = {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT", "TRACE"}


def _default_route_manifest(script_path: Path) -> Path:
    return script_path.resolve().parent.parent.parent / "docs" / "architecture" / "route_ownership_manifest.yml"


_DEFAULT_ROUTE_MANIFEST = _default_route_manifest(Path(__file__))


@dataclass(frozen=True)
class ObservedRequest:
    method: str
    route: str
    route_name: str
    capability: str
    status_code: int
    request_duration_ms: float
    query_count: int
    failed_query_count: int
    query_duration_ms: float
    fingerprint_overflow_count: int
    fingerprints: tuple[tuple[str, int], ...]
    timestamp: float | None


@dataclass
class RouteAggregate:
    request_count: int = 0
    status_5xx_count: int = 0
    slow_request_count: int = 0
    total_request_duration_ms: float = 0.0
    max_request_duration_ms: float = 0.0
    request_durations_ms: array = field(default_factory=lambda: array("d"))
    total_query_duration_ms: float = 0.0
    total_query_count: int = 0
    max_query_count: int = 0
    total_failed_query_count: int = 0
    fingerprint_overflow_count: int = 0

    def add(self, event: ObservedRequest) -> None:
        self.request_count += 1
        self.status_5xx_count += int(event.status_code >= 500)
        self.slow_request_count += int(event.request_duration_ms > 250.0)
        self.total_request_duration_ms += event.request_duration_ms
        self.max_request_duration_ms = max(self.max_request_duration_ms, event.request_duration_ms)
        self.request_durations_ms.append(event.request_duration_ms)
        self.total_query_duration_ms += event.query_duration_ms
        self.total_query_count += event.query_count
        self.max_query_count = max(self.max_query_count, event.query_count)
        self.total_failed_query_count += event.failed_query_count
        self.fingerprint_overflow_count += event.fingerprint_overflow_count


@dataclass
class FingerprintAggregate:
    calls: int = 0
    request_count: int = 0
    routes: set[tuple[str, str, str]] = field(default_factory=set)


def _safe_int(value: Any, *, minimum: int, maximum: int) -> int | None:
    if type(value) is not int or not minimum <= value <= maximum:
        return None
    return value


def _safe_float(value: Any, *, maximum: float = 86_400_000.0) -> float | None:
    if type(value) not in {int, float}:
        return None
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0.0 or parsed > maximum:
        return None
    return parsed


def _journal_timestamp(record: dict[str, Any]) -> float | None:
    raw = record.get("__REALTIME_TIMESTAMP") or record.get("_SOURCE_REALTIME_TIMESTAMP")
    if type(raw) not in {str, int}:
        return None
    try:
        microseconds = int(raw)
    except (TypeError, ValueError):
        return None
    return microseconds / 1_000_000.0 if microseconds > 0 else None


def _payload_from_text(text: str) -> tuple[dict[str, Any] | None, bool]:
    if _LOG_MARKER not in text and f'"event": "{_EVENT}"' not in text and f'"event":"{_EVENT}"' not in text:
        return None, False
    start = text.find("{")
    if start < 0:
        return None, True
    try:
        payload = json.loads(text[start:])
    except (TypeError, ValueError):
        return None, True
    return (payload if isinstance(payload, dict) else None), True


def _extract_payload(line: str) -> tuple[dict[str, Any] | None, float | None, bool]:
    stripped = line.strip()
    if not stripped:
        return None, None, False
    try:
        record = json.loads(stripped)
    except (TypeError, ValueError):
        payload, matched = _payload_from_text(stripped)
        return payload, None, matched
    if not isinstance(record, dict):
        return None, None, False
    timestamp = _journal_timestamp(record)
    if record.get("event") == _EVENT:
        return record, timestamp, True
    message = record.get("MESSAGE")
    if isinstance(message, str):
        payload, matched = _payload_from_text(message)
        return payload, timestamp, matched
    return None, timestamp, False


def _allowed_routes(path: str | Path) -> set[tuple[str, str, str, str]]:
    allowed: set[tuple[str, str, str, str]] = set()
    for entry in load_route_manifest(path):
        route = str(entry.get("path") or "")
        route_name = str(entry.get("route_name") or "")
        capability = str(entry.get("capability") or "")
        for method in entry.get("methods") or ():
            allowed.add((str(method).upper(), route, route_name, capability))
    if not allowed:
        raise ValueError("route ownership manifest produced no allowed routes")
    return allowed


def _validated_event(
    payload: dict[str, Any],
    timestamp: float | None,
    allowed_routes: set[tuple[str, str, str, str]],
) -> ObservedRequest | None:
    method = payload.get("method")
    route = payload.get("route")
    route_name = payload.get("route_name")
    capability = payload.get("capability")
    if (
        payload.get("event") != _EVENT
        or not isinstance(method, str)
        or method not in _METHODS
        or not isinstance(route, str)
        or _ROUTE.fullmatch(route) is None
        or not isinstance(route_name, str)
        or _NAME.fullmatch(route_name) is None
        or not isinstance(capability, str)
        or _NAME.fullmatch(capability) is None
        or (method, route, route_name, capability) not in allowed_routes
    ):
        return None

    status_code = _safe_int(payload.get("status_code"), minimum=100, maximum=599)
    request_duration_ms = _safe_float(payload.get("request_duration_ms"))
    query_count = _safe_int(payload.get("query_count"), minimum=0, maximum=1_000_000)
    failed_query_count = _safe_int(payload.get("failed_query_count"), minimum=0, maximum=1_000_000)
    query_duration_ms = _safe_float(payload.get("query_duration_ms"))
    overflow_count = _safe_int(
        payload.get("query_fingerprint_overflow_count"),
        minimum=0,
        maximum=1_000_000,
    )
    if None in {
        status_code,
        request_duration_ms,
        query_count,
        failed_query_count,
        query_duration_ms,
        overflow_count,
    }:
        return None
    assert query_count is not None
    assert failed_query_count is not None
    assert overflow_count is not None
    if failed_query_count > query_count:
        return None

    raw_fingerprints = payload.get("query_fingerprints")
    if not isinstance(raw_fingerprints, list) or len(raw_fingerprints) > 32:
        return None
    fingerprints: list[tuple[str, int]] = []
    seen: set[str] = set()
    for item in raw_fingerprints:
        if not isinstance(item, dict):
            return None
        fingerprint = item.get("fingerprint")
        calls = _safe_int(item.get("calls"), minimum=1, maximum=1_000_000)
        if not isinstance(fingerprint, str) or _FINGERPRINT.fullmatch(fingerprint) is None or calls is None or fingerprint in seen:
            return None
        seen.add(fingerprint)
        fingerprints.append((fingerprint, calls))
    if sum(calls for _, calls in fingerprints) + overflow_count != query_count:
        return None

    return ObservedRequest(
        method=method,
        route=route,
        route_name=route_name,
        capability=capability,
        status_code=status_code,
        request_duration_ms=request_duration_ms,
        query_count=query_count,
        failed_query_count=failed_query_count,
        query_duration_ms=query_duration_ms,
        fingerprint_overflow_count=overflow_count,
        fingerprints=tuple(fingerprints),
        timestamp=timestamp,
    )


def _nearest_rank(values: array, percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return float(ordered[index])


def _utc_timestamp(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def build_report(
    lines: Iterable[str],
    *,
    top: int = 100,
    fingerprint_route_limit: int = 20,
    require_window_hours: float = 0.0,
    require_active_days: int = 0,
    route_manifest: str | Path = _DEFAULT_ROUTE_MANIFEST,
) -> dict[str, Any]:
    allowed_routes = _allowed_routes(route_manifest)
    route_aggregates: dict[tuple[str, str, str, str], RouteAggregate] = {}
    fingerprint_aggregates: dict[str, FingerprintAggregate] = {}
    input_line_count = 0
    accepted_event_count = 0
    rejected_event_count = 0
    non_event_line_count = 0
    timestamped_event_count = 0
    first_timestamp: float | None = None
    last_timestamp: float | None = None
    active_utc_days: set[str] = set()

    for line in lines:
        input_line_count += 1
        payload, timestamp, matched = _extract_payload(line)
        if not matched:
            non_event_line_count += 1
            continue
        if payload is None:
            rejected_event_count += 1
            continue
        event = _validated_event(payload, timestamp, allowed_routes)
        if event is None:
            rejected_event_count += 1
            continue
        accepted_event_count += 1
        if event.timestamp is not None:
            timestamped_event_count += 1
            first_timestamp = event.timestamp if first_timestamp is None else min(first_timestamp, event.timestamp)
            last_timestamp = event.timestamp if last_timestamp is None else max(last_timestamp, event.timestamp)
            active_utc_days.add(datetime.fromtimestamp(event.timestamp, tz=timezone.utc).date().isoformat())

        route_key = (event.method, event.route, event.route_name, event.capability)
        route_aggregates.setdefault(route_key, RouteAggregate()).add(event)
        for fingerprint, calls in event.fingerprints:
            aggregate = fingerprint_aggregates.setdefault(fingerprint, FingerprintAggregate())
            aggregate.calls += calls
            aggregate.request_count += 1
            aggregate.routes.add((event.method, event.route, event.capability))

    route_rows: list[dict[str, Any]] = []
    for (method, route, route_name, capability), aggregate in route_aggregates.items():
        route_rows.append(
            {
                "method": method,
                "route": route,
                "route_name": route_name,
                "capability": capability,
                "request_count": aggregate.request_count,
                "status_5xx_count": aggregate.status_5xx_count,
                "slow_request_count_over_250ms": aggregate.slow_request_count,
                "total_request_duration_ms": round(aggregate.total_request_duration_ms, 3),
                "p95_request_duration_ms": round(_nearest_rank(aggregate.request_durations_ms, 0.95), 3),
                "max_request_duration_ms": round(aggregate.max_request_duration_ms, 3),
                "total_query_duration_ms": round(aggregate.total_query_duration_ms, 3),
                "total_query_count": aggregate.total_query_count,
                "max_query_count": aggregate.max_query_count,
                "total_failed_query_count": aggregate.total_failed_query_count,
                "query_fingerprint_overflow_count": aggregate.fingerprint_overflow_count,
            }
        )
    route_rows.sort(
        key=lambda row: (
            -row["total_request_duration_ms"],
            -row["p95_request_duration_ms"],
            -row["request_count"],
            row["route"],
        )
    )

    fingerprint_rows = []
    for fingerprint, aggregate in fingerprint_aggregates.items():
        routes = sorted(aggregate.routes)
        visible_routes = routes[: max(1, int(fingerprint_route_limit))]
        fingerprint_rows.append(
            {
                "fingerprint": fingerprint,
                "calls": aggregate.calls,
                "request_count": aggregate.request_count,
                "route_count": len(routes),
                "route_overflow_count": len(routes) - len(visible_routes),
                "routes": [{"method": method, "route": route, "capability": capability} for method, route, capability in visible_routes],
            }
        )
    fingerprint_rows.sort(key=lambda row: (-row["calls"], -row["request_count"], row["fingerprint"]))

    observed_hours = 0.0
    if first_timestamp is not None and last_timestamp is not None:
        observed_hours = max(0.0, (last_timestamp - first_timestamp) / 3600.0)
    complete = accepted_event_count > 0
    if require_window_hours > 0:
        complete = complete and timestamped_event_count == accepted_event_count and observed_hours >= require_window_hours
    if require_active_days > 0:
        complete = complete and len(active_utc_days) >= require_active_days

    return {
        "version": 1,
        "source_event": _EVENT,
        "input_line_count": input_line_count,
        "accepted_event_count": accepted_event_count,
        "rejected_event_count": rejected_event_count,
        "non_event_line_count": non_event_line_count,
        "timestamped_event_count": timestamped_event_count,
        "unique_route_count": len(route_rows),
        "unique_fingerprint_count": len(fingerprint_rows),
        "window": {
            "first_event_at": _utc_timestamp(first_timestamp),
            "last_event_at": _utc_timestamp(last_timestamp),
            "observed_hours": round(observed_hours, 3),
            "required_hours": round(float(require_window_hours), 3),
            "active_utc_day_count": len(active_utc_days),
            "required_active_days": int(require_active_days),
            "complete": complete,
        },
        "route_rankings": route_rows[: max(1, int(top))],
        "fingerprint_rankings": fingerprint_rows[: max(1, int(top))],
    }


def _positive_top(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 1_000:
        raise argparse.ArgumentTypeError("top must be between 1 and 1000")
    return parsed


def _fingerprint_route_limit(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 100:
        raise argparse.ArgumentTypeError("fingerprint-route-limit must be between 1 and 100")
    return parsed


def _window_hours(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 8_760.0:
        raise argparse.ArgumentTypeError("require-window-hours must be between 0 and 8760")
    return parsed


def _active_days(value: str) -> int:
    parsed = int(value)
    if not 0 <= parsed <= 366:
        raise argparse.ArgumentTypeError("require-active-days must be between 0 and 366")
    return parsed


def run(
    argv: list[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Aggregate privacy-safe aicrm_request_query_summary journal events from stdin.")
    parser.add_argument("--top", type=_positive_top, default=100)
    parser.add_argument("--fingerprint-route-limit", type=_fingerprint_route_limit, default=20)
    parser.add_argument("--require-window-hours", type=_window_hours, default=0.0)
    parser.add_argument("--require-active-days", type=_active_days, default=0)
    parser.add_argument("--route-manifest", type=Path, default=_DEFAULT_ROUTE_MANIFEST)
    args = parser.parse_args(argv)
    report = build_report(
        stdin or sys.stdin,
        top=args.top,
        fingerprint_route_limit=args.fingerprint_route_limit,
        require_window_hours=args.require_window_hours,
        require_active_days=args.require_active_days,
        route_manifest=args.route_manifest,
    )
    json.dump(report, stdout or sys.stdout, ensure_ascii=True, indent=2, sort_keys=True)
    (stdout or sys.stdout).write("\n")
    return 0 if report["window"]["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(run())
