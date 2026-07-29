from __future__ import annotations

from aicrm_next import fixture_reset_registry, main


EXPECTED_RESET_ORDER = [
    "user_ops",
    "questionnaire",
    "questionnaire_h5_write",
    "automation",
    "group_ops",
    "commerce",
    "commerce_coupons",
    "service_period",
    "media_library",
    "admin_jobs",
    "hxc_dashboard",
    "hxc_safe_mode",
    "radar_links",
    "cloud_plan",
    "campaign_read",
    "campaign_write",
    "sidebar_write",
    "wecom_customer_acquisition_link",
    "admin_auth",
    "questionnaire_admin_write",
    "wecom_tag_write",
    "wecom_tag_live_mutation",
    "sidebar_jssdk_attempts",
    "external_effect",
    "internal_event",
]


def test_fixture_reset_registry_preserves_main_order() -> None:
    assert [step.name for step in fixture_reset_registry.FIXTURE_RESET_STEPS] == EXPECTED_RESET_ORDER


def test_fixture_reset_registry_runs_steps_in_order(monkeypatch) -> None:
    calls: list[str] = []
    steps = tuple(
        fixture_reset_registry.FixtureResetStep(name, lambda name=name: calls.append(name))
        for name in ("first", "second", "third")
    )
    monkeypatch.setattr(fixture_reset_registry, "FIXTURE_RESET_STEPS", steps)

    fixture_reset_registry.reset_fixture_state()

    assert calls == ["first", "second", "third"]


def test_create_app_uses_fixture_reset_registry_in_fixture_mode(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(main, "fixture_mode", lambda: True)
    monkeypatch.setattr(main.fixture_reset_registry, "reset_fixture_state", lambda: calls.append("reset"))

    app = main.create_app()

    assert app.title == "AI-CRM Next"
    assert calls == ["reset"]


def test_create_app_skips_fixture_reset_registry_in_production_mode(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(main, "fixture_mode", lambda: False)
    monkeypatch.setattr(main.fixture_reset_registry, "reset_fixture_state", lambda: calls.append("reset"))

    app = main.create_app()

    assert app.title == "AI-CRM Next"
    assert calls == []
