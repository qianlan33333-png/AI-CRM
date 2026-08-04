from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from time import sleep

import pytest
import psycopg

from aicrm_next.capability_registry import (
    CAPABILITY_SPECS,
    capability_for_route_group,
    default_capability_ids,
    validate_capability_registry,
)
from aicrm_next.deployment_profile import (
    DeploymentProfile,
    RUNTIME_ROLES,
    default_deployment_profile,
    validate_deployment_profile,
)
from aicrm_next.platform.admin_auth.capabilities import capabilities_for_roles, normalize_roles
from aicrm_next.platform.shared.mobile import normalize_mainland_mobile
from aicrm_next.platform.shared.product_code_aliases import (
    canonical_product_code,
    canonical_product_name,
    product_code_filter_values,
)
from aicrm_next.platform.shared.runtime import runtime_health_state, runtime_route_map_state
from aicrm_next.platform.shared.wechat_identity_page import wechat_full_service_required_response
from aicrm_next.platform.shared.resource_admission import (
    RequestPriorityMetrics,
    ResourceAdmissionController,
    ResourceCapacityExhausted,
    ResourcePolicy,
    media_binary_admission,
    request_priority_for_path,
    reset_resource_admission_controllers,
    resource_admission_snapshot,
)
from aicrm_next.platform.platform_foundation.repository import (
    READINESS_LOCK_TIMEOUT_MS,
    READINESS_STATEMENT_TIMEOUT_MS,
    _connect_readiness_db,
)


pytestmark = pytest.mark.unit


def test_capability_registry_owns_every_declared_route_group() -> None:
    assert validate_capability_registry() == []
    route_groups = {group for spec in CAPABILITY_SPECS for group in spec.route_groups}
    assert route_groups
    assert all(capability_for_route_group(group) is not None for group in route_groups)


def test_default_profile_enables_the_current_core_and_runtime_roles() -> None:
    profile = default_deployment_profile()
    assert validate_deployment_profile(profile) == []
    assert set(default_capability_ids()) <= set(profile.enabled_capabilities)
    assert set(profile.runtime_roles) == set(RUNTIME_ROLES)


def test_profile_validation_fails_closed_for_unknown_capability() -> None:
    current = default_deployment_profile()
    invalid = DeploymentProfile(
        profile_id=current.profile_id,
        core_version=current.core_version,
        enabled_capabilities=(*current.enabled_capabilities, "extension.unknown"),
        runtime_roles=current.runtime_roles,
        config_schema_version=current.config_schema_version,
    )
    assert "unknown capability: extension.unknown" in validate_deployment_profile(invalid)


@pytest.mark.parametrize(
    ("raw", "allow_country_code", "expected"),
    [
        ("13800138000", False, "13800138000"),
        ("+86 138-0013-8000", True, "13800138000"),
        ("23800138000", False, ""),
        ([], False, ""),
    ],
)
def test_mobile_normalization_is_explicit(raw: object, allow_country_code: bool, expected: str) -> None:
    assert normalize_mainland_mobile(raw, allow_country_code=allow_country_code) == expected


def test_product_aliases_share_one_canonical_vocabulary() -> None:
    legacy = "prd_20260518095708_9f77db"
    canonical = canonical_product_code(legacy)
    assert canonical == "subscription_trial_month"
    assert canonical_product_name(canonical) == "订阅会员"
    assert {legacy, canonical} <= set(product_code_filter_values(canonical))


def test_role_capabilities_are_deduplicated_and_unknown_roles_add_nothing() -> None:
    assert normalize_roles(["viewer", "viewer", "", None]) == ("viewer",)
    assert "admin_read" in capabilities_for_roles(["viewer", "unknown"])


def test_runtime_state_declares_next_as_the_only_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    health = runtime_health_state()
    route_map = runtime_route_map_state()
    assert health["runtime_owner"] == "ai_crm_next"
    assert health["legacy_runtime_enabled"] is False
    assert route_map["route_owner"] == "ai_crm_next"
    assert route_map["legacy_callback_fallback_enabled"] is False


def test_runtime_readiness_database_probe_has_bounded_query_and_lock_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_connect(database_url: str, **kwargs: object) -> object:
        captured["database_url"] = database_url
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    assert _connect_readiness_db("postgresql+psycopg://test@db/current") is sentinel
    assert captured["database_url"] == "postgresql://test@db/current"
    assert captured["connect_timeout"] == 3
    assert f"statement_timeout={READINESS_STATEMENT_TIMEOUT_MS}" in str(captured["options"])
    assert f"lock_timeout={READINESS_LOCK_TIMEOUT_MS}" in str(captured["options"])


def test_wechat_full_service_page_uses_calm_script_free_guidance() -> None:
    response = wechat_full_service_required_response()
    html = response.body.decode("utf-8")

    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    assert '<title>还差一步</title>' in html
    assert '<div class="brand">新流商业</div>' in html
    assert '<h1 id="full-service-title">还差一步</h1>' in html
    assert '点击屏幕底部的 <b>使用完整服务</b>' in html
    assert "animation: reveal-fallback 0s linear 8s forwards" in html
    assert 'data-identity-status="full_service_required"' in html
    assert "oauth_state_cookie_missing" not in html
    assert "cookieReady" not in html
    assert "<script" not in html
    assert "<button" not in html


def test_media_resource_admission_caps_concurrency_and_queue() -> None:
    controller = ResourceAdmissionController(
        ResourcePolicy("media_binary", "P2", max_in_flight=2, max_queued=10, wait_timeout_ms=500)
    )
    current = 0
    observed = 0
    lock = Lock()

    def run(_: int) -> None:
        nonlocal current, observed
        with controller.admit():
            with lock:
                current += 1
                observed = max(observed, current)
            sleep(0.01)
            with lock:
                current -= 1

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(run, range(50)))
    snapshot = controller.snapshot()
    assert observed == 2
    assert snapshot["observed"]["max_queued"] <= 10
    assert snapshot["totals"]["completed"] == 50


def test_media_resource_admission_rejects_a_full_queue() -> None:
    controller = ResourceAdmissionController(
        ResourcePolicy("media_binary", "P2", max_in_flight=1, max_queued=1, wait_timeout_ms=200)
    )
    entered = Event()
    release = Event()

    def hold() -> None:
        with controller.admit():
            entered.set()
            release.wait(timeout=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(hold)
        assert entered.wait(timeout=1)
        waiting = pool.submit(lambda: _admit_once(controller))
        sleep(0.02)
        with pytest.raises(ResourceCapacityExhausted, match="capacity exhausted"):
            with controller.admit():
                pass
        release.set()
        first.result(timeout=1)
        waiting.result(timeout=1)


def _admit_once(controller: ResourceAdmissionController) -> None:
    with controller.admit():
        pass


def test_resource_priority_metrics_and_rollout_are_low_cardinality(monkeypatch: pytest.MonkeyPatch) -> None:
    metrics = RequestPriorityMetrics()
    for priority, status in (("P0", 504), ("P1", 500), ("P2", 429)):
        metrics.begin(priority)
        metrics.complete(priority, duration_ms=20, status_code=status)
    assert metrics.snapshot()["P0"]["totals"]["timeouts"] == 1
    assert request_priority_for_path("/api/sidebar/v2/workbench") == "P0"
    assert request_priority_for_path("/api/sidebar/v2/materials/image/1/thumbnail") == "P2"

    reset_resource_admission_controllers()
    monkeypatch.setenv("AICRM_MEDIA_ADMISSION_ENABLED", "false")
    monkeypatch.setenv("AICRM_MEDIA_ADMISSION_ROLLOUT_PERCENT", "100")
    with media_binary_admission(rollout_key="image:1"):
        pass
    assert resource_admission_snapshot()["media_rollout"]["bypassed"] == 1
    monkeypatch.setenv("AICRM_MEDIA_ADMISSION_ENABLED", "true")
    with media_binary_admission(rollout_key="image:1"):
        pass
    assert resource_admission_snapshot()["media_binary"]["totals"]["completed"] == 1
