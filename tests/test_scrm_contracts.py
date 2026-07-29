from __future__ import annotations

import pytest

from aicrm_next.platform.platform_foundation.external_effects.models import ExternalEffectAttempt, ExternalEffectJob
from aicrm_next.platform.platform_foundation.external_effects.provider_receipts import provider_receipt_from_external_effect
from aicrm_next.scrm_contracts import (
    AudienceFilter,
    AudienceSpec,
    CampaignExecution,
    ContentBundle,
    ContentItem,
    ContractValidationError,
    CustomerRelationship,
)


def test_five_canonical_scrm_contracts_are_versioned_and_serializable() -> None:
    relationship = CustomerRelationship(
        relationship_id="rel:1",
        customer_id="customer:1",
        corp_id="ww-corp",
        employee_userid="zhangsan",
        external_userid="wm-external",
        tag_ids=("tag:high-intent",),
    )
    audience = AudienceSpec(
        audience_id="audience:active",
        name="活跃客户",
        owner_capability_id="core.automation",
        filters=(AudienceFilter("lifecycle.stage", "eq", "active"),),
        data_scope="team",
    )
    content = ContentBundle(
        bundle_id="content:welcome",
        name="欢迎语",
        items=(ContentItem("text", text="欢迎加入"), ContentItem("image", asset_ref="media:18")),
    )
    execution = CampaignExecution(
        execution_id="execution:1",
        campaign_id="campaign:1",
        audience_id=audience.audience_id,
        audience_version=audience.version,
        content_bundle_id=content.bundle_id,
        content_bundle_version=content.version,
        idempotency_key="campaign:1:execution:1",
        target_count=10,
    )

    assert relationship.to_dict()["customer_id"] == "customer:1"
    assert audience.to_dict()["filters"][0]["operator"] == "eq"
    assert content.to_dict()["items"][1]["asset_ref"] == "media:18"
    assert execution.to_dict()["target_count"] == 10


def test_audience_contract_forbids_online_code_and_untyped_operators() -> None:
    with pytest.raises(ContractValidationError, match="field is invalid"):
        AudienceFilter("python(script)", "eq", "unsafe")
    with pytest.raises(ContractValidationError, match="unsupported audience operator"):
        AudienceFilter("customer.stage", "sql", "SELECT 1")


def test_campaign_outcome_counts_cannot_exceed_targets() -> None:
    with pytest.raises(ContractValidationError, match="exceed target_count"):
        CampaignExecution(
            execution_id="execution:bad",
            campaign_id="campaign:bad",
            audience_id="audience:bad",
            audience_version=1,
            content_bundle_id="content:bad",
            content_bundle_version=1,
            idempotency_key="campaign:bad:execution",
            target_count=1,
            succeeded_count=2,
        )


def test_external_effect_attempt_maps_to_redacted_provider_receipt() -> None:
    job = ExternalEffectJob(
        id=41,
        effect_type="wecom.message.private.send",
        idempotency_key="effect:private:41",
        completed_at="2026-07-25T01:00:00Z",
    )
    attempt = ExternalEffectAttempt(
        attempt_id="attempt:41",
        status="succeeded",
        response_summary_json={"errcode": 0, "errmsg": "ok", "msgid": "provider:99"},
        completed_at="2026-07-25T01:00:00Z",
    )

    receipt = provider_receipt_from_external_effect(job, attempt, provider="wecom")

    assert receipt.status == "delivered"
    assert receipt.provider_code == "0"
    assert receipt.provider_request_id == "provider:99"
    assert receipt.raw_payload_ref == ""
    assert "body_json" not in receipt.to_dict()
