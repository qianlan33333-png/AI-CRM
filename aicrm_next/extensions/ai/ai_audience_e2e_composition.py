from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .ai_audience_ops.e2e_runner import AudienceRealE2ERunner
from .ai_audience_ops.repository import AudienceRepository
from aicrm_next.external_effect_composition import build_external_effect_adapter_registry
from aicrm_next.ops_enrollment.ai_audience_e2e_gateway import OpsEnrollmentAudienceE2EGateway
from aicrm_next.platform_foundation.external_effects.adapters import ExternalEffectAdapterRegistry
from aicrm_next.platform_foundation.external_effects.worker import ExternalEffectWorker


@dataclass(frozen=True)
class AiAudienceE2ERunnerFactory:
    user_ops_gateway_factory: Callable[[], OpsEnrollmentAudienceE2EGateway]
    external_effect_adapter_registry: ExternalEffectAdapterRegistry

    def __call__(self, *, repository: AudienceRepository | None = None) -> AudienceRealE2ERunner:
        return AudienceRealE2ERunner(
            repository=repository,
            user_ops_gateway=self.user_ops_gateway_factory(),
            worker=ExternalEffectWorker(
                adapter_registry=self.external_effect_adapter_registry,
                locked_by="ai-audience-real-e2e",
            ),
        )


def build_ai_audience_e2e_runner_factory(
    *,
    external_effect_adapter_registry: ExternalEffectAdapterRegistry | None = None,
) -> AiAudienceE2ERunnerFactory:
    return AiAudienceE2ERunnerFactory(
        user_ops_gateway_factory=OpsEnrollmentAudienceE2EGateway,
        external_effect_adapter_registry=(
            external_effect_adapter_registry or build_external_effect_adapter_registry()
        ),
    )
