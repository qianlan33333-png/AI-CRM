from __future__ import annotations

import subprocess
import sys
from pathlib import Path

try:
    from scripts.script_runtime import REPO_ROOT
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from script_runtime import REPO_ROOT  # type: ignore[no-redef]

ROOT = REPO_ROOT
PYTHON_TARGETS = [
    "aicrm_next",
    "scripts/run_lint.py",
    "scripts/run_typecheck.py",
    "scripts/script_runtime.py",
    "tools",
]
REPORT_ONLY_PYTHON_TARGETS = [
    "aicrm_next",
]
SCAN_ROOTS = [
    ROOT / "tests",
    ROOT / "scripts",
    ROOT / "tools",
]
REPORT_ONLY_SCAN_ROOTS = [
    ROOT / "aicrm_next",
]
TEXT_SUFFIXES = {".py", ".js", ".html", ".css", ".md", ".sql", ".toml"}
SKIP_DIR_NAMES = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", "test-results"}


def _iter_text_files(scan_roots: list[Path] | None = None) -> list[Path]:
    files: list[Path] = []
    for base in scan_roots or SCAN_ROOTS:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_dir():
                continue
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            if path.suffix.lower() in TEXT_SUFFIXES:
                files.append(path)
    return files


def _custom_text_checks(scan_roots: list[Path] | None = None) -> list[str]:
    issues: list[str] = []
    for path in _iter_text_files(scan_roots):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("<<<<<<<") or stripped.startswith("=======") or stripped.startswith(">>>>>>>"):
                issues.append(f"{path.relative_to(ROOT)}:{line_no}: contains merge marker")
            if line.rstrip("\r\n") != line.rstrip():
                issues.append(f"{path.relative_to(ROOT)}:{line_no}: trailing whitespace")
            if "\t" in line and path.suffix.lower() in {".py", ".js", ".html", ".css"}:
                issues.append(f"{path.relative_to(ROOT)}:{line_no}: tab character")
    return issues


def _run_ruff() -> int:
    ruff_path = ROOT / ".venv" / "bin" / "ruff"
    command = [str(ruff_path if ruff_path.exists() else "ruff"), "check", "--config", str(ROOT / "pyproject.toml"), *PYTHON_TARGETS]
    return subprocess.run(command, cwd=ROOT).returncode


def _run_ruff_report_only() -> int:
    ruff_path = ROOT / ".venv" / "bin" / "ruff"
    command = [
        str(ruff_path if ruff_path.exists() else "ruff"),
        "check",
        "--config",
        str(ROOT / "pyproject.toml"),
        *REPORT_ONLY_PYTHON_TARGETS,
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        print("report-only ruff findings for aicrm_next/ (non-blocking):")
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        if output:
            print(output.rstrip())
    return result.returncode


def _print_report_only_text_issues(issues: list[str]) -> None:
    if not issues:
        return
    print("report-only custom lint findings for aicrm_next/ (non-blocking):")
    for issue in issues:
        print(f"  - {issue}")


def main() -> int:
    text_issues = _custom_text_checks()
    if text_issues:
        print("custom lint failures:")
        for issue in text_issues:
            print(f"  - {issue}")
        return 1
    ruff_status = _run_ruff()
    _run_ruff_report_only()
    _print_report_only_text_issues(_custom_text_checks(REPORT_ONLY_SCAN_ROOTS))
    return ruff_status


if __name__ == "__main__":
    sys.exit(main())
