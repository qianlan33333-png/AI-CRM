from __future__ import annotations

from datetime import date
import json
import logging
from pathlib import Path

from aicrm_next.platform.shared.safe_logging import safe_log_exception
from scripts.ci.check_pii_logging import apply_allowlist, scan_file, scan_paths, validate_allowlist


ROOT = Path(__file__).resolve().parents[1]


def _source(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "sample.py"
    path.write_text(body, encoding="utf-8")
    return path


def test_scanner_rejects_sensitive_logger_print_and_exception_trace(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """
def unsafe(logger, request, mobile, payload):
    logger.info("mobile=%s", mobile)
    print(payload)
    logger.warning("request=%s", request.query_params)
    try:
        raise RuntimeError(mobile)
    except Exception:
        logger.exception("failed")
""",
    )

    findings = scan_file(path, root=tmp_path)

    assert {finding.rule for finding in findings} == {
        "exception_trace",
        "sensitive_log_argument",
        "sensitive_print_argument",
    }
    assert {finding.line for finding in findings if finding.rule == "sensitive_log_argument"} == {3, 5}


def test_scanner_accepts_redaction_hmac_types_and_counts(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        """
def safe(logger, mobile, payload, exc):
    logger.info("mobile=%s", redact_sensitive_text(mobile))
    logger.info("actor=%s", stable_hmac_identifier(mobile, secret=b"x"))
    logger.info("oauth_failed", extra=safe_log_fields(payload=payload))
    logger.info("payload_type=%s count=%s", type(payload).__name__, len(payload))
    logger.error("failed error_type=%s", type(exc).__name__)
""",
    )

    assert scan_file(path, root=tmp_path) == []


def test_scanner_does_not_trust_a_safe_prefix_without_a_redaction_call(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        "def unsafe(logger, mobile):\n    safe_mobile = mobile\n    logger.info('mobile=%s', safe_mobile)\n",
    )

    findings = scan_file(path, root=tmp_path)

    assert [(finding.rule, finding.line) for finding in findings] == [("sensitive_log_argument", 3)]


def test_scanner_only_trusts_explicitly_approved_redaction_helpers(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        "def unsafe(logger, token):\n    logger.info('token=%s', unmask_secret(token))\n",
    )

    findings = scan_file(path, root=tmp_path)

    assert [(finding.rule, finding.line) for finding in findings] == [("sensitive_log_argument", 2)]


def test_allowlist_requires_owner_reason_expiry_and_rejects_expired_or_unused_entries(tmp_path: Path) -> None:
    path = _source(tmp_path, "def unsafe(logger, mobile):\n    logger.info('mobile=%s', mobile)\n")
    finding = scan_file(path, root=tmp_path)[0]
    approved = {
        "path": finding.path,
        "function": finding.function,
        "rule": finding.rule,
        "owner": "security",
        "reason": "temporary migration diagnostic",
        "expires_at": "2026-08-01",
    }

    assert validate_allowlist([approved], today=date(2026, 7, 10)) == []
    remaining, unused = apply_allowlist([finding], [approved])
    assert remaining == []
    assert unused == []

    expired = {**approved, "expires_at": "2026-07-09"}
    assert "expired" in " ".join(validate_allowlist([expired], today=date(2026, 7, 10)))
    unmatched = {**approved, "function": "other"}
    assert apply_allowlist([finding], [unmatched])[1]


def test_runtime_exception_logging_never_emits_secret_or_pii(caplog) -> None:
    mobile = "13987654321"
    external_userid = "wmRuntimeSentinel001"
    secret = "runtime-sentinel-secret-001"
    out_trade_no = "RUNTIME-ORDER-SENTINEL-001"
    logger = logging.getLogger("tests.pii_runtime_capture")

    with caplog.at_level(logging.ERROR, logger=logger.name):
        for path_label in (
            "admin_config",
            "channel_entry",
            "questionnaire",
            "payment",
            "external_effect_worker",
            "migration_cli",
        ):
            safe_log_exception(
                logger,
                f"{path_label} failed",
                RuntimeError(
                    f"token={secret} mobile={mobile} external_userid={external_userid}"
                ),
                token=secret,
                mobile=mobile,
                external_userid=external_userid,
                out_trade_no=out_trade_no,
            )

    rendered = json.dumps(
        [record.__dict__ for record in caplog.records],
        ensure_ascii=False,
        default=str,
    )
    assert secret not in rendered
    assert mobile not in rendered
    assert external_userid not in rendered
    assert out_trade_no not in rendered
    assert rendered.count("RuntimeError") == 6
    assert "[redacted]" in rendered
    assert "[pii]" in rendered


def test_runtime_exception_logging_preserves_requested_severity(caplog) -> None:
    logger = logging.getLogger("tests.pii_runtime_severity")

    with caplog.at_level(logging.DEBUG, logger=logger.name):
        safe_log_exception(logger, "optional probe failed", RuntimeError("mobile=13987654321"), level=logging.DEBUG)

    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.DEBUG
    assert "13987654321" not in json.dumps(caplog.records[0].__dict__, default=str)


def test_repository_has_zero_unapproved_pii_logging_findings() -> None:
    findings = scan_paths([ROOT / "aicrm_next", ROOT / "scripts"], root=ROOT)

    assert findings == []
