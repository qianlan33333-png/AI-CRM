from __future__ import annotations

import re
from pathlib import Path


RUNTIME_FILE_LINE_BUDGETS = {
    "aicrm_next/cloud_orchestrator/repository.py": 2469,
    "aicrm_next/admin_config/application.py": 2062,
    "aicrm_next/questionnaire/repo.py": 1882,
    "aicrm_next/ai_audience_ops/repository.py": 1859,
    "aicrm_next/automation_engine/group_ops/postgres_repo.py": 1726,
    "aicrm_next/commerce/api.py": 1549,
    "aicrm_next/automation_engine/group_ops/application.py": 1545,
    "aicrm_next/customer_read_model/repo.py": 1543,
    "aicrm_next/customer_read_model/application.py": 1506,
}

INLINE_SCRIPT_LINE_BUDGETS = {
    "aicrm_next/frontend_compat/templates/admin_user_ops.html": 926,
    "aicrm_next/frontend_compat/templates/questionnaire_h5_page.html": 798,
    "aicrm_next/frontend_compat/templates/admin_console/cloud_campaigns_workspace.html": 682,
    "aicrm_next/commerce/templates/wechat_products.html": 636,
    "aicrm_next/frontend_compat/templates/admin_console/owner_migration.html": 555,
    "aicrm_next/frontend_compat/templates/admin_console/image_library.html": 473,
    "aicrm_next/ai_audience_ops/templates/admin_console/ai_audience_package_detail.html": 447,
    "aicrm_next/automation_agents/templates/admin_console/automation_agent_edit.html": 390,
    "aicrm_next/frontend_compat/templates/admin_console/miniprogram_library.html": 379,
    "aicrm_next/questionnaire/templates/admin_console/questionnaires.html": 371,
    "aicrm_next/frontend_compat/templates/admin_console/hxc_dashboard.html": 359,
    "aicrm_next/frontend_compat/templates/admin_console/config_push_capabilities.html": 306,
}

TEMPLATE_TOTAL_LINE_LIMIT = 3000


def test_runtime_files_over_1500_lines_do_not_grow_without_decomposition() -> None:
    overruns: list[str] = []
    for path, budget in RUNTIME_FILE_LINE_BUDGETS.items():
        count = len(Path(path).read_text(encoding="utf-8").splitlines())
        if count > budget:
            overruns.append(f"{path}: {count} > {budget}")

    assert overruns == []


def test_inline_script_debt_does_not_grow_without_static_js_migration() -> None:
    overruns: list[str] = []
    for path, budget in INLINE_SCRIPT_LINE_BUDGETS.items():
        count = _inline_script_line_count(Path(path).read_text(encoding="utf-8"))
        if count > budget:
            overruns.append(f"{path}: {count} > {budget}")

    assert overruns == []


def test_large_template_debt_stays_below_r13_limit() -> None:
    overruns: list[str] = []
    for path in sorted(Path("aicrm_next").rglob("*.html")):
        if "templates" not in path.parts:
            continue
        count = len(path.read_text(encoding="utf-8").splitlines())
        if count > TEMPLATE_TOTAL_LINE_LIMIT:
            overruns.append(f"{path}: {count} > {TEMPLATE_TOTAL_LINE_LIMIT}")

    assert overruns == []


def _inline_script_line_count(html: str) -> int:
    count = 0
    for match in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.I | re.S):
        body = match.group(1).strip("\n")
        if body.strip():
            count += len(body.splitlines())
    return count
