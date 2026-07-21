from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci.audit_test_inventory import audit_test_inventory, render_markdown


pytestmark = pytest.mark.unit


def _write(root: Path, relative_path: str, source: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_inventory_finds_duplicate_definitions_bodies_slow_and_stale_files(tmp_path: Path) -> None:
    duplicate_body = """\
def {name}():
    payload = {{"value": 3}}
    result = payload["value"] * 2
    assert result == 6
    assert payload == {{"value": 3}}
"""
    _write(
        tmp_path,
        "tests/test_alpha.py",
        duplicate_body.format(name="test_same_behavior") + "\n" + duplicate_body.format(name="test_same_behavior"),
    )
    _write(tmp_path, "tests/test_beta.py", duplicate_body.format(name="test_equivalent_behavior"))
    _write(tmp_path, "tests/test_new.py", "def test_new():\n    assert True\n")
    baseline = {
        "version": 1,
        "source_run_id": 123,
        "source_sha": "a" * 40,
        "total_items": 3,
        "total_duration_seconds": 42.0,
        "files": {
            "tests/test_alpha.py": {"duration_seconds": 40.0, "items": 2},
            "tests/test_retired.py": {"duration_seconds": 2.0, "items": 1},
        },
    }

    report = audit_test_inventory(
        tmp_path,
        duration_baseline=baseline,
        slow_file_seconds=30.0,
        oversized_file_lines=8,
        minimum_duplicate_body_nodes=8,
    )

    assert report["test_file_count"] == 3
    assert report["test_function_count"] == 4
    assert report["duplicate_test_definitions"] == [
        {
            "path": "tests/test_alpha.py",
            "qualified_name": "test_same_behavior",
            "lines": [1, 7],
        }
    ]
    assert len(report["exact_duplicate_body_candidates"]) == 1
    assert len(report["exact_duplicate_body_candidates"][0]["locations"]) == 3
    assert report["slow_test_files"] == [{"path": "tests/test_alpha.py", "duration_seconds": 40.0, "items": 2}]
    assert report["duration_baseline"]["missing_current_files"] == [
        "tests/test_beta.py",
        "tests/test_new.py",
    ]
    assert report["duration_baseline"]["retired_files"] == ["tests/test_retired.py"]
    assert "Observation only" in render_markdown(report)


def test_inventory_rejects_invalid_thresholds(tmp_path: Path) -> None:
    _write(tmp_path, "tests/test_one.py", "def test_one():\n    assert True\n")
    baseline = {
        "version": 1,
        "files": {"tests/test_one.py": {"duration_seconds": 1.0, "items": 1}},
    }

    with pytest.raises(ValueError, match="thresholds"):
        audit_test_inventory(tmp_path, duration_baseline=baseline, slow_file_seconds=-1)
