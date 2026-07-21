from __future__ import annotations

from aicrm_next.platform_foundation.external_effects import WECOM_MEDIA_UPLOAD
from aicrm_next.platform_foundation.external_effects.continuations import ExternalEffectContinuation

from .durable_effects_repository import (
    GROUP_OPS_EFFECT_BUSINESS_TYPE,
    build_group_ops_effect_graph_repository,
)


def _matches_group_ops_media_upload(job, _dispatch_result) -> bool:
    return job.effect_type == WECOM_MEDIA_UPLOAD and job.business_type == GROUP_OPS_EFFECT_BUSINESS_TYPE and bool(str(job.business_id or "").strip())


def _release_group_ops_final_effect(job, _dispatch_result):
    return build_group_ops_effect_graph_repository().release_after_upload(
        int(job.id),
        attempt_id=str(job.last_attempt_id or "").strip(),
    )


def _matches_group_ops_terminal_effect(job, _dispatch_result) -> bool:
    return (
        job.business_type == GROUP_OPS_EFFECT_BUSINESS_TYPE
        and bool(str(job.business_id or "").strip())
        and job.status != "succeeded"
    )


def _settle_group_ops_effect_graph(job, _dispatch_result):
    return build_group_ops_effect_graph_repository().settle_effect(
        int(job.id),
        status=str(job.status or "").strip(),
        attempt_id=str(job.last_attempt_id or "").strip(),
    )


GROUP_OPS_MEDIA_DEPENDENCY_CONTINUATION = ExternalEffectContinuation(
    name="group_ops_media_dependency_release",
    matches=_matches_group_ops_media_upload,
    run=_release_group_ops_final_effect,
)

GROUP_OPS_EFFECT_SETTLEMENT_CONTINUATION = ExternalEffectContinuation(
    name="group_ops_effect_graph_settlement",
    matches=_matches_group_ops_terminal_effect,
    run=_settle_group_ops_effect_graph,
)


__all__ = [
    "GROUP_OPS_EFFECT_SETTLEMENT_CONTINUATION",
    "GROUP_OPS_MEDIA_DEPENDENCY_CONTINUATION",
]
