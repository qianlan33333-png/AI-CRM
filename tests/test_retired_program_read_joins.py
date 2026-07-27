from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_customer_sidebar_no_longer_reads_automation_program_for_workflow_title() -> None:
    source = _read("aicrm_next/customer_read_model/sidebar_v2.py")

    assert "JOIN automation_program" not in source
    assert "p.program_name" not in source
    assert 'sidebar_context.get("program_name")' not in source


def test_commerce_lead_channel_choices_do_not_join_or_display_old_programs() -> None:
    repo_source = _read("aicrm_next/extensions/commerce/commerce/repo.py")
    template_source = _read("aicrm_next/extensions/commerce/commerce/templates/wechat_products.html")

    assert "JOIN automation_program" not in repo_source
    assert "p.program_name" not in repo_source
    assert "c.program_id" not in repo_source
    assert "program_name" not in template_source
    assert "channel.program_name" not in template_source


def test_public_product_lead_qr_resolves_only_explicit_channel_binding() -> None:
    source = _read("aicrm_next/extensions/commerce/public_product/h5_wechat_pay.py")

    assert "WHERE c.program_id" not in source
    assert "lead_program_id" not in source
    assert "program_id:" not in source
