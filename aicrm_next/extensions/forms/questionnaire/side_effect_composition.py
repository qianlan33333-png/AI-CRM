from __future__ import annotations

from typing import Any

from aicrm_next.crm.customer_tags.live_mutation import execute_wecom_tag_mutation
from aicrm_next.crm.customer_tags.local_projection import project_questionnaire_tags
from aicrm_next.crm.customer_tags.mutation_commands import PlanQuestionnaireTagSideEffectCommand
from aicrm_next.crm.identity_contact.application import BindMobileToExternalContactCommand
from aicrm_next.crm.identity_contact.dto import BindMobileToExternalContactRequest
from aicrm_next.integration_ports import make_idempotency_key
from aicrm_next.integration_ports import QuestionnaireSubmitSideEffectGateway


def build_questionnaire_submit_side_effect_gateway() -> QuestionnaireSubmitSideEffectGateway:
    return QuestionnaireSubmitSideEffectGateway(
        local_tag_projector=project_questionnaire_tags,
        tag_mutation_executor=_execute_tag_mutation,
        mobile_binder=_bind_mobile,
    )


def _execute_tag_mutation(
    *,
    questionnaire_id: int | str,
    submission_id: str,
    external_userid: str,
    tag_ids: list[str],
    unionid: str,
    follow_user_userid: str,
    local_projection: dict[str, Any],
) -> dict[str, Any]:
    command = PlanQuestionnaireTagSideEffectCommand(
        idempotency_key=make_idempotency_key(
            operation="questionnaire.tag.apply",
            payload={
                "questionnaire_id": questionnaire_id,
                "submission_id": submission_id,
                "external_userid": external_userid,
                "tag_ids": sorted(tag_ids),
            },
        ),
        actor_id="questionnaire_submit_pipeline",
        actor_type="system",
        external_userid=external_userid,
        tag_ids=tag_ids,
        source_route="/api/h5/questionnaires/{slug}/submit",
        source_context={
            "source": "questionnaire_submit_pipeline",
            "questionnaire_id": questionnaire_id,
            "submission_id": submission_id,
            "unionid": unionid,
            "follow_user_userid": follow_user_userid,
            "local_projection": local_projection,
            "local_projection_updated": bool(local_projection.get("local_projection_updated")),
            "bypass_push_capability": True,
        },
    )
    return execute_wecom_tag_mutation(command)


def _bind_mobile(
    *,
    external_userid: str,
    mobile: str,
    owner_userid: str,
    bind_by_userid: str,
    customer_name: str,
    force_rebind: bool,
) -> dict[str, Any]:
    return BindMobileToExternalContactCommand()(
        BindMobileToExternalContactRequest(
            external_userid=external_userid,
            mobile=mobile,
            owner_userid=owner_userid,
            bind_by_userid=bind_by_userid,
            customer_name=customer_name,
            force_rebind=force_rebind,
        )
    )


__all__ = ["build_questionnaire_submit_side_effect_gateway"]
