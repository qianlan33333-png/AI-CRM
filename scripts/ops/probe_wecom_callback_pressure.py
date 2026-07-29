#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import sys
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlsplit
from urllib.request import Request, urlopen

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()


DEFAULT_CALLBACK_URL = "http://127.0.0.1:5002/wecom/external-contact/callback"
DEFAULT_HEALTH_URL = "http://127.0.0.1:5001/health"
DEFAULT_SIDEBAR_URL = "http://127.0.0.1:5001/sidebar/bind-mobile"
DEFAULT_ADMIN_URL = "http://127.0.0.1:5001/admin/automation-conversion"


@dataclass(frozen=True)
class ProbeResult:
    label: str
    method: str
    url: str
    status_code: int | None
    latency_ms: float
    error: str = ""


@dataclass(frozen=True)
class SampleTarget:
    label: str
    url: str
    method: str = "GET"
    target_p95_ms: float = 500.0
    expected_status_min: int = 200
    expected_status_max: int = 499


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(float(ordered[0]), 3)
    clamped = min(max(float(percent), 0.0), 100.0)
    position = (len(ordered) - 1) * clamped / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    value = ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
    return round(float(value), 3)


def parse_sample_target(value: str) -> SampleTarget:
    parts = value.split(",")
    first = parts[0].strip()
    if "=" not in first:
        raise argparse.ArgumentTypeError("sample target must start with label=url")
    label, url = first.split("=", 1)
    options: dict[str, float | int] = {"target_p95_ms": 500.0, "expected_status_min": 200, "expected_status_max": 499}
    for part in parts[1:]:
        if not part.strip():
            continue
        if "=" not in part:
            raise argparse.ArgumentTypeError(f"invalid sample target option: {part}")
        key, raw_value = [item.strip() for item in part.split("=", 1)]
        if key not in options:
            raise argparse.ArgumentTypeError(f"unsupported sample target option: {key}")
        options[key] = float(raw_value) if key == "target_p95_ms" else int(raw_value)
    return SampleTarget(
        label=label.strip(),
        url=url.strip(),
        target_p95_ms=float(options["target_p95_ms"]),
        expected_status_min=int(options["expected_status_min"]),
        expected_status_max=int(options["expected_status_max"]),
    )


def _request(method: str, url: str, *, body: bytes, timeout_seconds: float, label: str) -> ProbeResult:
    start = time.perf_counter()
    data = body if method.upper() != "GET" else None
    request = Request(
        url,
        data=data,
        method=method.upper(),
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "User-Agent": "aicrm-wecom-callback-pressure-probe/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            response.read(2048)
            latency_ms = (time.perf_counter() - start) * 1000
            return ProbeResult(label=label, method=method.upper(), url=url, status_code=int(response.status), latency_ms=latency_ms)
    except HTTPError as exc:
        exc.read(2048)
        latency_ms = (time.perf_counter() - start) * 1000
        return ProbeResult(label=label, method=method.upper(), url=url, status_code=int(exc.code), latency_ms=latency_ms)
    except (OSError, URLError) as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return ProbeResult(label=label, method=method.upper(), url=url, status_code=None, latency_ms=latency_ms, error=str(exc))


def summarize_results(
    results: list[ProbeResult],
    *,
    expected_status_min: int,
    expected_status_max: int,
    target_p95_ms: float,
    target_p99_ms: float | None = None,
) -> dict[str, Any]:
    latencies = [float(item.latency_ms) for item in results if not item.error]
    status_counts = Counter(str(item.status_code) if item.status_code is not None else "error" for item in results)
    ok_count = sum(
        1
        for item in results
        if not item.error
        and item.status_code is not None
        and expected_status_min <= int(item.status_code) <= expected_status_max
    )
    errors = Counter(item.error for item in results if item.error)
    p95 = percentile(latencies, 95)
    p99 = percentile(latencies, 99)
    summary: dict[str, Any] = {
        "request_count": len(results),
        "ok_count": ok_count,
        "failure_count": len(results) - ok_count,
        "status_counts": dict(sorted(status_counts.items())),
        "latency_ms": {
            "p50": percentile(latencies, 50),
            "p95": p95,
            "p99": p99,
            "max": round(max(latencies), 3) if latencies else None,
        },
        "error_counts": dict(errors.most_common(10)),
        "target_p95_ms": target_p95_ms,
        "target_p99_ms": target_p99_ms,
        "meets_status_target": ok_count == len(results) and len(results) > 0,
        "meets_p95_target": p95 is not None and p95 <= target_p95_ms,
    }
    if target_p99_ms is not None:
        summary["meets_p99_target"] = p99 is not None and p99 <= target_p99_ms
    return summary


def _read_body(args: argparse.Namespace) -> bytes:
    if args.callback_body_file:
        return Path(args.callback_body_file).read_bytes()
    if args.callback_body_text is not None:
        return str(args.callback_body_text).encode("utf-8")
    raise SystemExit("--callback-body-file or --callback-body-text is required")


def validate_callback_sample(callback_url: str, body: bytes) -> dict[str, Any]:
    parsed = urlsplit(str(callback_url))
    query = {key: value for key, value in parse_qsl(parsed.query, keep_blank_values=True)}
    missing = [key for key in ("timestamp", "nonce", "msg_signature") if not str(query.get(key) or "").strip()]
    if missing:
        return {
            "checked": True,
            "ok": False,
            "event_summary": {},
            "idempotency_key": "",
            "plain_xml_bytes": 0,
            "error": "callback URL missing required query params: " + ", ".join(missing),
        }
    try:
        from aicrm_next.channels.channel_entry.application import decrypt_callback_body
        from aicrm_next.channels.channel_entry.inbox import wecom_callback_idempotency_key

        event_data, plain_xml = decrypt_callback_body(query=query, body=body)
    except Exception as exc:
        return {
            "checked": True,
            "ok": False,
            "event_summary": {},
            "idempotency_key": "",
            "plain_xml_bytes": 0,
            "error": str(exc),
        }
    corp_id = str(event_data.get("ToUserName") or "")
    idempotency_key = wecom_callback_idempotency_key(corp_id, event_data)
    return {
        "checked": True,
        "ok": True,
        "event_summary": {
            "ToUserName": corp_id,
            "Event": str(event_data.get("Event") or ""),
            "ChangeType": str(event_data.get("ChangeType") or ""),
            "ExternalUserID_present": bool(str(event_data.get("ExternalUserID") or "").strip()),
            "UserID_present": bool(str(event_data.get("UserID") or "").strip()),
            "WelcomeCode_present": bool(str(event_data.get("WelcomeCode") or "").strip()),
            "State_present": bool(str(event_data.get("State") or "").strip()),
        },
        "idempotency_key": idempotency_key,
        "plain_xml_bytes": len(plain_xml.encode("utf-8")),
        "error": "",
    }


def _default_targets(args: argparse.Namespace) -> list[SampleTarget]:
    if args.no_default_samples:
        return []
    return [
        SampleTarget("health", str(args.health_url), target_p95_ms=float(args.health_target_p95_ms), expected_status_min=200, expected_status_max=299),
        SampleTarget("sidebar_bind_mobile", str(args.sidebar_url), target_p95_ms=float(args.sidebar_target_p95_ms)),
        SampleTarget("automation_conversion_admin", str(args.admin_url), target_p95_ms=float(args.admin_target_p95_ms)),
    ]


def _sample_once(targets: list[SampleTarget], timeout_seconds: float, sink: list[ProbeResult]) -> None:
    for target in targets:
        sink.append(_request(target.method, target.url, body=b"", timeout_seconds=timeout_seconds, label=target.label))


def run_pressure(args: argparse.Namespace) -> dict[str, Any]:
    body = _read_body(args)
    sample_validation = (
        validate_callback_sample(str(args.callback_url), body)
        if bool(args.require_valid_callback_sample)
        else {"checked": False, "ok": None, "event_summary": {}, "plain_xml_bytes": 0, "error": "sample validation skipped"}
    )
    if args.require_valid_callback_sample and sample_validation.get("ok") is not True:
        return {
            "ok": False,
            "real_external_call_executed": False,
            "callback_url": str(args.callback_url),
            "pressure": {
                "requested_rate_per_minute": float(args.rate_per_minute),
                "total_requests": 0,
                "elapsed_seconds": 0.0,
                "observed_rate_per_minute": 0.0,
                "concurrency": int(args.concurrency),
            },
            "sample_validation": sample_validation,
            "callback": summarize_results([], expected_status_min=int(args.callback_expected_status), expected_status_max=int(args.callback_expected_status), target_p95_ms=float(args.callback_target_p95_ms), target_p99_ms=float(args.callback_target_p99_ms)),
            "page_samples": {},
            "warnings": ["valid encrypted WeCom callback sample validation failed; pressure probe was not executed"],
            "notes": [
                "This probe only sends HTTP requests to the supplied endpoints.",
                "Use a valid captured WeCom callback query/body in staging or during an approved production canary to prove 200 app-level ACK.",
                "Realtime external effects remain governed by runtime gates; this probe does not enable them.",
            ],
        }
    total_requests = int(args.total_requests) if args.total_requests is not None else max(
        1, int(round(float(args.rate_per_minute) * float(args.duration_seconds) / 60.0))
    )
    rate_per_second = max(float(args.rate_per_minute) / 60.0, 0.001)
    callback_results: list[ProbeResult] = []
    sample_results: list[ProbeResult] = []
    sample_targets = _default_targets(args) + list(args.sample_url or [])
    stop_sampling = threading.Event()

    def sampler() -> None:
        if not sample_targets:
            return
        if float(args.sample_interval_seconds) <= 0:
            _sample_once(sample_targets, float(args.timeout_seconds), sample_results)
            return
        while not stop_sampling.is_set():
            _sample_once(sample_targets, float(args.timeout_seconds), sample_results)
            stop_sampling.wait(float(args.sample_interval_seconds))

    sample_thread = threading.Thread(target=sampler, name="aicrm-callback-pressure-sampler", daemon=True)
    sample_thread.start()

    start = time.perf_counter()
    futures = []
    with ThreadPoolExecutor(max_workers=int(args.concurrency)) as executor:
        for index in range(total_requests):
            due_at = start + (index / rate_per_second)
            delay = due_at - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            futures.append(
                executor.submit(
                    _request,
                    "POST",
                    str(args.callback_url),
                    body=body,
                    timeout_seconds=float(args.timeout_seconds),
                    label="callback",
                )
            )
        for future in as_completed(futures):
            callback_results.append(future.result())
    stop_sampling.set()
    sample_thread.join(timeout=max(float(args.timeout_seconds), 1.0))
    elapsed_seconds = time.perf_counter() - start

    callback_summary = summarize_results(
        callback_results,
        expected_status_min=int(args.callback_expected_status),
        expected_status_max=int(args.callback_expected_status),
        target_p95_ms=float(args.callback_target_p95_ms),
        target_p99_ms=float(args.callback_target_p99_ms),
    )
    samples_by_label: dict[str, list[ProbeResult]] = {}
    for result in sample_results:
        samples_by_label.setdefault(result.label, []).append(result)
    sample_summaries: dict[str, Any] = {}
    for target in sample_targets:
        sample_summaries[target.label] = summarize_results(
            samples_by_label.get(target.label, []),
            expected_status_min=int(target.expected_status_min),
            expected_status_max=int(target.expected_status_max),
            target_p95_ms=float(target.target_p95_ms),
        )
        sample_summaries[target.label]["url"] = target.url

    sample_targets_ok = all(
        item.get("meets_status_target") and item.get("meets_p95_target")
        for item in sample_summaries.values()
    )
    callback_ok = bool(
        callback_summary.get("meets_status_target")
        and callback_summary.get("meets_p95_target")
        and callback_summary.get("meets_p99_target")
    )
    warnings: list[str] = []
    if str(args.callback_url) == DEFAULT_CALLBACK_URL and int(args.callback_expected_status) == 200:
        warnings.append("default local callback URL expects a valid encrypted WeCom payload and query string to return 200")
    if not sample_targets:
        warnings.append("page sampling disabled; this run does not prove web/admin availability during pressure")
    if callback_summary["request_count"] < 1:
        warnings.append("no callback requests were sent")

    return {
        "ok": callback_ok and sample_targets_ok,
        "real_external_call_executed": False,
        "callback_url": str(args.callback_url),
        "sample_validation": sample_validation,
        "pressure": {
            "requested_rate_per_minute": float(args.rate_per_minute),
            "total_requests": total_requests,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "observed_rate_per_minute": round((len(callback_results) / elapsed_seconds) * 60.0, 3) if elapsed_seconds else None,
            "concurrency": int(args.concurrency),
        },
        "callback": callback_summary,
        "page_samples": sample_summaries,
        "warnings": warnings,
        "notes": [
            "This probe only sends HTTP requests to the supplied endpoints.",
            "Use a valid captured WeCom callback query/body in staging or during an approved production canary to prove 200 app-level ACK.",
            "Realtime external effects remain governed by runtime gates; this probe does not enable them.",
        ],
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe WeCom callback ACK latency while sampling critical web/admin routes.")
    parser.add_argument("--callback-url", default=DEFAULT_CALLBACK_URL)
    parser.add_argument("--callback-body-file", default="")
    parser.add_argument("--callback-body-text")
    parser.add_argument("--callback-expected-status", type=int, default=200)
    parser.add_argument("--require-valid-callback-sample", action="store_true", default=False)
    parser.add_argument("--rate-per-minute", type=float, default=1200.0)
    parser.add_argument("--duration-seconds", type=float, default=60.0)
    parser.add_argument("--total-requests", type=int)
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--timeout-seconds", type=float, default=3.0)
    parser.add_argument("--callback-target-p95-ms", type=float, default=200.0)
    parser.add_argument("--callback-target-p99-ms", type=float, default=500.0)
    parser.add_argument("--health-url", default=DEFAULT_HEALTH_URL)
    parser.add_argument("--sidebar-url", default=DEFAULT_SIDEBAR_URL)
    parser.add_argument("--admin-url", default=DEFAULT_ADMIN_URL)
    parser.add_argument("--health-target-p95-ms", type=float, default=100.0)
    parser.add_argument("--sidebar-target-p95-ms", type=float, default=300.0)
    parser.add_argument("--admin-target-p95-ms", type=float, default=500.0)
    parser.add_argument("--sample-interval-seconds", type=float, default=1.0)
    parser.add_argument("--sample-url", action="append", type=parse_sample_target, help="Additional sample as label=url,target_p95_ms=300,expected_status_min=200,expected_status_max=499")
    parser.add_argument("--no-default-samples", action="store_true", default=False)
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> dict[str, Any]:
    return run_pressure(_parse_args(argv))


def main(argv: list[str] | None = None) -> int:
    payload = run(argv)
    print_json(payload, indent=2)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
