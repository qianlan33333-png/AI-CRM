#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.shared.sensitive_data import redact_sensitive_data, redact_sensitive_text  # noqa: E402


SOURCE_ROOT = ROOT / "aicrm_next"
RESOLVER = Path("aicrm_next/identity_contact/resolver.py")
CHANNEL_CRM_PORT = Path("aicrm_next/channel_entry/crm_port.py")
CHANNEL_CRM_COMPOSITION = Path("aicrm_next/channel_entry_composition.py")

HIGH_RISK_ALIAS_CONSUMERS = (
    Path("aicrm_next/ai_assist/external_campaigns_repo.py"),
    Path("aicrm_next/ai_audience_ops/repository.py"),
    Path("aicrm_next/automation_agents/repository.py"),
    Path("aicrm_next/automation_engine/group_ops/action_dispatcher.py"),
    Path("aicrm_next/channel_entry/identity_bridge_repo.py"),
    Path("aicrm_next/channel_entry/repo.py"),
    Path("aicrm_next/cloud_orchestrator/repository.py"),
    Path("aicrm_next/customer_read_model/repo.py"),
    Path("aicrm_next/customer_read_model/sidebar_v2.py"),
    Path("aicrm_next/customer_tags/local_projection.py"),
    Path("aicrm_next/hxc_dashboard/postgres_repo.py"),
    Path("aicrm_next/message_archive/repo.py"),
    Path("aicrm_next/public_product/h5_wechat_pay.py"),
    Path("aicrm_next/send_targets/repo.py"),
    Path("aicrm_next/service_period/repo.py"),
    Path("aicrm_next/sidebar_write/repo.py"),
)

CHANNEL_CRM_PORT_CONSUMERS = {
    Path("aicrm_next/channel_entry/identity_bridge_repo.py"),
    Path("aicrm_next/channel_entry/repo.py"),
}

CANONICAL_WRITE_OWNERS = {
    Path("aicrm_next/channel_entry/identity_bridge_repo.py"),
    Path("aicrm_next/identity_contact/oauth_projection_repo.py"),
    Path("aicrm_next/identity_contact/payment_projection.py"),
    Path("aicrm_next/identity_contact/repo.py"),
    Path("aicrm_next/identity_contact/write_repository.py"),
}

RAW_ALIAS_SQL = (
    re.compile(r"primary_external_userid\s*=[\s\S]{0,600}jsonb_exists\([^\n]*external_userids_json"),
    re.compile(r"primary_openid\s*=[\s\S]{0,600}jsonb_exists\([^\n]*openids_json"),
)


def _read(path: Path) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _canonical_write_files() -> set[Path]:
    write_pattern = re.compile(r"(?:INSERT\s+INTO|UPDATE)\s+crm_user_identity\b", re.IGNORECASE)
    found: set[Path] = set()
    for source_path in SOURCE_ROOT.rglob("*.py"):
        if write_pattern.search(source_path.read_text(encoding="utf-8")):
            found.add(source_path.relative_to(ROOT))
    return found


def _missing_unionid_succeeded_branches(path: Path) -> list[int]:
    source = _read(path)
    tree = ast.parse(source, filename=str(path))
    violations: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test_source = ast.get_source_segment(source, node.test) or ""
        if "missing_unionid" not in test_source:
            continue
        branch_source = "\n".join(ast.get_source_segment(source, item) or "" for item in node.body)
        if re.search(r"status\s*=\s*[\"']succeeded[\"']", branch_source):
            violations.append(node.lineno)
    return violations


def check() -> list[str]:
    errors: list[str] = []
    resolver_source = _read(RESOLVER)
    for required in (
        "class IdentityResolver(Protocol)",
        "class DBAPIIdentityResolver",
        "class SQLAlchemyIdentityResolver",
        "classify_identity_candidates",
        "mobile_normalized = input.mobile",
        "ORDER BY identity.unionid",
    ):
        if required not in resolver_source:
            errors.append(f"resolver contract missing token: {required}")
    if "LIMIT 1" in resolver_source:
        errors.append("central resolver must collect all canonical candidates; LIMIT 1 is forbidden")

    channel_crm_port_source = _read(CHANNEL_CRM_PORT)
    channel_crm_composition_source = _read(CHANNEL_CRM_COMPOSITION)
    for required in (
        "class ResolvePersonIdentityRequest(BaseModel)",
        "def resolve_identity_with_dbapi(",
        "_dependencies().resolve_identity_with_dbapi(",
        'raise RuntimeError("channel_crm_port_not_configured")',
    ):
        if required not in channel_crm_port_source:
            errors.append(f"channel CRM resolver port missing token: {required}")
    for required in (
        "resolve_external_userid_with_dbapi, resolve_identity_with_dbapi",
        "ChannelCrmDependencies(",
        "configure_channel_crm_port(",
    ):
        if required not in channel_crm_composition_source:
            errors.append(f"channel CRM resolver composition missing token: {required}")

    private_resolver_pattern = re.compile(r"^\s*def\s+_resolve_unionids?", re.MULTILINE)
    for source_path in SOURCE_ROOT.rglob("*.py"):
        relative = source_path.relative_to(ROOT)
        source = source_path.read_text(encoding="utf-8")
        if relative != RESOLVER and private_resolver_pattern.search(source):
            errors.append(f"private unionid resolver remains: {relative}")

    for relative in HIGH_RISK_ALIAS_CONSUMERS:
        source = _read(relative)
        imports_central_resolver = "identity_contact.resolver" in source
        imports_channel_crm_port = relative in CHANNEL_CRM_PORT_CONSUMERS and "from .crm_port import" in source
        if (
            not imports_central_resolver
            and not imports_channel_crm_port
            and relative != Path("aicrm_next/hxc_dashboard/postgres_repo.py")
        ):
            errors.append(f"high-risk identity consumer does not import central resolver: {relative}")
        for pattern in RAW_ALIAS_SQL:
            if pattern.search(source):
                errors.append(f"raw alias SQL remains outside central resolver: {relative}")
                break

    write_files = _canonical_write_files()
    unexpected_writers = sorted(write_files - CANONICAL_WRITE_OWNERS)
    if unexpected_writers:
        errors.append("unexpected crm_user_identity write owners: " + ", ".join(map(str, unexpected_writers)))

    postgres_binding_source = _read(Path("aicrm_next/identity_contact/repo.py")).split("class PostgresIdentityBindingRepository:", 1)[1]
    for forbidden in ("INSERT INTO people", "UPDATE people", "INSERT INTO external_contact_bindings", "UPDATE external_contact_bindings"):
        if forbidden in postgres_binding_source:
            errors.append(f"production identity binding still writes legacy canonical path: {forbidden}")

    consumer_path = Path("aicrm_next/service_period/payment_consumer.py")
    for line in _missing_unionid_succeeded_branches(consumer_path):
        errors.append(f"missing_unionid branch returns succeeded: {consumer_path}:{line}")
    consumer_source = _read(consumer_path)
    if 'status="failed_retryable"' not in consumer_source or 'error_code="missing_unionid"' not in consumer_source:
        errors.append("service period missing_unionid must be failed_retryable with an explicit error code")

    questionnaire_h5 = _read(Path("aicrm_next/questionnaire/h5_write.py"))
    if (
        '"error_code": "identity_pending_unionid" if not unionid else ""' not in questionnaire_h5
        or '"identity_pending": not bool(unionid and external_userid and follow_user_userid)' not in questionnaire_h5
    ):
        errors.append("questionnaire H5 must expose unresolved canonical identity as queued continuation state")
    questionnaire_consumer = _read(Path("aicrm_next/questionnaire/event_consumers.py"))
    if (
        'if not _text(submission.get("unionid"))' not in questionnaire_consumer
        or 'status="failed_retryable"' not in questionnaire_consumer
        or 'error_code="missing_unionid"' not in questionnaire_consumer
    ):
        errors.append("questionnaire webhook consumer must keep missing unionid retryable and unsent")
    if (
        "def questionnaire_tag_consumer(" not in questionnaire_consumer
        or 'error_code="identity_pending_unionid" if "unionid" in missing' not in questionnaire_consumer
        or 'target_type="unionid"' not in questionnaire_consumer
    ):
        errors.append("questionnaire tag consumer must require canonical unionid before planning an effect")

    payment_source = _read(Path("aicrm_next/public_product/h5_wechat_pay.py"))
    payment_resolver_source = payment_source.split("def _resolve_payment_identity(", 1)[1].split("\ndef _paid_order_for_product_identity(", 1)[0]
    if "external_userid" in payment_resolver_source or "mobile=" in payment_resolver_source:
        errors.append("payment identity resolver must not mix sidebar customer context into payer identity")
    payment_create_source = payment_source.split("def create_jsapi_order_response(", 1)[1].split("\ndef order_status_response(", 1)[0]
    required_payment_tokens = (
        "_resolve_payment_identity(conn, identity, for_update=True)",
        'order_identity["unionid"] = canonical_unionid',
        '"identity_resolution_required"',
    )
    for token in required_payment_tokens:
        if token not in payment_create_source:
            errors.append(f"payment identity fail-closed boundary missing token: {token}")
    resolver_position = payment_create_source.find("_resolve_payment_identity(conn, identity, for_update=True)")
    insert_position = payment_create_source.find("_insert_order(")
    provider_call_position = payment_create_source.find("client.create_jsapi_transaction(transaction_payload)")
    if min(resolver_position, insert_position, provider_call_position) < 0 or not (
        resolver_position < insert_position < provider_call_position
    ):
        errors.append(
            "payment identity resolution must happen before order insert, and the local order must exist before WeChat Pay call"
        )

    bridge_service = _read(Path("aicrm_next/channel_entry/identity_bridge_service.py"))
    if "corp_id_mismatch" not in bridge_service or "_single_corp_id" not in bridge_service:
        errors.append("identity bridge must reject request corp overrides before side effects")
    return errors


def main() -> int:
    errors = check()
    payload = {
        "ok": not errors,
        "error_count": len(errors),
        "private_resolver_count": 0 if not any("private unionid resolver" in error for error in errors) else None,
        "canonical_write_owner_count": len(_canonical_write_files()),
    }
    print(json.dumps(redact_sensitive_data(payload), ensure_ascii=False, sort_keys=True))
    for error in errors:
        print(redact_sensitive_text(f"ERROR: {error}"))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
