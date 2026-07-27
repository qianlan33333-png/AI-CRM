from __future__ import annotations

from pathlib import Path

from tools import check_next_admin_ui_data_parity as parity


ROOT = Path(__file__).resolve().parents[1]


def test_admin_ui_data_parity_checker_does_not_require_removed_frontend_compat_facade() -> None:
    source = (ROOT / "tools/check_next_admin_ui_data_parity.py").read_text(encoding="utf-8")

    assert not (ROOT / "aicrm_next/app/admin_console/legacy_routes.py").exists()
    assert '"automation_program_legacy_facade"' not in source
    assert "frontend_compat_legacy_routes:should_be_removed" in source


def test_static_production_data_contract_check_no_longer_reports_removed_legacy_routes() -> None:
    _ok, blockers = parity._static_production_data_contracts_ready()

    assert all("legacy_routes.py" not in blocker for blocker in blockers)
    assert "frontend_compat_legacy_routes:should_be_removed" not in blockers
