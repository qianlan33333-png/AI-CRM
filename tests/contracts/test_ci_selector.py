from __future__ import annotations

import pytest

from scripts.ci.select_test_scope import PUBLIC_OUTPUT_FIELDS, classify


pytestmark = pytest.mark.contract


@pytest.mark.parametrize(
    ("path", "expected_reason"),
    [
        ("migrations/versions/0167_new.py", "migration_or_schema"),
        ("aicrm_next/platform/admin_auth/guards.py", "authentication_or_identity"),
        ("aicrm_next/extensions/commerce/public_product/h5_wechat_pay.py", "payment_refund_or_entitlement"),
        ("aicrm_next/channels/channel_entry/api.py", "callback_or_external_effect"),
        ("deploy/runtime_role_catalog.json", "production_or_deploy"),
    ],
)
def test_high_risk_paths_upgrade_to_one_full_runner(path: str, expected_reason: str) -> None:
    selection = classify([path])
    assert selection.tier == "high_risk"
    assert selection.requires_postgres is True
    assert expected_reason in selection.reason
    assert selection.python_targets == (
        "tests/unit",
        "tests/contracts",
        "tests/postgres",
        "tests/high_risk",
        "tests/release",
    )


def test_normal_current_code_selects_mapped_tests() -> None:
    selection = classify(["aicrm_next/crm/customer_read_model/dto.py"])
    assert selection.tier == "fast"
    assert "tests/unit/test_crm_rules.py" in selection.python_targets
    assert "tests/contracts/test_routes_and_auth.py" in selection.python_targets
    assert selection.requires_postgres is True


def test_frontend_change_selects_current_node_tests() -> None:
    selection = classify(["aicrm_next/app/admin_console/static/admin_console/admin_api_client.js"])
    assert selection.tier == "fast"
    assert "tests/frontend/shared_api_client.test.mjs" in selection.frontend_targets


def test_unknown_runtime_and_deletion_fail_closed() -> None:
    unknown = classify(["aicrm_next/new_context/runtime.py"])
    deleted = classify(["docs/readme.md"], deleted_files=["docs/readme.md"])
    assert unknown.tier == "high_risk"
    assert "unknown_runtime_path" in unknown.reason
    assert deleted.tier == "high_risk"
    assert "deleted_file" in deleted.reason


def test_local_high_risk_selection_never_requests_postgres_or_release() -> None:
    selection = classify(["migrations/versions/0167_new.py"], local=True)
    assert selection.tier == "high_risk"
    assert selection.requires_postgres is False
    assert all(path.startswith(("tests/unit/", "tests/contracts/")) for path in selection.python_targets)


def test_postgres_conftest_is_cloud_only_even_when_it_selects_the_layer_directory() -> None:
    cloud = classify(["tests/postgres/conftest.py"])
    local = classify(["tests/postgres/conftest.py"], local=True)
    assert cloud.python_targets[0] == "tests/postgres"
    assert cloud.requires_postgres is True
    assert "tests/postgres" not in local.python_targets
    assert local.requires_postgres is False


def test_main_and_manual_events_have_explicit_tiers() -> None:
    release = classify(["aicrm_next/main.py"], event_name="push", main_push=True)
    full = classify([], event_name="workflow_dispatch")
    assert release.tier == "release"
    assert release.python_targets == ("tests/release",)
    assert full.tier == "full"


def test_selector_exposes_only_the_fixed_public_fields() -> None:
    payload = classify(["README.md"]).to_dict()
    assert tuple(payload) == PUBLIC_OUTPUT_FIELDS
