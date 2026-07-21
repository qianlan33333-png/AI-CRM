from __future__ import annotations

from pathlib import Path

from scripts.ci.check_github_actions_expression_length import (
    GITHUB_HARD_FORMAT_LIMIT,
    REPOSITORY_FORMAT_LIMIT,
    check_workflows,
    simulate_template_reader_format,
    utf16_code_units,
)


ROOT = Path(__file__).resolve().parents[1]


def _write_workflow(root: Path, source: str) -> None:
    workflow_dir = root / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "test.yml").write_text(source, encoding="utf-8")


def test_repository_workflows_stay_below_expression_format_limit() -> None:
    assert REPOSITORY_FORMAT_LIMIT == 20_000
    assert GITHUB_HARD_FORMAT_LIMIT == 21_000
    assert check_workflows(ROOT) == []


def test_template_reader_format_escapes_braces_and_numbers_expressions() -> None:
    scalar = "prefix {literal} ${{ github.sha }} middle ${{ env.NAME }} suffix }"

    assert simulate_template_reader_format(scalar) == (
        "format('prefix {{literal}} {0} middle {1} suffix }}', github.sha, env.NAME)"
    )


def test_template_reader_format_doubles_single_quotes_before_counting() -> None:
    scalar = "echo 'release' ${{ github.sha }}"

    assert simulate_template_reader_format(scalar) == (
        "format('echo ''release'' {0}', github.sha)"
    )


def test_expression_scanner_ignores_closing_braces_inside_quoted_string() -> None:
    scalar = "echo ${{ format('}}', github.sha) }} done"

    assert simulate_template_reader_format(scalar) == (
        "format('echo {0} done', format('}}', github.sha))"
    )


def test_single_expression_uses_raw_expression_without_format_wrapper() -> None:
    payload = "'" + ("a" * 19_988) + "'"

    expression = simulate_template_reader_format("${{ " + payload + " }}")

    assert expression == payload
    assert utf16_code_units(expression) == 19_990
    assert utf16_code_units(expression) < REPOSITORY_FORMAT_LIMIT


def test_utf16_astral_characters_cannot_bypass_expression_limit(tmp_path: Path) -> None:
    astral_script = "😀" * 10_500 + "${{ github.sha }}"
    _write_workflow(
        tmp_path,
        "jobs:\n  deploy:\n    steps:\n      - run: |\n          "
        + astral_script
        + "\n",
    )

    violations = check_workflows(tmp_path)

    assert len(violations) == 1
    assert violations[0].expression_length > GITHUB_HARD_FORMAT_LIMIT


def test_single_quote_expansion_can_cross_repository_limit(tmp_path: Path) -> None:
    quote_heavy_script = "'" * (REPOSITORY_FORMAT_LIMIT // 2) + "${{ github.sha }}"
    _write_workflow(
        tmp_path,
        "jobs:\n  deploy:\n    steps:\n      - run: |\n          "
        + quote_heavy_script
        + "\n",
    )

    violations = check_workflows(tmp_path)

    assert len(violations) == 1
    assert violations[0].expression_length > REPOSITORY_FORMAT_LIMIT


def test_recursive_expression_scalar_over_repository_limit_is_rejected(tmp_path: Path) -> None:
    long_script = "x" * REPOSITORY_FORMAT_LIMIT + "${{ github.sha }}"
    _write_workflow(
        tmp_path,
        "jobs:\n  deploy:\n    steps:\n      - name: deploy\n        run: |\n          "
        + long_script
        + "\n",
    )

    violations = check_workflows(tmp_path)

    assert len(violations) == 1
    assert violations[0].scalar_path.endswith(".run")
    expected = simulate_template_reader_format(long_script + "\n")
    assert expected is not None
    assert violations[0].expression_length == utf16_code_units(expected)


def test_long_pure_literal_scalar_is_not_expressionized_or_rejected(tmp_path: Path) -> None:
    long_literal = "x" * (GITHUB_HARD_FORMAT_LIMIT + 1_000)
    _write_workflow(
        tmp_path,
        "jobs:\n  test:\n    steps:\n      - run: |\n          " + long_literal + "\n",
    )

    assert simulate_template_reader_format(long_literal) is None
    assert check_workflows(tmp_path) == []
