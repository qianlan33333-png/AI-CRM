from __future__ import annotations

from tools.report_refactor_line_growth import NumstatRow, categorize_path, parse_numstat, render_report, summarize_rows


def test_refactor_line_growth_categories_match_slimming_plan() -> None:
    assert categorize_path("migrations/versions/0084_id_dev_p1_baseline_tables.py") == "migrations"
    assert categorize_path("tests/test_external_effects_mvp.py") == "tests"
    assert categorize_path("docs/queue/internal-event-questionnaire-submitted.md") == "docs"
    assert categorize_path("tools/check_sql_static_guard.py") == "tools/guards"
    assert categorize_path("aicrm_next/extensions/forms/questionnaire/api.py") == "runtime APIs"
    assert categorize_path("aicrm_next/extensions/forms/questionnaire/templates/admin_console/questionnaires.html") == "templates/pages"
    assert categorize_path("docs/architecture/route_ownership_manifest.yml") == "manifests/yml"


def test_refactor_line_growth_numstat_summary() -> None:
    rows = parse_numstat(
        "\n".join(
            [
                "20\t5\tmigrations/versions/0084_id_dev_p1_baseline_tables.py",
                "12\t2\ttests/test_external_effects_mvp.py",
                "9\t0\taicrm_next/extensions/forms/questionnaire/api.py",
                "-\t-\tassets/binary.png",
            ]
        )
    )

    assert rows == [
        NumstatRow(added=20, deleted=5, path="migrations/versions/0084_id_dev_p1_baseline_tables.py"),
        NumstatRow(added=12, deleted=2, path="tests/test_external_effects_mvp.py"),
        NumstatRow(added=9, deleted=0, path="aicrm_next/extensions/forms/questionnaire/api.py"),
    ]

    summary = summarize_rows(rows)

    assert summary["migrations"] == {"added": 20, "deleted": 5, "net": 15, "files": 1}
    assert summary["tests"] == {"added": 12, "deleted": 2, "net": 10, "files": 1}
    assert summary["runtime APIs"] == {"added": 9, "deleted": 0, "net": 9, "files": 1}


def test_refactor_line_growth_render_report() -> None:
    report = {
        "base": "base-sha",
        "target": "HEAD",
        "totals": {"added": 10, "deleted": 3, "net": 7, "files": 2},
        "categories": {
            "tests": {"added": 6, "deleted": 1, "net": 5, "files": 1},
            "docs": {"added": 4, "deleted": 2, "net": 2, "files": 1},
        },
    }

    rendered = render_report(report)

    assert "Refactor Line Growth Report" in rendered
    assert "Base: `base-sha`" in rendered
    assert "| `tests` | 6 | 1 | 5 | 1 |" in rendered
