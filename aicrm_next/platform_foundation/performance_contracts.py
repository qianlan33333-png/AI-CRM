from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE_PATH = ROOT / "docs" / "performance" / "critical_read_path_baselines.json"
REQUIRED_PROFILES = {
    "customer_list",
    "sidebar_workbench",
    "questionnaire_admin",
    "admin_jobs",
    "push_center",
}


@dataclass(frozen=True)
class ReadPathBaseline:
    name: str
    route: str
    owner: str
    dataset_rows: int
    sample_count: int
    max_query_count: int
    page_limit: int
    baseline_p95_ms: float
    max_seq_scan_rows: int
    allowed_large_seq_scan_relations: frozenset[str]
    regression_factor: float


def percentile(values: Iterable[float], percentile_value: float) -> float:
    samples = sorted(float(value) for value in values)
    if not samples:
        raise ValueError("latency samples are required")
    if not 0 < percentile_value <= 100:
        raise ValueError("percentile must be in (0, 100]")
    rank = max(0, math.ceil(len(samples) * percentile_value / 100.0) - 1)
    return samples[rank]


def load_read_path_baselines(path: Path = DEFAULT_BASELINE_PATH) -> dict[str, ReadPathBaseline]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("version") or 0) != 1:
        raise ValueError("critical read baseline version must be 1")
    regression_factor = float(payload.get("regression_factor") or 0)
    if regression_factor != 1.1:
        raise ValueError("critical read p95 regression factor must remain exactly 1.1")
    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, Mapping):
        raise ValueError("critical read baseline profiles must be an object")
    names = set(raw_profiles)
    if names != REQUIRED_PROFILES:
        missing = sorted(REQUIRED_PROFILES - names)
        extra = sorted(names - REQUIRED_PROFILES)
        raise ValueError(f"critical read baseline profiles mismatch: missing={missing}, extra={extra}")

    profiles: dict[str, ReadPathBaseline] = {}
    for name, raw in raw_profiles.items():
        if not isinstance(raw, Mapping):
            raise ValueError(f"{name}: profile must be an object")
        profile = ReadPathBaseline(
            name=name,
            route=str(raw.get("route") or "").strip(),
            owner=str(raw.get("owner") or "").strip(),
            dataset_rows=int(raw.get("dataset_rows") or 0),
            sample_count=int(raw.get("sample_count") or 0),
            max_query_count=int(raw.get("max_query_count") or 0),
            page_limit=int(raw.get("page_limit") or 0),
            baseline_p95_ms=float(raw.get("baseline_p95_ms") or 0),
            max_seq_scan_rows=int(raw.get("max_seq_scan_rows") or 0),
            allowed_large_seq_scan_relations=frozenset(
                str(value) for value in raw.get("allowed_large_seq_scan_relations") or []
            ),
            regression_factor=regression_factor,
        )
        _validate_profile(profile)
        profiles[name] = profile
    return profiles


def _validate_profile(profile: ReadPathBaseline) -> None:
    if not profile.route.startswith("/"):
        raise ValueError(f"{profile.name}: route must be absolute")
    if not profile.owner:
        raise ValueError(f"{profile.name}: owner is required")
    for field_name in (
        "dataset_rows",
        "sample_count",
        "max_query_count",
        "page_limit",
        "max_seq_scan_rows",
    ):
        if int(getattr(profile, field_name)) <= 0:
            raise ValueError(f"{profile.name}: {field_name} must be positive")
    if profile.sample_count < 10:
        raise ValueError(f"{profile.name}: sample_count must be at least 10")
    if profile.baseline_p95_ms <= 0:
        raise ValueError(f"{profile.name}: baseline_p95_ms must be positive")


def collect_plan_evidence(plan: Mapping[str, Any]) -> dict[str, Any]:
    node_types: list[str] = []
    indexes: list[str] = []
    seq_scans: list[dict[str, Any]] = []

    def visit(node: Mapping[str, Any]) -> None:
        node_type = str(node.get("Node Type") or "")
        if node_type:
            node_types.append(node_type)
        index_name = str(node.get("Index Name") or "")
        if index_name:
            indexes.append(index_name)
        if node_type == "Seq Scan":
            seq_scans.append(
                {
                    "relation": str(node.get("Relation Name") or ""),
                    "plan_rows": int(node.get("Plan Rows") or 0),
                    "actual_rows": int(node.get("Actual Rows") or 0),
                }
            )
        for child in node.get("Plans") or []:
            if isinstance(child, Mapping):
                visit(child)

    visit(plan)
    return {
        "node_types": sorted(set(node_types)),
        "indexes": sorted(set(indexes)),
        "seq_scans": seq_scans,
    }


def evaluate_read_path_report(
    profile: ReadPathBaseline,
    report: Mapping[str, Any],
) -> list[str]:
    failures: list[str] = []
    if int(report.get("dataset_rows") or 0) != profile.dataset_rows:
        failures.append(
            f"dataset rows changed: expected {profile.dataset_rows}, got {report.get('dataset_rows')}"
        )
    if int(report.get("sample_count") or 0) < profile.sample_count:
        failures.append(
            f"sample count too small: expected at least {profile.sample_count}, got {report.get('sample_count')}"
        )
    query_count = int(report.get("query_count") or 0)
    if query_count > profile.max_query_count:
        failures.append(
            f"query count regression: maximum {profile.max_query_count}, got {query_count}"
        )
    max_page_rows = int(report.get("max_page_rows") or 0)
    if max_page_rows > profile.page_limit:
        failures.append(
            f"pagination regression: maximum {profile.page_limit}, got {max_page_rows}"
        )
    p95_ms = float(report.get("p95_ms") or 0)
    p95_limit = profile.baseline_p95_ms * profile.regression_factor
    if p95_ms > p95_limit:
        failures.append(
            f"p95 regression: maximum {p95_limit:.3f}ms, got {p95_ms:.3f}ms"
        )
    plans = report.get("plans") or []
    if not plans:
        failures.append("query plan evidence is required")
    for plan in plans:
        for scan in plan.get("seq_scans") or []:
            relation = str(scan.get("relation") or "")
            plan_rows = int(scan.get("plan_rows") or 0)
            if (
                plan_rows > profile.max_seq_scan_rows
                and relation not in profile.allowed_large_seq_scan_relations
            ):
                failures.append(
                    f"large sequential scan: relation={relation}, plan_rows={plan_rows}, "
                    f"maximum={profile.max_seq_scan_rows}"
                )
    return failures
