from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from aicrm_next.platform.admin_config import application as admin_application
from aicrm_next.platform.admin_config import application_support as admin_support
from aicrm_next.extensions.ai.ai_audience_ops import repository as audience_repository
from aicrm_next.extensions.ai.ai_audience_ops import repository_packages as audience_package_repository
from aicrm_next.automation.automation_engine.group_ops import postgres_repo as group_ops_repository
from aicrm_next.automation.automation_engine.group_ops import postgres_repo_mapping as group_ops_mapping_repository
from aicrm_next.extensions.growth.cloud_orchestrator import repository as cloud_repository
from aicrm_next.extensions.growth.cloud_orchestrator import repository_legacy as cloud_legacy_repository
from aicrm_next.extensions.growth.cloud_orchestrator import repository_memory as cloud_memory_repository
from aicrm_next.extensions.commerce.commerce import api as commerce_api
from aicrm_next.extensions.commerce.commerce import api_support as commerce_api_support
from aicrm_next.crm.customer_read_model import application as customer_application
from aicrm_next.crm.customer_read_model import application_customer360_support as customer_application_support
from aicrm_next.crm.customer_read_model import repo as customer_repository
from aicrm_next.crm.customer_read_model import repo_fixture as customer_fixture_repository
from aicrm_next.crm.customer_read_model import repo_live_source as customer_live_repository
from aicrm_next.platform.platform_foundation.external_effects import repo as effect_repository
from aicrm_next.platform.platform_foundation.external_effects import repo_memory as effect_memory_repository
from aicrm_next.platform.platform_foundation.internal_events import repository as event_repository
from aicrm_next.platform.platform_foundation.internal_events import repository_memory as event_memory
from aicrm_next.extensions.forms.questionnaire import repo as questionnaire_repository
from aicrm_next.extensions.forms.questionnaire import repo_memory as questionnaire_memory


def test_internal_event_repository_facade_preserves_public_imports() -> None:
    assert event_repository.InMemoryInternalEventRepository is event_memory.InMemoryInternalEventRepository
    assert event_repository.SQLAlchemyInternalEventRepository.__module__ == event_repository.__name__
    assert callable(event_repository.build_internal_event_repository)
    assert callable(event_repository.reset_internal_event_fixture_state)


def test_internal_event_repository_facade_initializes_fixture_before_any_reset() -> None:
    script = """
from aicrm_next.platform.platform_foundation.internal_events.repository import (
    InMemoryInternalEventRepository,
    build_internal_event_repository,
)

assert isinstance(build_internal_event_repository(), InMemoryInternalEventRepository)
"""
    env = dict(os.environ)
    env["DATABASE_URL"] = ""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_questionnaire_repository_facade_preserves_public_imports_and_patch_seams() -> None:
    assert questionnaire_repository.InMemoryQuestionnaireRepository is questionnaire_memory.InMemoryQuestionnaireRepository
    assert questionnaire_repository.PostgresQuestionnaireReadRepository.__module__ == questionnaire_repository.__name__
    assert callable(questionnaire_repository._jsonb)
    assert callable(questionnaire_repository.build_questionnaire_repository)
    assert callable(questionnaire_repository.reset_questionnaire_fixture_state)


def test_admin_config_application_keeps_services_and_support_seams_on_facade() -> None:
    assert admin_application.AdminConfigReadService.__module__ == admin_application.__name__
    assert admin_application.AdminConfigWriteCommand.__module__ == admin_application.__name__
    assert admin_application._text is admin_support._text
    assert admin_application.AdminConfigRepository is admin_support.AdminConfigRepository


def test_external_effect_repository_facade_preserves_repository_classes() -> None:
    assert effect_repository.InMemoryExternalEffectRepository is effect_memory_repository.InMemoryExternalEffectRepository
    assert effect_repository.SQLAlchemyExternalEffectRepository.__module__ == effect_repository.__name__
    assert callable(effect_repository.build_external_effect_repository)
    assert callable(effect_repository.reset_external_effect_fixture_state)


def test_customer_read_repository_facade_preserves_all_repository_variants() -> None:
    assert customer_repository.FixtureCustomerReadRepository is customer_fixture_repository.FixtureCustomerReadRepository
    assert customer_repository.LiveSourceCustomerReadRepository is customer_live_repository.LiveSourceCustomerReadRepository
    assert customer_repository.SqlAlchemyCustomerReadModelRepository.__module__ == customer_repository.__name__
    assert callable(customer_repository.build_customer_read_model_repository)
    assert callable(customer_repository.build_customer_live_source_repository)


def test_customer_application_facade_preserves_customer_360_helpers() -> None:
    assert customer_application._customer_360_identity is customer_application_support._customer_360_identity
    assert customer_application.GetCustomer360ProfileQuery.__module__ == customer_application.__name__


def test_cloud_repository_facade_preserves_classes_and_legacy_mixin() -> None:
    assert cloud_repository.InMemoryCloudPlanRepository is cloud_memory_repository.InMemoryCloudPlanRepository
    assert cloud_legacy_repository.CloudLegacyPostgresRepositoryMixin in cloud_repository.PostgresCloudPlanRepository.__mro__
    assert cloud_repository.PostgresCloudPlanRepository.__module__ == cloud_repository.__name__
    assert callable(cloud_repository.build_cloud_plan_repository)


def test_audience_repository_facade_preserves_sqlalchemy_class_and_package_mixin() -> None:
    assert audience_package_repository.AudiencePackageRepositoryMixin in audience_repository.SQLAlchemyAudienceRepository.__mro__
    assert audience_repository.SQLAlchemyAudienceRepository.__module__ == audience_repository.__name__
    assert callable(audience_repository.build_audience_repository)


def test_group_ops_repository_facade_preserves_class_and_mapping_mixin() -> None:
    assert group_ops_mapping_repository.GroupOpsPostgresMappingMixin in group_ops_repository.PostgresGroupOpsRepository.__mro__
    assert group_ops_repository.PostgresGroupOpsRepository.__module__ == group_ops_repository.__name__


def test_commerce_api_facade_preserves_support_helpers_and_route_module() -> None:
    assert commerce_api._payment_final_headers is commerce_api_support._payment_final_headers
    assert commerce_api.list_products.__module__ == commerce_api.__name__
    assert commerce_api.router.routes
