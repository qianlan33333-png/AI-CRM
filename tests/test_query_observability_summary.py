from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import sys

from scripts.ops.summarize_query_observability import _default_route_manifest, build_report, run


ROOT = Path(__file__).resolve().parents[1]


def _event(
    *,
    route: str = "/api/sidebar/v2/timeline",
    route_name: str = "get_sidebar_v2_timeline",
    capability: str = "sidebar_read",
    duration_ms: float = 10.0,
    query_count: int = 2,
    fingerprint: str = "0123456789abcdef",
    fingerprint_calls: int = 2,
) -> dict:
    return {
        "event": "aicrm_request_query_summary",
        "method": "GET",
        "route": route,
        "route_name": route_name,
        "capability": capability,
        "status_code": 200,
        "request_duration_ms": duration_ms,
        "query_count": query_count,
        "failed_query_count": 0,
        "query_duration_ms": duration_ms / 2,
        "query_fingerprint_overflow_count": 0,
        "query_fingerprints": [
            {"fingerprint": fingerprint, "calls": fingerprint_calls},
        ],
    }


def _journal_line(event: dict, timestamp_us: int) -> str:
    return json.dumps(
        {
            "__REALTIME_TIMESTAMP": str(timestamp_us),
            "MESSAGE": "aicrm request query summary " + json.dumps(event),
        }
    )


def test_default_manifest_path_is_safe_for_a_shallow_temporary_copy() -> None:
    assert _default_route_manifest(Path("/tmp/tool.py")) == Path("/docs/architecture/route_ownership_manifest.yml")


def test_cli_can_run_directly_outside_the_repository(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "ops" / "summarize_query_observability.py"),
            "--route-manifest",
            str(ROOT / "docs" / "architecture" / "route_ownership_manifest.yml"),
        ],
        cwd=tmp_path,
        input=json.dumps(_event()),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["accepted_event_count"] == 1


def test_build_report_ranks_routes_and_fingerprints_without_sql() -> None:
    lines = [
        json.dumps(_event(duration_ms=10.0)),
        json.dumps(_event(duration_ms=40.0)),
        json.dumps(
            _event(
                route="/api/customers",
                route_name="list_customers",
                capability="read_customer",
                duration_ms=100.0,
                query_count=1,
                fingerprint="fedcba9876543210",
                fingerprint_calls=1,
            )
        ),
    ]

    report = build_report(lines, top=20)

    assert report["accepted_event_count"] == 3
    assert report["rejected_event_count"] == 0
    assert report["route_rankings"][0] == {
        "method": "GET",
        "route": "/api/customers",
        "route_name": "list_customers",
        "capability": "read_customer",
        "request_count": 1,
        "status_5xx_count": 0,
        "slow_request_count_over_250ms": 0,
        "total_request_duration_ms": 100.0,
        "p95_request_duration_ms": 100.0,
        "max_request_duration_ms": 100.0,
        "total_query_duration_ms": 50.0,
        "total_query_count": 1,
        "max_query_count": 1,
        "total_failed_query_count": 0,
        "query_fingerprint_overflow_count": 0,
    }
    timeline = report["route_rankings"][1]
    assert timeline["request_count"] == 2
    assert timeline["total_request_duration_ms"] == 50.0
    assert timeline["p95_request_duration_ms"] == 40.0
    assert report["fingerprint_rankings"][0]["fingerprint"] == "0123456789abcdef"
    assert report["fingerprint_rankings"][0]["calls"] == 4
    assert report["fingerprint_rankings"][0]["request_count"] == 2
    assert report["fingerprint_rankings"][0]["route_count"] == 1
    assert report["fingerprint_rankings"][0]["route_overflow_count"] == 0


def test_fingerprint_route_lists_are_bounded() -> None:
    lines = [
        json.dumps(_event()),
        json.dumps(
            _event(
                route="/api/customers",
                route_name="list_customers",
                capability="read_customer",
            )
        ),
    ]

    report = build_report(lines, fingerprint_route_limit=1)
    fingerprint = report["fingerprint_rankings"][0]

    assert fingerprint["route_count"] == 2
    assert len(fingerprint["routes"]) == 1
    assert fingerprint["route_overflow_count"] == 1


def test_build_report_understands_journal_timestamps_and_enforces_window() -> None:
    start_us = 1_700_000_000_000_000
    end_us = start_us + 8 * 24 * 60 * 60 * 1_000_000
    lines = [
        _journal_line(_event(), start_us),
        _journal_line(_event(), end_us),
    ]

    report = build_report(lines, require_window_hours=168)

    assert report["timestamped_event_count"] == 2
    assert report["window"]["observed_hours"] == 192.0
    assert report["window"]["required_hours"] == 168.0
    assert report["window"]["complete"] is True
    assert report["window"]["first_event_at"].endswith("Z")
    assert report["window"]["last_event_at"].endswith("Z")


def test_build_report_can_require_events_across_seven_active_utc_days() -> None:
    start_us = 1_700_000_000_000_000
    lines = [_journal_line(_event(), start_us + day * 24 * 60 * 60 * 1_000_000) for day in range(8)]

    report = build_report(
        lines,
        require_window_hours=168,
        require_active_days=7,
    )

    assert report["window"]["active_utc_day_count"] == 8
    assert report["window"]["required_active_days"] == 7
    assert report["window"]["complete"] is True


def test_report_never_echoes_unknown_or_identifying_input_fields() -> None:
    event = _event()
    event.update(
        {
            "external_userid": "wm-sensitive-customer",
            "unionid": "on-sensitive-union",
            "phone": "13800138000",
            "sql": "SELECT secret FROM customer",
        }
    )
    line = json.dumps(
        {
            "MESSAGE": "aicrm request query summary " + json.dumps(event),
            "REMOTE_ADDR": "192.0.2.1",
            "HTTP_COOKIE": "session=secret",
        }
    )

    encoded = json.dumps(build_report([line]), sort_keys=True)

    for forbidden in (
        "wm-sensitive-customer",
        "on-sensitive-union",
        "13800138000",
        "SELECT secret",
        "192.0.2.1",
        "session=secret",
        "external_userid",
        "unionid",
    ):
        assert forbidden not in encoded


def test_invalid_route_or_fingerprint_is_rejected_without_echoing_value() -> None:
    unsafe_route = _event(route="/api/customers?external_userid=wm-secret")
    unregistered_route = _event(route="/api/sidebar/v2/timeline/wm-secret")
    unsafe_fingerprint = _event(fingerprint="not-a-digest")

    report = build_report([json.dumps(unsafe_route), json.dumps(unregistered_route), json.dumps(unsafe_fingerprint)])
    encoded = json.dumps(report, sort_keys=True)

    assert report["accepted_event_count"] == 0
    assert report["rejected_event_count"] == 3
    assert "wm-secret" not in encoded
    assert "not-a-digest" not in encoded


def test_fingerprint_calls_must_reconcile_to_request_query_count() -> None:
    event = _event(query_count=3, fingerprint_calls=2)

    report = build_report(["unrelated journal line", json.dumps(event)])

    assert report["input_line_count"] == 2
    assert report["non_event_line_count"] == 1
    assert report["accepted_event_count"] == 0
    assert report["rejected_event_count"] == 1


def test_cli_fails_closed_when_required_baseline_window_is_incomplete() -> None:
    start_us = 1_700_000_000_000_000
    end_us = start_us + 24 * 60 * 60 * 1_000_000
    stdin = io.StringIO(
        "\n".join(
            [
                _journal_line(_event(), start_us),
                _journal_line(_event(), end_us),
            ]
        )
    )
    stdout = io.StringIO()

    exit_code = run(
        [
            "--require-window-hours",
            "168",
            "--require-active-days",
            "7",
            "--fingerprint-route-limit",
            "5",
        ],
        stdin=stdin,
        stdout=stdout,
    )
    report = json.loads(stdout.getvalue())

    assert exit_code == 2
    assert report["window"]["complete"] is False
    assert report["window"]["observed_hours"] == 24.0
    assert report["window"]["active_utc_day_count"] == 2
