from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
import requests

from aicrm_next.admin_config.repository import AdminConfigRepository
from aicrm_next.admin_config.settings import mask_value
from aicrm_next.extensions.ai.ai_audience_ops.package_spec import redact_report
from aicrm_next.platform_foundation.external_calls import scrub_summary
from aicrm_next.platform_foundation.external_effects.adapters import WebhookAdapter
from aicrm_next.platform_foundation.external_effects.models import WEBHOOK_GENERIC_PUSH
from aicrm_next.shared.sensitive_data import (
    PII_MASK,
    SECRET_MASK,
    redact_sensitive_data,
    redact_sensitive_text,
    stable_hmac_identifier,
)
from tests.webhook_hmac_test_helpers import outbound_webhook_hmac_signer


class _CapturingConnection:
    def __init__(self) -> None:
        self.params: dict[str, Any] = {}

    def execute(self, _statement: Any, params: dict[str, Any]) -> None:
        self.params = dict(params)


class _CapturingEngine:
    def __init__(self) -> None:
        self.connection = _CapturingConnection()

    @contextmanager
    def begin(self) -> Iterator[_CapturingConnection]:
        yield self.connection


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("token", "tok_super_secret"),
        ("Authorization", "Bearer complete-credential"),
        ("set-cookie", "session=complete-cookie"),
        ("private_key", "-----BEGIN PRIVATE KEY-----complete-----END PRIVATE KEY-----"),
        ("WECOM_CALLBACK_AES_KEY", "complete-aes-key"),
    ],
)
def test_secret_fields_use_one_fixed_mask_without_prefix_or_suffix(key: str, value: str) -> None:
    redacted = redact_sensitive_data({key: value}, sensitive_keys={"WECOM_CALLBACK_AES_KEY"})

    assert redacted[key] == SECRET_MASK
    assert value not in json.dumps(redacted)
    assert value[:3] not in redacted[key]
    assert value[-3:] not in redacted[key]


def test_nested_sensitive_data_redacts_dict_list_and_tuple_without_mutating_input() -> None:
    payload = {
        "customers": [
            {
                "unionid": "unionid_complete_001",
                "external_userid": "wmCompleteExternal001",
                "mobile": "13912345678",
                "answers": ("medical answer", {"message_content": "private message"}),
            }
        ],
        "headers": {"authorization": "Bearer complete-token"},
    }

    redacted = redact_sensitive_data(payload)

    assert redacted == {
        "customers": [
            {
                "unionid": PII_MASK,
                "external_userid": PII_MASK,
                "mobile": PII_MASK,
                "answers": (PII_MASK, {"message_content": PII_MASK}),
            }
        ],
        "headers": {"authorization": SECRET_MASK},
    }
    assert payload["customers"][0]["mobile"] == "13912345678"


def test_sensitive_text_scrubs_credentials_and_pii_from_exception_messages() -> None:
    raw = (
        "dispatch failed Authorization=Bearer complete-token "
        "mobile=13912345678 external_userid=wmCompleteExternal001 "
        "-----BEGIN PRIVATE KEY-----complete-private-material-----END PRIVATE KEY-----"
    )

    redacted = redact_sensitive_text(raw)

    assert "complete-token" not in redacted
    assert "13912345678" not in redacted
    assert "wmCompleteExternal001" not in redacted
    assert "complete-private-material" not in redacted
    assert SECRET_MASK in redacted
    assert PII_MASK in redacted


def test_ai_audience_cli_report_uses_shared_secret_and_pii_redaction() -> None:
    report = json.loads(
        redact_report(
            {
                "sender_userid": "ownerRuntimeSentinel001",
                "mobile": "13987654321",
                "external_token": "runtime-sentinel-secret-001",
            }
        )
    )

    assert report == {
        "sender_userid": PII_MASK,
        "mobile": PII_MASK,
        "external_token": SECRET_MASK,
    }


def test_stable_hmac_identifier_supports_correlation_without_disclosing_source() -> None:
    first = stable_hmac_identifier("wmCompleteExternal001", secret="audit-secret", namespace="external_userid")
    second = stable_hmac_identifier("wmCompleteExternal001", secret="audit-secret", namespace="external_userid")
    other_namespace = stable_hmac_identifier("wmCompleteExternal001", secret="audit-secret", namespace="unionid")

    assert first == second
    assert first != other_namespace
    assert first.startswith("hmac-sha256:")
    assert "wmCompleteExternal001" not in first
    with pytest.raises(ValueError, match="HMAC secret is required"):
        stable_hmac_identifier("wmCompleteExternal001", secret="", namespace="external_userid")


def test_admin_audit_repository_redacts_sensitive_setting_rows_before_serialization() -> None:
    engine = _CapturingEngine()
    repository = AdminConfigRepository(engine=engine)  # type: ignore[arg-type]

    repository.insert_audit_log(
        operator="security-test",
        action_type="update",
        target_type="app_setting",
        target_id="WECOM_SECRET",
        before={"key": "WECOM_SECRET", "value": "complete-before-secret"},
        after={"key": "WECOM_SECRET", "value": "complete-after-secret", "mobile": "13912345678"},
    )

    serialized = json.dumps(engine.connection.params, ensure_ascii=False)
    assert "complete-before-secret" not in serialized
    assert "complete-after-secret" not in serialized
    assert "13912345678" not in serialized
    assert json.loads(engine.connection.params["before_json"])["value"] == SECRET_MASK
    assert json.loads(engine.connection.params["after_json"])["mobile"] == PII_MASK


def test_admin_secret_display_mask_never_reveals_prefix_or_suffix() -> None:
    assert mask_value("WECOM_SECRET", "super-secret-value") == SECRET_MASK
    assert mask_value("WECOM_CORP_ID", "ww-public") == "ww-public"


def test_diagnostic_summary_redacts_pii_but_preserves_safe_counts() -> None:
    summary = scrub_summary(
        {
            "external_userid": "wmCompleteExternal001",
            "external_userid_count": 3,
            "error": "mobile=13912345678 token=complete-token",
            "details": {"mobile": "13912345678"},
        }
    )

    assert summary == {
        "external_userid": PII_MASK,
        "external_userid_count": 3,
        "error": f"mobile={PII_MASK} token={SECRET_MASK}",
        "details": {"mobile": PII_MASK},
    }


def test_webhook_exception_diagnostic_never_contains_secret_or_pii(monkeypatch) -> None:
    def fail_post(*_args: Any, **_kwargs: Any) -> None:
        raise requests.ConnectionError(
            "Authorization=Bearer complete-token mobile=13912345678 external_userid=wmCompleteExternal001"
        )

    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES", WEBHOOK_GENERIC_PUSH)
    job = SimpleNamespace(
        id=1,
        idempotency_key="pytest-sensitive-webhook-event",
        effect_type=WEBHOOK_GENERIC_PUSH,
        execution_mode="execute",
        payload_json={"webhook_url": "https://hooks.example.test/redaction", "body": {"ok": True}},
        operation="post",
        target_type="webhook",
        target_id="wmCompleteExternal001",
    )

    result = WebhookAdapter(http_post=fail_post, signer=outbound_webhook_hmac_signer()).dispatch(job)

    assert result.error_code == "network_error"
    assert "complete-token" not in result.error_message
    assert "13912345678" not in result.error_message
    assert "wmCompleteExternal001" not in result.error_message
    assert SECRET_MASK in result.error_message
    assert PII_MASK in result.error_message
