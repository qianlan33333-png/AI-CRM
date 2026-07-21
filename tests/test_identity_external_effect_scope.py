from __future__ import annotations

from aicrm_next.channel_entry.identity_external_effect import (
    IDENTITY_EXTERNAL_CONTACT_DETAIL_CONTINUATION,
    IDENTITY_RESOLUTION_BUSINESS_TYPE,
)
from aicrm_next.platform_foundation.external_effects import WECOM_EXTERNAL_CONTACT_DETAIL_FETCH
from aicrm_next.platform_foundation.external_effects.continuations import run_external_effect_continuation
from aicrm_next.platform_foundation.external_effects.models import (
    ExternalEffectDispatchResult,
    ExternalEffectJob,
)


def _result() -> ExternalEffectDispatchResult:
    return ExternalEffectDispatchResult(status="succeeded", adapter_mode="execute")


def _job(**overrides) -> ExternalEffectJob:
    values = {
        "id": 101,
        "effect_type": WECOM_EXTERNAL_CONTACT_DETAIL_FETCH,
        "business_type": IDENTITY_RESOLUTION_BUSINESS_TYPE,
        "business_id": "42",
        "payload_json": {"queue_id": 42},
    }
    values.update(overrides)
    return ExternalEffectJob(**values)


def test_identity_continuation_matches_only_identity_resolution_jobs() -> None:
    assert IDENTITY_EXTERNAL_CONTACT_DETAIL_CONTINUATION.matches(_job(), _result()) is True

    direct_canary = _job(
        business_type="id_validation_canary",
        business_id="sha2dba-direct-20260721-1",
        payload_json={"external_userid": "redacted"},
    )
    assert IDENTITY_EXTERNAL_CONTACT_DETAIL_CONTINUATION.matches(direct_canary, _result()) is False

    outcome = run_external_effect_continuation(
        IDENTITY_EXTERNAL_CONTACT_DETAIL_CONTINUATION,
        direct_canary,
        _result(),
        provider_result_loader=lambda: (_ for _ in ()).throw(AssertionError("must not load provider result")),
    )
    assert outcome == {
        "applicable": False,
        "ok": True,
        "continuation": "identity_external_contact_detail_continuation",
        "reason": "continuation_not_applicable",
    }


def test_identity_continuation_predicate_is_fail_closed_for_malformed_queue_links() -> None:
    assert (
        IDENTITY_EXTERNAL_CONTACT_DETAIL_CONTINUATION.matches(
            _job(business_id="not-an-integer", payload_json={}),
            _result(),
        )
        is False
    )
    assert (
        IDENTITY_EXTERNAL_CONTACT_DETAIL_CONTINUATION.matches(
            _job(business_id="42", payload_json={"queue_id": 43}),
            _result(),
        )
        is False
    )


def test_identity_continuation_accepts_one_canonical_positive_queue_link() -> None:
    assert (
        IDENTITY_EXTERNAL_CONTACT_DETAIL_CONTINUATION.matches(
            _job(business_id="42", payload_json={}),
            _result(),
        )
        is True
    )
    assert (
        IDENTITY_EXTERNAL_CONTACT_DETAIL_CONTINUATION.matches(
            _job(business_id="", payload_json={"queue_id": 42}),
            _result(),
        )
        is True
    )
