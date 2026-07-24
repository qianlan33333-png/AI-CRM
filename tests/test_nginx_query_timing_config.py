from __future__ import annotations

from pathlib import Path

from scripts.ops.check_nginx_query_timing_config import analyze_nginx_config


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "deploy" / "nginx-query-timing.conf.example"


def test_nginx_query_timing_template_is_privacy_safe_and_complete() -> None:
    report = analyze_nginx_config(TEMPLATE.read_text(encoding="utf-8"))

    assert report["log_format_found"] is True
    assert report["required_variables_present"] is True
    assert report["forbidden_variables"] == []
    assert report["access_log_enabled"] is False
    assert report["ready"] is False


def test_nginx_query_timing_checker_accepts_reviewed_activation() -> None:
    content = (
        TEMPLATE.read_text(encoding="utf-8")
        + "\naccess_log /operator/selected/query-timing.log aicrm_query_timing;\n"
    )

    report = analyze_nginx_config(content)

    assert report["access_log_enabled"] is True
    assert report["ready"] is True


def test_nginx_query_timing_checker_rejects_identifying_request_fields() -> None:
    content = """
    log_format aicrm_query_timing escape=json
        '{"request":"$request","uri":"$request_uri","ip":"$remote_addr"}';
    access_log /operator/selected/query-timing.log aicrm_query_timing;
    """

    report = analyze_nginx_config(content)

    assert report["forbidden_variables"] == [
        "remote_addr",
        "request",
        "request_uri",
    ]
    assert report["ready"] is False


def test_nginx_query_timing_checker_ignores_commented_activation() -> None:
    content = (
        TEMPLATE.read_text(encoding="utf-8")
        + "\n# access_log /operator/selected/query-timing.log aicrm_query_timing;\n"
    )

    report = analyze_nginx_config(content)

    assert report["access_log_enabled"] is False
    assert report["ready"] is False


def test_nginx_query_timing_checker_does_not_treat_hash_inside_quotes_as_comment() -> None:
    content = """
    log_format aicrm_query_timing escape=json
        '{"required":"$request_id $request_method $request_time $status $time_iso8601 '
        '$upstream_connect_time $upstream_response_time $upstream_status",'
        '"unsafe":"# $request_uri"}';
    access_log /operator/selected/query-timing.log aicrm_query_timing;
    """

    report = analyze_nginx_config(content)

    assert report["forbidden_variables"] == ["request_uri"]
    assert report["ready"] is False


def test_nginx_query_timing_checker_does_not_stop_at_semicolon_inside_quotes() -> None:
    content = """
    log_format aicrm_query_timing escape=json
        '{"required":"$request_id $request_method $request_time $status $time_iso8601 '
        '$upstream_connect_time $upstream_response_time $upstream_status",'
        '"unsafe":"marker; $remote_addr"}';
    access_log /operator/selected/query-timing.log aicrm_query_timing;
    """

    report = analyze_nginx_config(content)

    assert report["forbidden_variables"] == ["remote_addr"]
    assert report["ready"] is False
